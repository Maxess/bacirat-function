# -*- coding: utf-8 -*-
"""خواندن *فقط-خواندنی* پایگاه داده‌ی خود محصول PersianMeet (sqlite پیش‌فرض ./test.db).

جدول‌های مورد استفاده:
  UserRoom   — هر بار Join از API یک رکورد می‌سازد: PID (برای کاربر) و ID (داخلی)
  Connection — هر اتصال WebSocket واقعی یک رکورد: Status = 'join' (داخل کلاس) یا 'left' (خارج شده)
               JoinDate / ExitDate به‌صورت unix-seconds ذخیره می‌شوند.

اگر فایل وجود نداشته باشد یا خوانده نشود، همه‌ی توابع None برمی‌گردانند و
کد فراخوان به حالت تقریبی (بر اساس entered_at خودمان) عقب می‌نشیند.
"""
import os
import sqlite3
import time


def _db_path() -> str | None:
    path = os.environ.get("PM_PRODUCT_DB")
    if not path:
        install_dir = os.environ.get("PERSIANMEET_INSTALL_DIR", "/opt/persianmeet")
        path = os.path.join(install_dir, "test.db")
    return path if os.path.exists(path) else None


def available() -> bool:
    path = _db_path()
    if not path:
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("SELECT 1 FROM Connection LIMIT 1")
        return True
    except Exception:
        return False


def _ro():
    path = _db_path()
    if not path:
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def user_room_id_for_pid(pid: str) -> str | None:
    conn = _ro()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT ID FROM UserRoom WHERE PID=? LIMIT 1", (pid,)).fetchone()
        return row["ID"] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def intervals_for_rooms(meet_id: int, user_room_ids: list[str]) -> dict[str, list[tuple[int, int]]] | None:
    """→ {user_room_id: [(join_ts, exit_ts|now), ...]} — بازه‌های حضور در آن اتاق."""
    ids = [i for i in user_room_ids if i]
    if not ids:
        return {}
    conn = _ro()
    if conn is None:
        return None
    now = int(time.time())
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT UserRoomID, JoinDate, ExitDate, Status FROM Connection "
            f"WHERE MeetInfoID=? AND UserRoomID IN ({placeholders})",
            [meet_id, *ids],
        ).fetchall()
        out: dict[str, list[tuple[int, int]]] = {i: [] for i in ids}
        for r in rows:
            start = int(r["JoinDate"] or 0)
            exit_ts = int(r["ExitDate"] or 0)
            if r["Status"] == "left" and exit_ts > 0:
                end = exit_ts
            else:  # هنوز داخل کلاس است
                end = now
            if start > 0:
                out.setdefault(r["UserRoomID"], []).append((start, max(start, end)))
        return out
    except Exception:
        return None
    finally:
        conn.close()


def online_room_ids(meet_id: int) -> set[str] | None:
    """شناسه‌ی داخلی کسانی که فعلاً وسط کلاس هستند (Connection با Status='join')."""
    conn = _ro()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT DISTINCT UserRoomID FROM Connection WHERE MeetInfoID=? AND Status='join'",
            (meet_id,),
        ).fetchall()
        return {r["UserRoomID"] for r in rows}
    except Exception:
        return None
    finally:
        conn.close()
