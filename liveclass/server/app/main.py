# -*- coding: utf-8 -*-
"""LiveClass — بک‌اند FastAPI: لاگین، کلاس‌ها، جلسات LiveKit، حضور، ایمپورت CSV، برند."""
from __future__ import annotations

import csv
import io
import json
import os
import secrets
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request, Response, HTTPException, Form, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

import zipfile
import sqlite3
import tempfile
from datetime import datetime as _dt

from . import db as dbmod
from . import brand as brandmod
from . import importer, livekit_hub
from . import pdf_store
from .security import hash_password, verify_password, encode_session, decode_session

COOKIE = "lc_session"
SESSION_COOKIE_MAX_AGE = 12 * 3600


def _secure_cookies() -> bool:
    # با اجرای auto-ssl.sh روی سرور، این مقدار در liveclass.env برابر 1 می‌شود
    return os.environ.get("LIVECLASS_SECURE_COOKIES") == "1"

HEARTBEAT_GRACE_SECONDS = 75
# سقف ناشران (دوربین/میکروفون) برای هر جلسه — فراتر از آن دانش‌آموزها فقط تماشا می‌کنند (بهینه برای سرور ضعیف)
try:
    MAX_PUBLISHERS = int(os.environ.get("LIVECLASS_MAX_PUBLISHERS", "8"))
except ValueError:
    MAX_PUBLISHERS = 8
MAX_BOARD_OPS = 5000          # سقف تعداد عملیات روی تخته برای هر جلسه
MAX_BOARD_OP_BYTES = 49152    # سقف اندازه‌ی یک عملیات تخته (۴۸KB)


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def _has_json_body(request: Request) -> bool:
    ctype = request.headers.get("content-type", "")
    return "application/json" in ctype and (request.headers.get("content-length") not in (None, "0"))


def seconds_between(a: str | None, b: str | None) -> int:
    try:
        ta = datetime.strptime(a or "", "%Y-%m-%d %H:%M:%S")
        tb = datetime.strptime(b or "", "%Y-%m-%d %H:%M:%S")
        return max(0, int((tb - ta).total_seconds()))
    except Exception:
        return 0


def effective_leave(row, reference: str) -> str:
    """تا کی حضور دانش‌آموز معتبر است؟ heartbeat تازه → تا لحظه‌ی مرجع؛ کهنه → تا آخرین heartbeat."""
    heartbeat = row["last_heartbeat"] or row["joined_at"]
    if seconds_between(heartbeat, reference) <= HEARTBEAT_GRACE_SECONDS:
        return reference
    return heartbeat if heartbeat > row["joined_at"] else row["joined_at"]


def create_app() -> FastAPI:
    dbmod.init_db()
    app = FastAPI(title="LiveClass", version="0.2.0")

    # فشرده‌سازی Gzip برای پاسخ‌های متنی (HTML/JSON/CSV) — کاهش محسوس مصرف پهنای باند
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def security_and_cache_headers(request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Content-Security-Policy",
                     "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                     "img-src 'self' data: blob:; font-src 'self' data:; "
                     "connect-src 'self' ws: wss: http: https:; media-src 'self' blob: mediastream:; "
                     "worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
        path = request.url.path
        if path.startswith("/assets/"):
            # فایل‌های build وی با هش نام‌گذاری شده‌اند → بی‌نهایت قابل کش
            h.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return resp

    # ---------- ابزارها ----------
    def current_user(request: Request) -> dict | None:
        data = decode_session(request.cookies.get(COOKIE))
        if not data:
            return None
        with dbmod.connect() as conn:
            row = conn.execute("SELECT id, username, full_name, role FROM users WHERE id=?", (data["uid"],)).fetchone()
        return dict(row) if row else None

    def require_user(request: Request) -> dict:
        user = current_user(request)
        if not user:
            raise HTTPException(401, "وارد نشده‌اید")
        return user

    def require_admin(request: Request) -> dict:
        user = require_user(request)
        if user["role"] != "admin":
            raise HTTPException(403, "فقط ادمین")
        return user

    def class_or_404(conn, class_id: int):
        row = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not row:
            raise HTTPException(404, "کلاس پیدا نشد")
        return row

    def require_class_teacher(request: Request, conn, class_row) -> dict:
        """ادمین، مالک کلاس، یا کاربری با «دسترسی دبیر» به این کلاس."""
        user = require_user(request)
        if user["role"] == "admin" or class_row["teacher_id"] == user["id"]:
            return user
        grant = conn.execute("SELECT 1 FROM class_access WHERE class_id=? AND user_id=?",
                             (class_row["id"], user["id"])).fetchone()
        if grant:
            return user
        raise HTTPException(403, "این کلاس متعلق به شما نیست")

    def require_class_owner(request: Request, conn, class_row) -> dict:
        """فقط مالک کلاس یا ادمین — برای منوی دسترسی‌های کلاس."""
        user = require_user(request)
        if user["role"] != "admin" and class_row["teacher_id"] != user["id"]:
            raise HTTPException(403, "فقط مالک کلاس یا ادمین می‌تواند دسترسی‌ها را مدیریت کند")
        return user

    def open_session_for_class(conn, class_id: int):
        return conn.execute(
            "SELECT * FROM sessions WHERE class_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (class_id,)).fetchone()

    def load_attendance(conn, session_id: int):
        rows = conn.execute("""
            SELECT a.student_id, s.student_code, s.full_name,
                   a.joined_at, a.left_at, a.last_heartbeat, a.seconds
            FROM attendance a JOIN students s ON s.id = a.student_id
            WHERE a.session_id=? ORDER BY a.joined_at""", (session_id,)).fetchall()
        out = []
        reference = now_str()
        for r in rows:
            if r["left_at"]:
                secs = r["seconds"]
                online = False
            else:
                eff = effective_leave(r, reference)
                secs = seconds_between(r["joined_at"], eff)
                # «آنلاین» یعنی heartbeat تازه دارد (حضورش تا همین لحظه معتبر است)
                online = seconds_between((r["last_heartbeat"] or r["joined_at"]), reference) <= HEARTBEAT_GRACE_SECONDS
            out.append({
                "student_id": r["student_id"], "student_code": r["student_code"],
                "full_name": r["full_name"], "joined_at": r["joined_at"],
                "left_at": r["left_at"], "last_heartbeat": r["last_heartbeat"],
                "seconds": secs, "online": online,
            })
        return out

    def finalize_session(conn, session_row) -> None:
        now = now_str()
        rows = conn.execute("SELECT * FROM attendance WHERE session_id=? AND left_at IS NULL",
                            (session_row["id"],)).fetchall()
        for r in rows:
            eff = effective_leave(r, now)
            secs = seconds_between(r["joined_at"], eff)
            conn.execute("UPDATE attendance SET left_at=?, seconds=? WHERE session_id=? AND student_id=?",
                         (eff, secs, session_row["id"], r["student_id"]))
        conn.execute("UPDATE sessions SET status='closed', closed_at=? WHERE id=?", (now, session_row["id"]))

    # ---------- تخته (اشتراکی) ----------
    def board_auth(conn, request: Request, session_id: int, body: dict):
        """مجوز تخته: کوکی دبیر/ادمین صاحب کلاس، یا توکن ورود دانش‌آموز همان کلاس."""
        session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session_row:
            raise HTTPException(404, "جلسه پیدا نشد")
        user = current_user(request)
        if user:
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
            return session_row, {"name": user["full_name"], "is_teacher": True}
        token = (body.get("token") or "").strip()
        entry = resolve_student_entry(conn, token) if token else None
        if not entry or entry["class_id"] != session_row["class_id"]:
            raise HTTPException(403, "دسترسی به تخته ندارید")
        return session_row, {"name": entry["full_name"], "is_teacher": False}

    def load_board_ops(conn, session_id: int) -> list[dict]:
        rows = conn.execute(
            "SELECT seq, op_id, author, op, created_at FROM board_ops WHERE session_id=? ORDER BY seq",
            (session_id,)).fetchall()
        out = []
        for r in rows:
            try:
                op = json.loads(r["op"])
            except Exception:
                continue
            out.append({"seq": r["seq"], "id": r["op_id"], "author": r["author"],
                        "created_at": r["created_at"], **op})
        return out

    # ---------- auth ----------
    @app.post("/api/auth/login")
    async def login(request: Request, response: Response):
        body = await request.json()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        with dbmod.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HTTPException(401, "نام کاربری یا رمز اشتباه است")
        token = encode_session(row["id"], row["role"])
        resp = JSONResponse({"user": {"id": row["id"], "username": row["username"],
                                       "full_name": row["full_name"], "role": row["role"]}})
        resp.set_cookie(COOKIE, token, max_age=SESSION_COOKIE_MAX_AGE, httponly=True,
                        samesite="lax", secure=_secure_cookies())
        return resp

    @app.post("/api/auth/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, secure=_secure_cookies())
        return resp

    @app.get("/api/me")
    async def me(request: Request):
        user = current_user(request)
        if not user:
            raise HTTPException(401, "وارد نشده‌اید")
        return {"user": user}

    # ---------- تنظیمات عمومی / برند ----------
    @app.get("/api/config")
    async def config():
        return {"livekit_url": livekit_hub.public_url(),
                "brand": brandmod.load_settings()}

    @app.get("/api/brand")
    async def brand_public():
        return brandmod.load_settings()

    @app.put("/api/admin/brand")
    async def brand_save(request: Request,
                         site_name: str = Form(...),
                         site_name_short: str = Form(""),
                         tagline: str = Form(""),
                         primary_color: str = Form("#2563eb"),
                         logo: UploadFile | None = File(None),
                         icon: UploadFile | None = File(None)):
        require_admin(request)
        logo_bytes = await logo.read() if logo else None
        icon_bytes = await icon.read() if icon else None
        try:
            brand = brandmod.save_brand(site_name=site_name, site_name_short=site_name_short,
                                        tagline=tagline, primary_color=primary_color,
                                        logo=logo_bytes or None, icon=icon_bytes or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "brand": brand}

    @app.get("/api/brand/logo")
    async def brand_logo():
        path = brandmod.brand_file("logo")
        if not path:
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/brand/icon")
    async def brand_icon():
        path = brandmod.brand_file("icon")
        if not path:
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    # ---------- کلاس‌ها ----------
    @app.get("/api/my/classes")
    async def my_classes(request: Request):
        user = require_user(request)
        with dbmod.connect() as conn:
            if user["role"] == "admin":
                rows = conn.execute("""SELECT c.*, u.full_name AS teacher_name FROM classes c
                                       JOIN users u ON u.id=c.teacher_id ORDER BY c.id DESC""").fetchall()
            else:
                rows = conn.execute("""
                    SELECT DISTINCT c.*, u.full_name AS teacher_name,
                           (c.teacher_id != ?) AS guest_access
                    FROM classes c JOIN users u ON u.id=c.teacher_id
                    LEFT JOIN class_access ca ON ca.class_id=c.id AND ca.user_id=?
                    WHERE c.teacher_id=? OR ca.user_id IS NOT NULL
                    ORDER BY c.id DESC""",
                    (user["id"], user["id"], user["id"])).fetchall()
        return {"classes": [dict(r) for r in rows]}

    @app.post("/api/classes")
    async def create_class(request: Request):
        user = require_user(request)
        if user["role"] not in ("admin", "teacher"):
            raise HTTPException(403, "کاربر مهمان نمی‌تواند کلاس بسازد")
        body = await request.json()
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "عنوان کلاس الزامی است")
        teacher_id = int(body.get("teacher_id") or user["id"])
        if user["role"] != "admin":
            teacher_id = user["id"]
        with dbmod.connect() as conn:
            teacher = conn.execute("SELECT id FROM users WHERE id=?", (teacher_id,)).fetchone()
            if not teacher:
                raise HTTPException(400, "دبیر پیدا نشد")
            try:
                cur = conn.execute("INSERT INTO classes(title, teacher_id) VALUES (?,?)", (title, teacher_id))
            except Exception:
                raise HTTPException(409, "کلاسی با این عنوان وجود دارد")
            conn.commit()
            row = conn.execute("SELECT * FROM classes WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"class": dict(row)}

    @app.get("/api/teachers")
    async def teachers(request: Request):
        require_admin(request)
        with dbmod.connect() as conn:
            rows = conn.execute("SELECT id, username, full_name FROM users WHERE role IN ('teacher','admin') ORDER BY full_name").fetchall()
        return {"teachers": [dict(r) for r in rows]}

    @app.get("/api/classes/{class_id}")
    async def class_detail(request: Request, class_id: int):
        user = require_user(request)
        with dbmod.connect() as conn:
            cls = class_or_404(conn, class_id)
            require_class_teacher(request, conn, cls)
            students = conn.execute("""
                SELECT s.id, s.student_code, s.full_name, e.token
                FROM students s JOIN enrollments en ON en.student_id=s.id
                LEFT JOIN entry_tokens e ON e.class_id=en.class_id AND e.student_id=s.id
                WHERE en.class_id=? ORDER BY s.full_name""", (class_id,)).fetchall()
            sessions = conn.execute("SELECT * FROM sessions WHERE class_id=? ORDER BY id DESC LIMIT 10", (class_id,)).fetchall()
        return {"class": dict(cls), "students": [dict(s) for s in students],
                "sessions": [dict(s) for s in sessions]}

    @app.post("/api/classes/{class_id}/students")
    async def add_student(request: Request, class_id: int):
        body = await request.json()
        user = require_user(request)
        code = (body.get("student_code") or "").strip()
        name = (body.get("full_name") or "").strip()
        if not code or not name:
            raise HTTPException(400, "کد و نام دانش‌آموز الزامی است")
        with dbmod.connect() as conn:
            cls = class_or_404(conn, class_id)
            require_class_teacher(request, conn, cls)
            student = conn.execute("SELECT id FROM students WHERE student_code=?", (code,)).fetchone()
            if not student:
                cur = conn.execute("INSERT INTO students(student_code, full_name) VALUES (?,?)", (code, name))
                student_id = cur.lastrowid
            else:
                student_id = student["id"]
            conn.execute("INSERT OR IGNORE INTO enrollments(class_id, student_id) VALUES (?,?)", (class_id, student_id))
            token = dbmod.new_token()
            conn.execute("INSERT OR IGNORE INTO entry_tokens(class_id, student_id, token) VALUES (?,?,?)",
                         (class_id, student_id, token))
            conn.commit()
        return {"ok": True}

    @app.delete("/api/classes/{class_id}/students/{student_id}")
    async def remove_student(request: Request, class_id: int, student_id: int):
        with dbmod.connect() as conn:
            cls = class_or_404(conn, class_id)
            require_class_teacher(request, conn, cls)
            conn.execute("DELETE FROM enrollments WHERE class_id=? AND student_id=?", (class_id, student_id))
            conn.execute("DELETE FROM entry_tokens WHERE class_id=? AND student_id=?", (class_id, student_id))
            conn.commit()
        return {"ok": True}

    @app.get("/api/classes/{class_id}/entry-tokens")
    async def ensure_tokens(request: Request, class_id: int):
        with dbmod.connect() as conn:
            cls = class_or_404(conn, class_id)
            require_class_teacher(request, conn, cls)
            students = conn.execute("""SELECT s.id FROM students s JOIN enrollments e ON e.student_id=s.id
                                       WHERE e.class_id=?""", (class_id,)).fetchall()
            for s in students:
                row = conn.execute("SELECT token FROM entry_tokens WHERE class_id=? AND student_id=?",
                                   (class_id, s["id"])).fetchone()
                if not row:
                    conn.execute("INSERT INTO entry_tokens(class_id, student_id, token) VALUES (?,?,?)",
                                 (class_id, s["id"], dbmod.new_token()))
            conn.commit()
        return {"ok": True}

    # ---------- جلسات (شروع/پایان) ----------
    @app.post("/api/classes/{class_id}/start")
    async def start_session(request: Request, class_id: int):
        with dbmod.connect() as conn:
            cls = class_or_404(conn, class_id)
            user = require_class_teacher(request, conn, cls)
            existing = open_session_for_class(conn, class_id)
            if existing:
                return {"session": dict(existing), "already_open": True}
            room_name = f"lc-{class_id}-{secrets.token_hex(4)}"
            try:
                await livekit_hub.create_room(room_name)
            except Exception as exc:
                raise HTTPException(502, f"ساخت اتاق در LiveKit ناموفق بود: {exc}")
            now = now_str()
            cur = conn.execute("INSERT INTO sessions(class_id, room_name, title, status, started_at) VALUES (?,?,?, 'open', ?)",
                               (class_id, room_name, cls["title"], now))
            conn.commit()
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"session": dict(session_row)}  # board_open داخلش هست

    @app.post("/api/sessions/{session_id}/end")
    async def end_session(request: Request, session_id: int):
        with dbmod.connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row:
                raise HTTPException(404, "جلسه پیدا نشد")
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
            if session_row["status"] == "open":
                await livekit_hub.delete_room(session_row["room_name"])
                finalize_session(conn, session_row)
                conn.commit()
                session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return {"ok": True, "session": dict(session_row)}

    @app.get("/api/sessions/{session_id}/token")
    async def teacher_token(request: Request, session_id: int):
        user = require_user(request)
        with dbmod.connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row or session_row["status"] != "open":
                raise HTTPException(404, "جلسه باز پیدا نشد")
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
        token = livekit_hub.mint_token(session_row["room_name"],
                                        f"teacher-{user['id']}", user["full_name"],
                                        can_publish=True)
        return {"livekit_url": livekit_hub.public_url(), "token": token,
                "room": session_row["room_name"], "session_id": session_id,
                "class_id": session_row["class_id"], "can_publish": True}

    @app.get("/api/sessions/{session_id}/attendance")
    async def attendance(request: Request, session_id: int):
        with dbmod.connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row:
                raise HTTPException(404, "جلسه پیدا نشد")
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
            rows = load_attendance(conn, session_id)
        return {"session": dict(session_row), "attendance": rows}

    @app.get("/api/sessions/{session_id}/attendance.csv")
    async def attendance_csv(request: Request, session_id: int):
        with dbmod.connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row:
                raise HTTPException(404, "جلسه پیدا نشد")
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
            rows = load_attendance(conn, session_id)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["کد دانش‌آموز", "نام", "ورود", "خروج/آخرین حضور", "مدت حضور (ثانیه)"])
        for r in rows:
            writer.writerow([r["student_code"], r["full_name"], r["joined_at"],
                             r["left_at"] or r["last_heartbeat"] or "", r["seconds"]])
        return Response(out.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.csv"'})

    @app.get("/api/sessions/{session_id}/participants")
    async def participants(request: Request, session_id: int):
        with dbmod.connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row:
                raise HTTPException(404, "جلسه پیدا نشد")
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
        parts = await livekit_hub.list_participants(session_row["room_name"])
        return {"participants": parts}

    @app.post("/api/sessions/{session_id}/kick")
    async def kick(request: Request, session_id: int):
        body = await request.json()
        identity = (body.get("identity") or "").strip()
        if not identity:
            raise HTTPException(400, "identity الزامی است")
        with dbmod.connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row:
                raise HTTPException(404, "جلسه پیدا نشد")
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
        await livekit_hub.remove_participant(session_row["room_name"], identity)
        return {"ok": True}

    # ---------- دسترسی دبیر مهمان به کلاس ----------
    @app.get("/api/classes/{class_id}/access")
    async def list_access(request: Request, class_id: int):
        with dbmod.connect() as conn:
            cls = class_or_404(conn, class_id)
            require_class_owner(request, conn, cls)
            owner = conn.execute("SELECT id, username, full_name, role FROM users WHERE id=?",
                                 (cls["teacher_id"],)).fetchone()
            rows = conn.execute("""
                SELECT u.id, u.username, u.full_name, u.role, ca.created_at
                FROM class_access ca JOIN users u ON u.id=ca.user_id
                WHERE ca.class_id=? ORDER BY ca.created_at""", (class_id,)).fetchall()
        return {"owner": dict(owner), "grants": [dict(r) for r in rows]}

    @app.post("/api/classes/{class_id}/access")
    async def grant_access(request: Request, class_id: int):
        body = await request.json()
        username = (body.get("username") or "").strip()
        if not username:
            raise HTTPException(400, "نام کاربری الزامی است")
        with dbmod.connect() as conn:
            cls = class_or_404(conn, class_id)
            require_class_owner(request, conn, cls)
            target = conn.execute("SELECT id, username, full_name, role FROM users WHERE username=?",
                                  (username,)).fetchone()
            if not target:
                raise HTTPException(404, f"کاربری با نام {username} وجود ندارد")
            if target["id"] == cls["teacher_id"]:
                raise HTTPException(400, "این کاربر مالک کلاس است")
            if target["role"] == "admin":
                raise HTTPException(400, "ادمین به همه‌ی کلاس‌ها دسترسی دارد")
            try:
                conn.execute("INSERT INTO class_access(class_id, user_id) VALUES (?,?)",
                             (class_id, target["id"]))
                conn.commit()
            except Exception:
                raise HTTPException(409, "این کاربر از قبل دسترسی دارد")
        return {"ok": True, "grant": dict(target)}

    @app.delete("/api/classes/{class_id}/access/{user_id}")
    async def revoke_access(request: Request, class_id: int, user_id: int):
        with dbmod.connect() as conn:
            cls = class_or_404(conn, class_id)
            require_class_owner(request, conn, cls)
            conn.execute("DELETE FROM class_access WHERE class_id=? AND user_id=?",
                         (class_id, user_id))
            conn.commit()
        return {"ok": True}

    # ---------- کتاب PDF روی تخته ----------
    @app.post("/api/sessions/{session_id}/board/pdf")
    async def board_pdf_upload(request: Request, session_id: int, file: UploadFile = File(...)):
        body = {}
        with dbmod.connect() as conn:
            session_row, who = board_auth(conn, request, session_id, body)
            if not who["is_teacher"]:
                raise HTTPException(403, "فقط دبیر می‌تواند PDF بارگذاری کند")
            if session_row["status"] != "open":
                raise HTTPException(409, "جلسه «باز» نیست")
        blob = await file.read()
        try:
            pdf_id = pdf_store.save_pdf(blob)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        name = Path(file.filename or "book.pdf").name[:120] or "book.pdf"
        with dbmod.connect() as conn:
            conn.execute("UPDATE sessions SET pdf_id=?, pdf_name=?, pdf_page=1 WHERE id=?",
                         (pdf_id, name, session_id))
            conn.commit()
        return {"ok": True, "pdf": {"id": pdf_id, "name": name, "page": 1}}

    @app.post("/api/sessions/{session_id}/board/pdf/page")
    async def board_pdf_page(request: Request, session_id: int):
        body = await request.json()
        try:
            page = int(body.get("page") or 1)
        except (TypeError, ValueError):
            raise HTTPException(400, "شماره‌ی صفحه نامعتبر است")
        page = max(1, min(page, 10000))
        with dbmod.connect() as conn:
            session_row, who = board_auth(conn, request, session_id, body)
            if not who["is_teacher"]:
                raise HTTPException(403, "فقط دبیر می‌تواند صفحه را تغییر دهد")
            conn.execute("UPDATE sessions SET pdf_page=? WHERE id=?", (page, session_id))
            conn.commit()
        return {"ok": True, "page": page}

    @app.post("/api/sessions/{session_id}/board/pdf/close")
    async def board_pdf_close(request: Request, session_id: int):
        body = await request.json() if await _has_json_body(request) else {}
        with dbmod.connect() as conn:
            session_row, who = board_auth(conn, request, session_id, body)
            if not who["is_teacher"]:
                raise HTTPException(403, "فقط دبیر")
            conn.execute("UPDATE sessions SET pdf_id=NULL, pdf_name=NULL, pdf_page=1 WHERE id=?",
                         (session_id,))
            conn.commit()
        return {"ok": True}

    @app.get("/api/board/pdf/{pdf_id}")
    async def board_pdf_file(request: Request, pdf_id: str, token: str | None = None):
        """استریم PDF — فقط اعضای همان کلاس (دبیر/مهمان با کوکی، یا دانش‌آموز همان کلاس با توکن)."""
        path = pdf_store.pdf_path(pdf_id)
        if not path:
            raise HTTPException(404, "فایل پیدا نشد")
        with dbmod.connect() as conn:
            session_row = conn.execute(
                "SELECT * FROM sessions WHERE pdf_id=? ORDER BY id DESC LIMIT 1", (pdf_id,)).fetchone()
            if not session_row:
                raise HTTPException(404, "فایل پیدا نشد")
            user = current_user(request)
            if user:
                cls = class_or_404(conn, session_row["class_id"])
                require_class_teacher(request, conn, cls)
            else:
                entry = resolve_student_entry(conn, token or "") if token else None
                if not entry or entry["class_id"] != session_row["class_id"]:
                    raise HTTPException(403, "دسترسی به این فایل ندارید")
        return FileResponse(path, media_type="application/pdf",
                            headers={"Content-Disposition": "inline; filename=book.pdf",
                                     "Cache-Control": "private, max-age=3600"})

    # ---------- ضبط جلسه (LiveKit Egress — اختیاری) ----------
    def recordings_dir() -> Path:
        d = Path(os.environ.get("LIVECLASS_RECORDINGS_DIR") or (Path(dbmod.db_path()).parent / "recordings"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def recording_file_for(session_id: int):
        files = sorted(recordings_dir().glob(f"{session_id}-*.mp4"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        return files[0] if files else None

    @app.post("/api/sessions/{session_id}/record/start")
    async def record_start(request: Request, session_id: int):
        with dbmod.connect() as conn:
            session_row, who = board_auth(conn, request, session_id, {})
            if not who["is_teacher"]:
                raise HTTPException(403, "فقط دبیر می‌تواند ضبط کند")
            if session_row["status"] != "open":
                raise HTTPException(409, "جلسه «باز» نیست")
            if session_row["record_status"] == "recording":
                return {"ok": True, "egress_id": session_row["record_egress_id"], "status": "recording", "already": True}
        egress_enabled = os.environ.get("LIVECLASS_EGRESS_ENABLE") == "1" or os.environ.get("LIVECLASS_LIVEKIT_MOCK") == "1"
        if not egress_enabled:
            raise HTTPException(503, "ضبط روی این سرور فعال نشده است (LIVECLASS_EGRESS_ENABLE=1 لازم است)")
        # اسکریپت نصب، RECORD_DIR را در همین مسیر داخل کانتینر egress ماونت می‌کند
        target = recordings_dir() / f"{session_id}-{session_row['room_name']}-{int(_dt.now().timestamp())}.mp4"
        try:
            egress_id = await livekit_hub.start_room_composite_egress(session_row["room_name"], str(target))
        except Exception as exc:
            raise HTTPException(502, f"شروع ضبط ناموفق بود (آیا سرویس livekit-egress اجرا شده؟): {exc}")
        with dbmod.connect() as conn:
            conn.execute("UPDATE sessions SET record_egress_id=?, record_status='recording' WHERE id=?",
                         (egress_id, session_id))
            conn.commit()
        return {"ok": True, "egress_id": egress_id, "status": "recording"}

    @app.post("/api/sessions/{session_id}/record/stop")
    async def record_stop(request: Request, session_id: int):
        with dbmod.connect() as conn:
            session_row, who = board_auth(conn, request, session_id, {})
            if not who["is_teacher"]:
                raise HTTPException(403, "فقط دبیر")
            egress_id = session_row["record_egress_id"]
        if egress_id:
            await livekit_hub.stop_egress(egress_id)
        with dbmod.connect() as conn:
            conn.execute("UPDATE sessions SET record_status='stopped' WHERE id=? AND record_status='recording'",
                         (session_id,))
            conn.commit()
        return {"ok": True, "status": "stopped"}

    @app.get("/api/sessions/{session_id}/record/status")
    async def record_status(request: Request, session_id: int):
        with dbmod.connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row:
                raise HTTPException(404, "جلسه پیدا نشد")
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
        f = recording_file_for(session_id)
        return {"record_status": session_row["record_status"],
                "egress_id": session_row["record_egress_id"],
                "has_file": bool(f),
                "file_name": f.name if f else None}

    @app.get("/api/sessions/{session_id}/record/download")
    async def record_download(request: Request, session_id: int):
        with dbmod.connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session_row:
                raise HTTPException(404, "جلسه پیدا نشد")
            cls = class_or_404(conn, session_row["class_id"])
            require_class_teacher(request, conn, cls)
        f = recording_file_for(session_id)
        if not f:
            raise HTTPException(404, "فایل ضبط هنوز آماده نیست؛ چند ثانیه بعد دوباره تلاش کنید")
        return FileResponse(f, media_type="video/mp4", filename=f.name)

    # ---------- تخته تعاملی ----------
    @app.post("/api/sessions/{session_id}/board/fetch")
    async def board_fetch(request: Request, session_id: int):
        body = await request.json() if await _has_json_body(request) else {}
        with dbmod.connect() as conn:
            session_row, _who = board_auth(conn, request, session_id, body)
            ops = load_board_ops(conn, session_id)
        pdf = None
        if session_row["pdf_id"]:
            pdf = {"id": session_row["pdf_id"], "name": session_row["pdf_name"], "page": session_row["pdf_page"]}
        return {"open": bool(session_row["board_open"]), "ops": ops, "pdf": pdf}

    @app.post("/api/sessions/{session_id}/board/op")
    async def board_op(request: Request, session_id: int):
        body = await request.json()
        op = body.get("op")
        if not isinstance(op, dict):
            raise HTTPException(400, "op نامعتبر است")
        op_id = str(op.get("id") or "").strip()
        op_type = str(op.get("t") or "").strip()
        if not op_id or len(op_id) > 64 or op_type not in ("stroke", "clear"):
            raise HTTPException(400, "عملیات تخته نامعتبر است")
        raw = json.dumps(op, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > MAX_BOARD_OP_BYTES:
            raise HTTPException(413, "عملیات خیلی بزرگ است")
        with dbmod.connect() as conn:
            session_row, who = board_auth(conn, request, session_id, body)
            if session_row["status"] != "open":
                raise HTTPException(409, "جلسه «باز» نیست")
            count = conn.execute("SELECT count(*) FROM board_ops WHERE session_id=?",
                                 (session_id,)).fetchone()[0]
            if count >= MAX_BOARD_OPS:
                raise HTTPException(429, "تخته بیش از حد اجازه پر شده است؛ دبیر آن را پاک کند")
            conn.execute("INSERT OR IGNORE INTO board_ops(session_id, op_id, author, op) VALUES (?,?,?,?)",
                         (session_id, op_id, who["name"], raw))
            conn.commit()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/board/clear")
    async def board_clear(request: Request, session_id: int):
        body = await request.json()
        with dbmod.connect() as conn:
            session_row, who = board_auth(conn, request, session_id, body)
            if not who["is_teacher"]:
                raise HTTPException(403, "فقط دبیر می‌تواند تخته را پاک کند")
            if session_row["status"] != "open":
                raise HTTPException(409, "جلسه «باز» نیست")
            conn.execute("DELETE FROM board_ops WHERE session_id=?", (session_id,))
            op_id = str(body.get("op_id") or "").strip()[:64] or ("clear-" + secrets.token_hex(8))
            op = {"t": "clear", "id": op_id}
            conn.execute("INSERT INTO board_ops(session_id, op_id, author, op) VALUES (?,?,?,?)",
                         (session_id, op["id"], who["name"], json.dumps(op, ensure_ascii=False)))
            conn.commit()
        return {"ok": True, "op": op}

    @app.post("/api/sessions/{session_id}/board/state")
    async def board_state(request: Request, session_id: int):
        body = await request.json()
        with dbmod.connect() as conn:
            session_row, who = board_auth(conn, request, session_id, body)
            if not who["is_teacher"]:
                raise HTTPException(403, "فقط دبیر می‌تواند وضعیت تخته را تغییر دهد")
            open_flag = 1 if body.get("open") else 0
            conn.execute("UPDATE sessions SET board_open=? WHERE id=?", (open_flag, session_id))
            conn.commit()
        return {"ok": True, "open": bool(open_flag)}

    # ---------- ورود دانش‌آموز با لینک اختصاصی ----------
    def resolve_student_entry(conn, token: str):
        row = conn.execute("""
            SELECT et.class_id, et.student_id, s.full_name, s.student_code, c.title AS class_title
            FROM entry_tokens et
            JOIN students s ON s.id=et.student_id
            JOIN classes c ON c.id=et.class_id
            WHERE et.token=?""", (token,)).fetchone()
        return row

    @app.post("/api/student/enter")
    async def student_enter(request: Request):
        body = await request.json()
        token = (body.get("token") or "").strip()
        if not token:
            raise HTTPException(400, "لینک نامعتبر است")
        with dbmod.connect() as conn:
            entry = resolve_student_entry(conn, token)
            if not entry:
                raise HTTPException(404, "لینک کلاس معتبر نیست")
            session_row = open_session_for_class(conn, entry["class_id"])
            if not session_row:
                raise HTTPException(409, "کلاس هنوز شروع نشده است؛ منتظر بمانید تا دبیر کلاس را شروع کند")
            now = now_str()
            conn.execute("""
                INSERT INTO attendance(session_id, student_id, joined_at, last_heartbeat)
                VALUES (?,?,?,?)
                ON CONFLICT(session_id, student_id) DO UPDATE SET last_heartbeat=excluded.last_heartbeat""",
                (session_row["id"], entry["student_id"], now, now))
            conn.commit()
            active = conn.execute(
                "SELECT count(*) FROM attendance WHERE session_id=? AND left_at IS NULL",
                (session_row["id"],)).fetchone()[0]
            # چند نفر اول اتاق اجازه‌ی انتشار تصویر/صدا دارند؛ بقیه فقط تماشا می‌کنند (بار سرور پایین می‌ماند)
            can_publish = active <= MAX_PUBLISHERS
            room = session_row["room_name"]
        livekit_token = livekit_hub.mint_token(room, f"student-{entry['student_id']}", entry["full_name"],
                                                can_publish=can_publish)
        return {"livekit_url": livekit_hub.public_url(), "token": livekit_token,
                "room": room, "session_id": session_row["id"],
                "student_name": entry["full_name"], "class_title": entry["class_title"],
                "can_publish": can_publish}

    @app.post("/api/student/heartbeat")
    async def student_heartbeat(request: Request):
        body = await request.json()
        token = (body.get("token") or "").strip()
        session_id = int(body.get("session_id") or 0)
        with dbmod.connect() as conn:
            entry = resolve_student_entry(conn, token)
            if not entry:
                raise HTTPException(403, "توکن نامعتبر است")
            cur = conn.execute("""
                UPDATE attendance SET last_heartbeat=?
                WHERE session_id=? AND student_id=? AND left_at IS NULL""",
                (now_str(), session_id, entry["student_id"]))
            conn.commit()
        return {"ok": True, "updated": cur.rowcount}

    # ---------- کاربران و پروفایل ----------
    @app.get("/api/users")
    async def list_users(request: Request):
        require_admin(request)
        with dbmod.connect() as conn:
            rows = conn.execute("""SELECT id, username, full_name, role, created_at FROM users ORDER BY id""").fetchall()
        return {"users": [dict(r) for r in rows]}

    @app.post("/api/users")
    async def create_user(request: Request):
        require_admin(request)
        body = await request.json()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        full_name = (body.get("full_name") or "").strip()
        role = (body.get("role") or "teacher").strip()
        if len(username) < 3:
            raise HTTPException(400, "نام کاربری حداقل ۳ کاراکتر باشد")
        if len(password) < 6:
            raise HTTPException(400, "رمز حداقل ۶ کاراکتر باشد")
        if role not in ("teacher", "guest"):
            raise HTTPException(400, "نقش باید teacher یا guest باشد")
        if not full_name:
            full_name = username
        with dbmod.connect() as conn:
            if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                raise HTTPException(409, "این نام کاربری قبلاً گرفته شده")
            cur = conn.execute("INSERT INTO users(username, full_name, password_hash, role) VALUES (?,?,?,?)",
                               (username, full_name, hash_password(password), role))
            conn.commit()
            row = conn.execute("SELECT id, username, full_name, role, created_at FROM users WHERE id=?",
                               (cur.lastrowid,)).fetchone()
        return {"user": dict(row)}

    @app.put("/api/users/{user_id}/password")
    async def admin_set_password(request: Request, user_id: int):
        require_admin(request)
        body = await request.json()
        password = body.get("password") or ""
        if len(password) < 6:
            raise HTTPException(400, "رمز حداقل ۶ کاراکتر باشد")
        with dbmod.connect() as conn:
            if not conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
                raise HTTPException(404, "کاربر پیدا نشد")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
            conn.commit()
        return {"ok": True}

    @app.delete("/api/users/{user_id}")
    async def delete_user(request: Request, user_id: int):
        admin = require_admin(request)
        if admin["id"] == user_id:
            raise HTTPException(400, "نمی‌توانید حساب خودتان را حذف کنید")
        with dbmod.connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise HTTPException(404, "کاربر پیدا نشد")
            owned = conn.execute("SELECT count(*) FROM classes WHERE teacher_id=?", (user_id,)).fetchone()[0]
            if owned:
                raise HTTPException(400, f"این کاربر مالک {owned} کلاس است؛ اول دسترسی آن کلاس‌ها را تغییر دهید")
            conn.execute("DELETE FROM class_access WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.commit()
        return {"ok": True}

    @app.post("/api/me/password")
    async def change_my_password(request: Request):
        user = require_user(request)
        body = await request.json()
        current = body.get("current_password") or ""
        new = body.get("new_password") or ""
        if len(new) < 6:
            raise HTTPException(400, "رمز جدید حداقل ۶ کاراکتر باشد")
        with dbmod.connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if not row or not verify_password(current, row["password_hash"]):
                raise HTTPException(400, "رمز فعلی اشتباه است")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), user["id"]))
            conn.commit()
        return {"ok": True}

    VIDEO_PRESETS = {"optimized", "balanced", "quality"}
    VIDEO_FPS = {15, 20, 24}

    @app.get("/api/me/preferences")
    async def get_preferences(request: Request):
        user = require_user(request)
        with dbmod.connect() as conn:
            row = conn.execute("SELECT video_preset, video_fps FROM users WHERE id=?",
                               (user["id"],)).fetchone()
        return {"video_preset": row["video_preset"] if row else "optimized",
                "video_fps": row["video_fps"] if row else 15}

    @app.put("/api/me/preferences")
    async def put_preferences(request: Request):
        user = require_user(request)
        body = await request.json()
        preset = str(body.get("video_preset") or "").strip()
        try:
            fps = int(body.get("video_fps"))
        except (TypeError, ValueError):
            fps = 0
        if preset not in VIDEO_PRESETS:
            raise HTTPException(400, "حالت کیفیت باید یکی از optimized/balanced/quality باشد")
        if fps not in VIDEO_FPS:
            raise HTTPException(400, "فریم‌ریت باید ۱۵، ۲۰ یا ۲۴ باشد")
        with dbmod.connect() as conn:
            conn.execute("UPDATE users SET video_preset=?, video_fps=? WHERE id=?",
                         (preset, fps, user["id"]))
            conn.commit()
        return {"ok": True, "video_preset": preset, "video_fps": fps}

    # ---------- بکاپ کامل سرور (zip: دیتابیس + dump JSON + برند) ----------
    @app.get("/api/admin/backup")
    async def admin_backup(request: Request):
        require_admin(request)
        tables = ["settings", "users", "students", "classes", "enrollments", "entry_tokens",
                  "sessions", "attendance", "class_access", "board_ops"]
        dump = {"format": "liveclass-backup", "version": 1, "created_at": now_str(), "tables": {}}
        with dbmod.connect() as conn:
            for t in tables:
                try:
                    dump["tables"][t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
                except Exception:
                    dump["tables"][t] = []
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
        try:
            with os.fdopen(tmp_fd, "wb") as _fh:
                pass
            src_conn = sqlite3.connect(dbmod.db_path())
            dst_conn = sqlite3.connect(tmp_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close(); src_conn.close()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("backup.json", json.dumps(dump, ensure_ascii=False, indent=1))
                zf.write(tmp_path, "liveclass.db")
                brand_dir = Path(dbmod.db_path()).parent / "brand"
                if brand_dir.is_dir():
                    for f in brand_dir.iterdir():
                        if f.is_file():
                            zf.write(f, f"brand/{f.name}")
                zf.writestr("README.txt", "LiveClass backup\n\nمحتویات:\n- backup.json : خروجی JSON همه‌ی جدول‌ها\n- liveclass.db : کپی سازگار دیتابیس SQLite (برای بازگردانی کامل)\n- brand/ : لوگو و آیکون‌های آپلود‌شده\n\nبازگردانی روی سرور جدید:\n1) systemctl stop liveclass\n2) cp liveclass.db به مسیر server/data/liveclass.db\n3) systemctl start liveclass\n\nهشدار: این فایل شامل داده‌های کامل مدرسه (و رمزهای هش‌شده) است؛ خصوصی نگه دارید.")
            payload = buf.getvalue()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
        return Response(payload, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="liveclass-backup-{stamp}.zip"'})

    # ---------- ایمپورت CSV ----------
    @app.post("/api/admin/import/{kind}")
    async def import_csv(request: Request, kind: str, file: UploadFile = File(...)):
        require_admin(request)
        if kind not in ("teachers", "classes", "students"):
            raise HTTPException(400, "نوع نامعتبر")
        text = (await file.read()).decode("utf-8-sig", errors="replace")
        func = {"teachers": importer.import_teachers, "classes": importer.import_classes,
                "students": importer.import_students}[kind]
        try:
            report = func(text)
        except Exception as exc:
            raise HTTPException(400, f"ایمپورت ناموفق بود: {exc}")
        return {"ok": True, "report": report}

    # ---------- استاتیک SPA (در حالت production) ----------
    dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404, "یافت نشد")
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
