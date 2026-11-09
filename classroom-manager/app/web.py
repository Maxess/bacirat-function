# -*- coding: utf-8 -*-
"""سرور HTTP ماژول مدیریت کلاس — استاندارد لِیبری (بدون وابستگی خارجی).

اجرا:
    python3 run.py serve --port 8090
پیکربندی با متغیر محیطی:
    PERSIANMEET_URL     آدرس عمومی PersianMeet (پیش‌فرض http://127.0.0.1:8003)
    PERSIANMEET_SECRET  secret API (برای MeetList/DeleteMeet لازم است)
    PM_MANAGER_DB       مسیر فایل SQLite (پیش‌فرض ./data/classroom_manager.db)
    PM_MANAGER_SECRET   کلید امضای کوکی (پیش‌فرض: تولید خودکار در کنار دیتابیس)
    PM_MANAGER_CLOSE_STRATEGY  delete | keep   (پیش‌فرض: delete — واقعاً همه را بیرون می‌کند)
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import sys
import time
import traceback
from datetime import datetime, timezone
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

from . import db as dbmod
from .auth import SessionCodec, verify_password, hash_password
from .importer import import_teachers, import_classes, import_students
from .templates import page, msg_box
from .pmclient import PersianMeetAPI, PersianMeetError
from html import escape


class Redirect(Exception):
    def __init__(self, location: str):
        self.location = location


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def seconds_between(a: str, b: str) -> int:
    try:
        ta = datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
        tb = datetime.strptime(b, "%Y-%m-%d %H:%M:%S")
        return max(0, int((tb - ta).total_seconds()))
    except Exception:
        return 0


def fmt_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class Config:
    def __init__(self):
        self.db_path = os.environ.get("PM_MANAGER_DB") or str(Path(__file__).resolve().parent.parent / "data" / "classroom_manager.db")
        secret = os.environ.get("PM_MANAGER_SECRET")
        if not secret:
            key_file = Path(self.db_path).parent / "secret_key"
            if key_file.exists():
                secret = key_file.read_text("utf-8").strip()
            else:
                secret = secrets.token_hex(32)
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_text(secret, "utf-8")
                try:
                    os.chmod(key_file, 0o600)
                except OSError:
                    pass
        self.session = SessionCodec(secret)
        self.pm_url = (os.environ.get("PERSIANMEET_URL") or "http://127.0.0.1:8003").rstrip("/")
        self.pm_secret = os.environ.get("PERSIANMEET_SECRET") or ""
        self.pm_timeout = float(os.environ.get("PERSIANMEET_TIMEOUT", "15"))
        self.close_strategy = (os.environ.get("PM_MANAGER_CLOSE_STRATEGY") or "delete").lower()
        assert self.close_strategy in ("delete", "keep")

    def pm_api(self) -> PersianMeetAPI:
        return PersianMeetAPI(self.pm_url, secret=self.pm_secret or None, timeout=self.pm_timeout)


class Handler(BaseHTTPRequestHandler):
    server_version = "ClassroomManager/1.0"
    MAX_BODY = 25 * 1024 * 1024

    # ---------- ابزارهای پایه ----------
    @property
    def cfg(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > self.MAX_BODY:
            raise ValueError("حجم بدنه بیش از حد مجاز است")
        return self.rfile.read(length) if length else b""

    def _read_form(self) -> dict:
        ctype = self.headers.get("Content-Type", "")
        body = self._read_body()
        if "application/x-www-form-urlencoded" in ctype:
            return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
        return {}

    def send_html(self, content: bytes, status: int = 200, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Frame-Ancestors", "none")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(content)

    def send_page(self, title: str, body: str, status: int = 200, user: dict | None = None, headers=None):
        self.send_html(page(title, body, user), status=status, headers=headers)

    def redirect(self, location: str, status: int = 302):
        location = quote(location, safe=":/?#[]@!$&'()*+,;=%")
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_csv(self, filename: str, text: str):
        body = text.encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- احراز هویت ----------
    def current_user(self) -> dict | None:
        if getattr(self, "_user", None) is not None:
            return self._user
        token = None
        for part in (self.headers.get("Cookie") or "").split(";"):
            part = part.strip()
            if part.startswith("pm_session="):
                token = part[len("pm_session="):]
        data = self.cfg.session.decode(token) if token else None
        user = None
        if data:
            with dbmod.connect(self.cfg.db_path) as conn:
                row = conn.execute("SELECT id, username, full_name, role FROM teachers WHERE id=?", (data["uid"],)).fetchone()
                if row:
                    user = dict(row)
        self._user = user
        return user

    def require_user(self) -> dict:
        user = self.current_user()
        if not user:
            raise Redirect("/login")
        return user

    def require_admin(self) -> dict:
        user = self.require_user()
        if user["role"] != "admin":
            raise Redirect("/")
        return user

    def public_base(self) -> str:
        scheme = self.headers.get("X-Forwarded-Proto") or ("https" if self.headers.get("X-Forwarded-Ssl") == "on" else "http")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        return f"{scheme}://{host}"

    def _set_session_cookie(self, uid: int, role: str) -> str:
        token = self.cfg.session.encode(uid, role)
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") == "https") else ""
        return f"pm_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={8 * 3600}{secure}"

    # ---------- مسیریابی ----------
    ROUTES = [
        ("GET",  re.compile(r"/healthz"),                         "healthz"),
        ("GET",  re.compile(r"/login"),                           "login_form"),
        ("POST", re.compile(r"/login"),                           "login_submit"),
        ("GET",  re.compile(r"/logout"),                          "logout"),
        ("GET",  re.compile(r"/"),                                "dashboard"),
        ("GET",  re.compile(r"/class/(\d+)"),                     "class_page"),
        ("POST", re.compile(r"/class/(\d+)/start"),               "class_start"),
        ("GET",  re.compile(r"/session/(\d+)"),                   "session_page"),
        ("POST", re.compile(r"/session/(\d+)/close"),             "session_close"),
        ("GET",  re.compile(r"/session/(\d+)/attendance\.csv"),   "attendance_csv"),
        ("GET",  re.compile(r"/enter"),                           "enter"),
        ("GET",  re.compile(r"/admin/import"),                    "import_form"),
        ("POST", re.compile(r"/admin/import"),                    "import_submit"),
        ("GET",  re.compile(r"/admin/teachers"),                  "admin_teachers"),
        ("GET",  re.compile(r"/admin/students"),                  "admin_students"),
        ("GET",  re.compile(r"/admin/classes"),                   "admin_classes"),
        ("POST", re.compile(r"/admin/delete"),                    "admin_delete"),
    ]

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        self._query = parse_qs(parsed.query)
        try:
            for route_method, pattern, name in self.ROUTES:
                if route_method != method:
                    continue
                m = pattern.fullmatch(path)
                if m:
                    getattr(self, f"route_{name}")(m)
                    return
            self.send_page("یافت نشد", '<div class="card"><h1>۴۰۴</h1><p>این صفحه وجود ندارد.</p></div>', status=404)
        except Redirect as r:
            self.redirect(r.location)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self.send_page("خطای داخلی", f'<div class="card"><h1>خطای داخلی سرور</h1><pre class="muted">{escape(traceback.format_exc(limit=3))}</pre></div>', status=500)
            except Exception:
                pass

    def q(self, key: str, default: str = "") -> str:
        return (self._query.get(key) or [default])[0]

    # ---------- health ----------
    def route_healthz(self, m):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- ورود ----------
    def route_login_form(self, m):
        if self.current_user():
            raise Redirect("/")
        body = """<div class="card" style="max-width:430px;margin:3rem auto">
  <h1>ورود به سامانه</h1>
  <form method="post" action="/login">
    <label class="muted">نام کاربری</label>
    <input class="input" name="username" autocomplete="username" required autofocus><br><br>
    <label class="muted">رمز عبور</label>
    <input class="input" type="password" name="password" autocomplete="current-password" required><br><br>
    <button class="btn btn-primary" type="submit" style="width:100%">ورود</button>
  </form>
  <p class="muted" style="margin-top:1rem">برای دانش‌آموزان: ورود از طریق لینک اختصاصی کلاس (بدون نام کاربری و رمز).</p>
</div>"""
        self.send_page("ورود", body)

    def route_login_submit(self, m):
        form = self._read_form()
        username, password = form.get("username", ""), form.get("password", "")
        with dbmod.connect(self.cfg.db_path) as conn:
            row = conn.execute("SELECT id, username, full_name, role, password_hash FROM teachers WHERE username=?",
                               (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            time.sleep(0.6)
            body = msg_box("err", "نام کاربری یا رمز عبور اشتباه است.") + \
                   '<p><a class="btn" href="/login">بازگشت</a></p>'
            self.send_page("ورود ناموفق", body, status=401)
            return
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", self._set_session_cookie(row["id"], row["role"]))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def route_logout(self, m):
        self.send_response(302)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", "pm_session=; Path=/; HttpOnly; Max-Age=0")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- داشبورد ----------
    def _class_query(self, user: dict):
        conn = dbmod.connect(self.cfg.db_path)
        if user["role"] == "admin":
            rows = conn.execute("""SELECT c.id, c.title, t.full_name AS teacher_name,
                                          (SELECT COUNT(*) FROM enrollments e WHERE e.class_id=c.id) AS student_count,
                                          (SELECT COUNT(*) FROM sessions s WHERE s.class_id=c.id AND s.status='open') AS open_session
                                   FROM classes c JOIN teachers t ON t.id=c.teacher_id ORDER BY c.id DESC""").fetchall()
        else:
            rows = conn.execute("""SELECT c.id, c.title, t.full_name AS teacher_name,
                                          (SELECT COUNT(*) FROM enrollments e WHERE e.class_id=c.id) AS student_count,
                                          (SELECT COUNT(*) FROM sessions s WHERE s.class_id=c.id AND s.status='open') AS open_session
                                   FROM classes c JOIN teachers t ON t.id=c.teacher_id
                                   WHERE c.teacher_id=? ORDER BY c.id DESC""", (user["id"],)).fetchall()
        return conn, rows

    def route_dashboard(self, m):
        user = self.require_user()
        conn, rows = self._class_query(user)
        cards = []
        for r in rows:
            state = '<span class="pill ok">نشست فعال</span>' if r["open_session"] else '<span class="pill">غیرفعال</span>'
            cards.append(f"""<div class="card">
  <h2>{escape(r['title'])}</h2>
  <p class="muted">دبیر: {escape(r['teacher_name'])} · {r['student_count']} دانش‌آموز {state}</p>
  <div class="row">
    <a class="btn btn-primary" href="/class/{r['id']}">مدیریت کلاس</a>
  </div>
</div>""")
        rendered = '<div class="grid">' + "".join(cards) + "</div>" if cards else \
                   '<div class="card"><h1>هنوز کلاسی ندارید</h1><p class="muted">ادمین باید کلاس‌ها را در «کلاس‌ها» و فایل CSV دانش‌آموزان را در «ایمپورت CSV» اضافه کند.</p></div>'
        admin_note = '<p class="muted">به‌عنوان مدیر، از منوی بالا می‌توانید دبیران، دانش‌آموزان و کلاس‌ها را مدیریت کنید.</p>' if user["role"] == "admin" else ""
        self.send_page("داشبورد", f"<h1>سلام، {escape(user['full_name'])} 👋</h1>{admin_note}{rendered}", user=user)

    # ---------- کلاس ----------
    def _get_class(self, conn, class_id: int):
        return conn.execute("SELECT c.id, c.title, c.teacher_id, t.full_name AS teacher_name FROM classes c JOIN teachers t ON t.id=c.teacher_id WHERE c.id=?", (class_id,)).fetchone()

    def _check_class_access(self, user: dict, cls) -> bool:
        return user["role"] == "admin" or cls["teacher_id"] == user["id"]

    def route_class_page(self, m):
        user = self.require_user()
        class_id = int(m.group(1))
        with dbmod.connect(self.cfg.db_path) as conn:
            cls = self._get_class(conn, class_id)
            if not cls:
                raise Redirect("/")
            if not self._check_class_access(user, cls):
                raise Redirect("/")
            students = conn.execute("""SELECT s.id, s.student_code, s.full_name
                                        FROM students s JOIN enrollments e ON e.student_id=s.id
                                        WHERE e.class_id=? ORDER BY s.full_name""", (class_id,)).fetchall()
            tokens = {r["student_id"]: r["token"] for r in conn.execute("SELECT student_id, token FROM entry_tokens WHERE class_id=?", (class_id,)).fetchall()}
            sessions = conn.execute("SELECT * FROM sessions WHERE class_id=? ORDER BY id DESC LIMIT 20", (class_id,)).fetchall()
            open_session = conn.execute("SELECT id FROM sessions WHERE class_id=? AND status='open'", (class_id,)).fetchone()
            base = self.public_base()
            # اطمینان از وجود توکن همه‌ی دانش‌آموزان (برای نمایش لینک‌ها)
            for s in students:
                if s["id"] not in tokens:
                    tokens[s["id"]] = dbmod.ensure_entry_token(conn, class_id, s["id"])
            conn.commit()
        msg = self.q("msg")
        banner = msg_box("ok", msg) if msg else ""
        start_block = (f'<a class="btn btn-ok" href="/session/{open_session["id"]}">▶️ ادامه‌ی نشست فعال</a>'
                       if open_session else
                       f'<form method="post" action="/class/{class_id}/start" style="display:inline"><button class="btn btn-primary" type="submit">🚀 شروع کلاس</button></form>')
        links_rows = "".join(
            f'<tr><td>{escape(s["student_code"])}</td><td>{escape(s["full_name"])}</td>'
            f'<td><code class="link">{escape(base)}/enter?token={escape(tokens[s["id"]])}</code></td>'
            f'<td class="copy-btn" style="cursor:pointer;color:var(--accent)">📋 کپی</td></tr>'
            for s in students)
        history_rows = "".join(
            f'<tr><td>{escape(r["started_at"])}</td><td>{r["meet_id"]}</td>'
            f'<td>{"🟢 باز" if r["status"] == "open" else "⚫ بسته"}</td>'
            f'<td>{escape(r["closed_at"] or "—")}</td>'
            f'<td><a class="btn" href="/session/{r["id"]}">مشاهده</a> '
            f'<a class="btn" href="/session/{r["id"]}/attendance.csv">خروجی CSV</a></td></tr>'
            for r in sessions) or '<tr><td colspan="5" class="muted">هنوز نشستی برگزار نشده است.</td></tr>'
        body = f"""{banner}
<div class="card">
  <h1>{escape(cls['title'])}</h1>
  <p class="muted">دبیر: {escape(cls['teacher_name'])} · دانش‌آموزان: {len(students)} نفر</p>
  <div class="row">{start_block} <a class="btn" href="/">بازگشت</a></div>
</div>
<div class="card">
  <h2>لینک اختصاصی ورود دانش‌آموزان (پایدار — مخصوص همین کلاس)</h2>
  <p class="muted">این لینک‌ها همیشگی‌اند؛ هر دانش‌آموز با کلیک روی لینک خودش وارد اتاق می‌شود و زمان ورودش ثبت می‌شود. صفحه را چاپ یا لینک‌ها را در گروه کلاس منتشر کنید.</p>
  <div style="overflow-x:auto"><table><thead><tr><th>کد</th><th>نام</th><th>لینک ورود</th><th></th></tr></thead><tbody>{links_rows or '<tr><td colspan="4" class="muted">دانش‌آموزی ثبت نشده است.</td></tr>'}</tbody></table></div>
</div>
<div class="card">
  <h2>نشست‌های قبلی کلاس</h2>
  <div style="overflow-x:auto"><table><thead><tr><th>شروع</th><th>شناسه اتاق</th><th>وضعیت</th><th>بستن</th><th></th></tr></thead><tbody>{history_rows}</tbody></table></div>
</div>"""
        self.send_page(cls["title"], body, user=user)

    def route_class_start(self, m):
        user = self.require_user()
        class_id = int(m.group(1))
        with dbmod.connect(self.cfg.db_path) as conn:
            cls = self._get_class(conn, class_id)
            if not cls or not self._check_class_access(user, cls):
                raise Redirect("/")
            existing = conn.execute("SELECT id FROM sessions WHERE class_id=? AND status='open'", (class_id,)).fetchone()
            if existing:
                raise Redirect(f"/session/{existing['id']}")
            student_count = conn.execute("SELECT COUNT(*) c FROM enrollments WHERE class_id=?", (class_id,)).fetchone()["c"]
        capacity = max(20, student_count + 15)
        admin_pass = secrets.token_urlsafe(6)
        try:
            pm = self.cfg.pm_api()
            meet = pm.create_meet(title=cls["title"], user_count=capacity,
                                  user_need_pass=False, admin_need_pass=True, admin_pass=admin_pass)
            meet_id = int(meet["ID"])
            join = pm.join(meet_id=meet_id, username=cls["teacher_name"], join_role=1, pass_=admin_pass)
            teacher_pid = join["PID"]
        except (PersianMeetError, KeyError, ValueError) as exc:
            body = f"""<div class="card"><h1>خطا در اتصال به PersianMeet</h1>
<p class="muted">ساخت اتاق انجام نشد: <code>{escape(str(exc))}</code></p>
<p>موارد را بررسی کنید:</p>
<ul>
<li>سرویس در حال اجراست؟ <code dir="ltr">systemctl status persianmeet</code></li>
<li>آدرس در <code dir="ltr">PERSIANMEET_URL</code> درست است؟ (الان: <code dir="ltr">{escape(self.cfg.pm_url)}</code>)</li>
<li>لاگ: <code dir="ltr">journalctl -u persianmeet -n 50</code></li>
</ul>
<a class="btn" href="/class/{class_id}">بازگشت</a></div>"""
            self.send_page("خطا", body, status=502, user=user)
            return
        with dbmod.connect(self.cfg.db_path) as conn:
            cur = conn.execute("INSERT INTO sessions(class_id, meet_id, teacher_pid, admin_pass, status, started_at) VALUES (?,?,?,?,'open',?)",
                             (class_id, meet_id, teacher_pid, admin_pass, now_str()))
            session_id = cur.lastrowid
            conn.commit()
        raise Redirect(f"/session/{session_id}")

    def _load_session(self, conn, session_id: int):
        return conn.execute("SELECT s.*, c.title AS class_title, c.teacher_id FROM sessions s JOIN classes c ON c.id=s.class_id WHERE s.id=?",
                            (session_id,)).fetchone()

    def route_session_page(self, m):
        user = self.require_user()
        session_id = int(m.group(1))
        with dbmod.connect(self.cfg.db_path) as conn:
            sess = self._load_session(conn, session_id)
            if not sess or not self._check_class_access(user, {"teacher_id": sess["teacher_id"]}):
                raise Redirect("/")
            rows = conn.execute("""SELECT s.id, s.student_code, s.full_name, a.entered_at, a.exited_at, a.pid
                                   FROM students s
                                   JOIN enrollments e ON e.student_id=s.id AND e.class_id=?
                                   LEFT JOIN attendance a ON a.session_id=? AND a.student_id=s.id
                                   ORDER BY s.full_name""", (sess["class_id"], session_id)).fetchall()
            tokens = {r["student_id"]: r["token"] for r in conn.execute("SELECT student_id, token FROM entry_tokens WHERE class_id=?", (sess["class_id"],)).fetchall()}
            for r in rows:
                if r["id"] not in tokens:
                    tokens[r["id"]] = dbmod.ensure_entry_token(conn, sess["class_id"], r["id"])
            conn.commit()
        base = self.public_base()
        msg = self.q("msg")
        banner = msg_box("ok", msg) if msg else ""
        is_open = sess["status"] == "open"
        status_pill = '<span class="pill ok">کلاس باز است</span>' if is_open else '<span class="pill danger">کلاس بسته است</span>'
        entered_rows = []
        entered = 0
        for r in rows:
            if r["entered_at"]:
                entered += 1
                until = r["exited_at"] or (now_str() if is_open else r["entered_at"])
                dur = fmt_duration(seconds_between(r["entered_at"], until))
                state = f'🟢 وارد شده ({escape(r["entered_at"][11:16])})'
                dur_cell = dur
            else:
                state = '<span class="muted">هنوز وارد نشده</span>'
                dur_cell = "—"
            entered_rows.append(f'<tr><td>{escape(r["student_code"])}</td><td>{escape(r["full_name"])}</td><td>{state}</td><td>{dur_cell}</td>'
                                f'<td><code class="link">{escape(base)}/enter?token={escape(tokens[r["id"]])}</code></td></tr>')
        until_note = "تا لحظه‌ی بستن کلاس" if not is_open else "تا همین لحظه (زنده)"
        teacher_link = f'{self.cfg.pm_url}/user/{sess["teacher_pid"]}' if sess["teacher_pid"] else ""
        refresh = '<meta http-equiv="refresh" content="15">' if is_open else ""
        if is_open:
            close_block = f'''<form method="post" action="/session/{session_id}/close" onsubmit="return confirm('کلاس بسته شود؟ همه‌ی دانش‌آموزان از اتاق خارج می‌شوند.')">
  <button class="btn btn-danger" type="submit">⏹️ بستن کلاس (خروج همه)</button></form>'''
        else:
            close_block = f'<a class="btn" href="/class/{sess["class_id"]}">بازگشت به صفحه‌ی کلاس</a>'
        body = f"""{refresh}{banner}
<div class="card">
  <h1>{escape(sess['class_title'])} {status_pill}</h1>
  <p class="muted">شروع: {escape(sess['started_at'])} · شناسه اتاق: {sess['meet_id']} · ورودی ثبت‌شده: {entered}/{len(rows)}</p>
  {f'<p><strong>لینک مستقیم ورود دبیر:</strong></p><code class="link">{escape(teacher_link)}</code>' if teacher_link else ''}
  <div class="row no-print" style="margin-top:.6rem">
    {close_block}
    <a class="btn" href="/session/{session_id}/attendance.csv">⬇️ خروجی حضور و غیاب (CSV)</a>
    <button class="btn" onclick="window.print()">🖨️ چاپ</button>
  </div>
  <p class="muted">مدت حضور دانش‌آموزان {until_note} محاسبه می‌شود.</p>
</div>
<div class="card">
  <h2>حضور و غیاب کلاس</h2>
  <div style="overflow-x:auto"><table><thead><tr><th>کد</th><th>نام</th><th>وضعیت</th><th>مدت حضور</th><th>لینک ورود</th></tr></thead>
  <tbody>{''.join(entered_rows)}</tbody></table></div>
</div>"""
        self.send_page(sess["class_title"], body, user=user)

    def route_session_close(self, m):
        user = self.require_user()
        session_id = int(m.group(1))
        with dbmod.connect(self.cfg.db_path) as conn:
            sess = self._load_session(conn, session_id)
            if not sess or not self._check_class_access(user, {"teacher_id": sess["teacher_id"]}):
                raise Redirect("/")
            if sess["status"] == "open":
                closed = now_str()
                conn.execute("UPDATE sessions SET status='closed', closed_at=? WHERE id=?", (closed, session_id))
                conn.execute("UPDATE attendance SET exited_at=? WHERE session_id=? AND exited_at IS NULL", (closed, session_id))
                conn.commit()
                note = "کلاس بسته شد و حضور و غیاب ثبت شد."
                if self.cfg.close_strategy == "delete" and sess["meet_id"]:
                    try:
                        self.cfg.pm_api().delete_meet(int(sess["meet_id"]))
                        note += " اتاق PersianMeet هم متوقف و حذف شد؛ همه خارج شدند."
                    except PersianMeetError as exc:
                        note += f" (بستن محلی انجام شد؛ حذف اتاق از PersianMeet ناموفق بود: {exc})"
            else:
                note = "این کلاس از قبل بسته شده است."
        raise Redirect(f"/session/{session_id}?msg={quote(note)}")

    def route_attendance_csv(self, m):
        user = self.require_user()
        session_id = int(m.group(1))
        with dbmod.connect(self.cfg.db_path) as conn:
            sess = self._load_session(conn, session_id)
            if not sess or not self._check_class_access(user, {"teacher_id": sess["teacher_id"]}):
                raise Redirect("/")
            rows = conn.execute("""SELECT s.student_code, s.full_name, a.entered_at, a.exited_at
                                   FROM students s
                                   JOIN enrollments e ON e.student_id=s.id AND e.class_id=?
                                   LEFT JOIN attendance a ON a.session_id=? AND a.student_id=s.id
                                   ORDER BY s.full_name""", (sess["class_id"], session_id)).fetchall()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["کد دانش‌آموز", "نام", "ورود", "خروج", "مدت حضور (ثانیه)", "مدت حضور"])
        for r in rows:
            entered, exited = r["entered_at"], r["exited_at"]
            if entered:
                until = exited or sess["closed_at"] or now_str()
                secs = seconds_between(entered, until)
                writer.writerow([r["student_code"], r["full_name"], entered, exited or "", secs, fmt_duration(secs)])
            else:
                writer.writerow([r["student_code"], r["full_name"], "", "", 0, "غایب"])
        self.send_csv(f"attendance-session-{session_id}.csv", out.getvalue())

    # ---------- ورود دانش‌آموز (redirector) ----------
    def route_enter(self, m):
        token = self.q("token").strip()
        if not token:
            self.send_page("لینک نامعتبر", '<div class="card"><h1>لینک نامعتبر</h1><p>هیچ توکنی ارسال نشد.</p></div>', status=400)
            return
        with dbmod.connect(self.cfg.db_path) as conn:
            row = conn.execute("""SELECT et.class_id, et.student_id, s.full_name, c.title AS class_title
                                  FROM entry_tokens et
                                  JOIN students s ON s.id=et.student_id
                                  JOIN classes c ON c.id=et.class_id
                                  WHERE et.token=?""", (token,)).fetchone()
            if not row:
                self.send_page("لینک نامعتبر", '<div class="card"><h1>این لینک معتبر نیست</h1><p>از دبیر کلاس لینک صحیح را بگیرید.</p></div>', status=404)
                return
            sess = conn.execute("SELECT * FROM sessions WHERE class_id=? AND status='open'", (row["class_id"],)).fetchone()
            if not sess:
                self.send_page("کلاس فعال نیست", f"""<div class="card" style="text-align:center;padding:2.5rem">
<h1>⏳ کلاس هنوز فعال نشده</h1>
<p class="muted">«{escape(row['class_title'])}» در حال حاضر باز نیست؛ وقتی دبیر کلاس را شروع کند، این لینک شما را مستقیم وارد اتاق می‌کند.</p>
</div>""", status=403)
                return
            att = conn.execute("SELECT pid FROM attendance WHERE session_id=? AND student_id=?",
                               (sess["id"], row["student_id"])).fetchone()
            if att:
                pid = att["pid"]
            else:
                # اولین کلیک در این نشست: ساخت PID در PersianMeet و ثبت زمان ورود واقعی
                try:
                    pm = self.cfg.pm_api()
                    join = pm.join(meet_id=int(sess["meet_id"]), username=row["full_name"], join_role=0)
                    pid = join["PID"]
                except (PersianMeetError, KeyError, ValueError) as exc:
                    self.send_page("خطا", f'<div class="card"><h1>خطا در ورود به کلاس</h1><p class="muted">{escape(str(exc))}</p></div>', status=502)
                    return
                conn.execute("INSERT INTO attendance(session_id, student_id, pid, entered_at) VALUES (?,?,?,?)",
                             (sess["id"], row["student_id"], pid, now_str()))
                conn.commit()
        self.redirect(f"{self.cfg.pm_url}/user/{pid}")

    # ---------- پنل مدیریت ----------
    def route_import_form(self, m):
        self.require_admin()
        body = """<div class="card" style="max-width:640px">
<h1>ایمپورت از CSV</h1>
<p class="muted">هر فایل را جداگانه آپلود کنید. جداکننده: کاما/سِمی‌کالن؛ کدگذاری: UTF-8 (BOM قبول می‌شود)؛ سطر اول = هدر.</p>
<form method="post" action="/admin/import" enctype="multipart/form-data">
  <p><strong>۱) دبیران</strong> — ستون‌ها: <code dir="ltr">username,password,full_name</code> (یا فارسی: نام‌کاربری، رمز، نام و نام خانوادگی)</p>
  <input class="input" type="file" name="teachers" accept=".csv"><br><br>
  <p><strong>۲) کلاس‌ها</strong> — ستون‌ها: <code dir="ltr">class_title,teacher_username</code></p>
  <input class="input" type="file" name="classes" accept=".csv"><br><br>
  <p><strong>۳) دانش‌آموزان</strong> — ستون‌ها: <code dir="ltr">student_code,full_name,class_title</code></p>
  <input class="input" type="file" name="students" accept=".csv"><br><br>
  <button class="btn btn-primary" type="submit">ایمپورت</button>
</form>
<p class="muted">مثال‌ها: <code dir="ltr">examples/teachers.csv</code>, <code dir="ltr">examples/classes.csv</code>, <code dir="ltr">examples/students.csv</code> داخل این پوشه.</p>
</div>"""
        self.send_page("ایمپورت CSV", body, user=self.current_user())

    def _parse_multipart(self) -> dict[str, bytes]:
        ctype = self.headers.get("Content-Type", "")
        body = self._read_body()
        if "multipart/form-data" not in ctype:
            return {}
        raw = (f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n").encode("utf-8") + body
        msg = message_from_bytes(raw)
        files: dict[str, bytes] = {}
        for part in msg.walk():
            disp = part.get("Content-Disposition", "")
            name_m = re.search(r'name="([^"]+)"', disp)
            filename = part.get_filename()
            if not name_m or not filename:
                continue
            payload = part.get_payload(decode=True) or b""
            if payload:
                files[name_m.group(1)] = payload
        return files

    def route_import_submit(self, m):
        self.require_admin()
        files = self._parse_multipart()
        reports = []
        for key, func in (("teachers", import_teachers), ("classes", import_classes), ("students", import_students)):
            blob = files.get(key)
            if not blob:
                continue
            text = blob.decode("utf-8-sig", errors="replace")
            try:
                rep = func(self.cfg.db_path, text)
            except Exception as exc:
                rep = {"errors": [{"line": 0, "reason": f"خطای کلی فایل: {exc}"}]}
            reports.append((key, rep))
        blocks = []
        for key, rep in reports:
            stats = "، ".join(f"{k}: {v}" for k, v in rep.items() if k != "errors")
            err_rows = "".join(f"<li>خط {e['line']}: {escape(e['reason'])}</li>" for e in rep.get("errors", []))
            err_block = f'<ul class="muted">{err_rows}</ul>' if err_rows else '<p class="muted">بدون خطا ✓</p>'
            blocks.append(f'<div class="card"><h2>{escape(key)}</h2><p>{escape(stats)}</p>{err_block}</div>')
        body = ('<h1>نتیجه‌ی ایمپورت</h1>' + "".join(blocks) +
                '<a class="btn" href="/admin/import">بازگشت</a> <a class="btn btn-primary" href="/">داشبورد</a>') if blocks else \
               msg_box("err", "هیچ فایلی انتخاب نشده بود.") + '<a class="btn" href="/admin/import">بازگشت</a>'
        self.send_page("نتیجه‌ی ایمپورت", body, user=self.current_user())

    def _simple_admin_table(self, title: str, headers: list[str], rows: list[list[str]]):
        head = "".join(f"<th>{escape(h)}</th>" for h in headers) + "<th></th>"
        body_rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
        return f'<h1>{escape(title)}</h1><div class="card" style="overflow-x:auto"><table><thead><tr>{head}</tr></thead><tbody>{body_rows or f"<tr><td colspan={len(headers)+1} class=muted>خالی</td></tr>"}</tbody></table></div><a class="btn" href="/">بازگشت</a>'

    def route_admin_teachers(self, m):
        self.require_admin()
        with dbmod.connect(self.cfg.db_path) as conn:
            rows = conn.execute("SELECT id, username, full_name, role FROM teachers ORDER BY id").fetchall()
        me = self.current_user()
        body_rows = []
        for r in rows:
            del_btn = f'<form method="post" action="/admin/delete" onsubmit="return confirm(\'حذف شود؟\')" style="display:inline"><input type="hidden" name="kind" value="teachers"><input type="hidden" name="id" value="{r["id"]}"><button class="btn btn-danger" type="submit">حذف</button></form>' if r["id"] != me["id"] else '<span class="muted">(شما)</span>'
            body_rows.append([escape(r["username"]), escape(r["full_name"]), escape(r["role"]), del_btn])
        self.send_page("دبیران", self._simple_admin_table("دبیران", ["نام کاربری", "نام", "نقش"], body_rows), user=self.current_user())

    def route_admin_students(self, m):
        self.require_admin()
        with dbmod.connect(self.cfg.db_path) as conn:
            rows = conn.execute("SELECT id, student_code, full_name FROM students ORDER BY student_code LIMIT 2000").fetchall()
        body_rows = []
        for r in rows:
            del_btn = f'<form method="post" action="/admin/delete" onsubmit="return confirm(\'حذف شود؟ (ثبت‌نام‌ها و حضورها هم پاک می‌شود)\')" style="display:inline"><input type="hidden" name="kind" value="students"><input type="hidden" name="id" value="{r["id"]}"><button class="btn btn-danger" type="submit">حذف</button></form>'
            body_rows.append([escape(r["student_code"]), escape(r["full_name"]), del_btn])
        self.send_page("دانش‌آموزان", self._simple_admin_table("دانش‌آموزان", ["کد", "نام"], body_rows), user=self.current_user())

    def route_admin_classes(self, m):
        self.require_admin()
        with dbmod.connect(self.cfg.db_path) as conn:
            rows = conn.execute("""SELECT c.id, c.title, t.full_name AS teacher_name,
                                          (SELECT COUNT(*) FROM enrollments e WHERE e.class_id=c.id) AS cnt
                                   FROM classes c JOIN teachers t ON t.id=c.teacher_id ORDER BY c.id""").fetchall()
        body_rows = []
        for r in rows:
            del_btn = f'<form method="post" action="/admin/delete" onsubmit="return confirm(\'کلاس حذف شود؟\')" style="display:inline"><input type="hidden" name="kind" value="classes"><input type="hidden" name="id" value="{r["id"]}"><button class="btn btn-danger" type="submit">حذف</button></form>'
            body_rows.append([escape(r["title"]), escape(r["teacher_name"]), str(r["cnt"]), del_btn])
        self.send_page("کلاس‌ها", self._simple_admin_table("کلاس‌ها", ["عنوان", "دبیر", "دانش‌آموز"], body_rows), user=self.current_user())

    def route_admin_delete(self, m):
        self.require_admin()
        form = self._read_form()
        kind, rid = form.get("kind", ""), form.get("id", "0")
        try:
            rid = int(rid)
        except ValueError:
            raise Redirect("/")
        me = self.current_user()
        with dbmod.connect(self.cfg.db_path) as conn:
            if kind == "teachers":
                if rid == me["id"]:
                    pass  # خودت را حذف نکن
                elif conn.execute("SELECT COUNT(*) c FROM teachers WHERE role='admin'").fetchone()["c"] <= 1 and \
                     conn.execute("SELECT role FROM teachers WHERE id=?", (rid,)).fetchone()["role"] == "admin":
                    pass  # آخرین ادمین
                else:
                    conn.execute("DELETE FROM teachers WHERE id=?", (rid,))
            elif kind == "students":
                conn.execute("DELETE FROM students WHERE id=?", (rid,))
            elif kind == "classes":
                conn.execute("DELETE FROM classes WHERE id=?", (rid,))
            conn.commit()
        raise Redirect({"teachers": "/admin/teachers", "students": "/admin/students", "classes": "/admin/classes"}.get(kind, "/"))


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    cfg = Config()
    dbmod.init_db(cfg.db_path)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.config = cfg  # type: ignore[attr-defined]
    return httpd
