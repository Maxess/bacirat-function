# -*- coding: utf-8 -*-
"""لایه‌ی پایگاه داده (SQLite) برای ماژول مدیریت کلاس.

مدل داده:
  teachers / students / classes / enrollments   — هویت و ثبت‌نام
  entry_tokens    — لینک پایدار هر دانش‌آموز برای هر کلاس (/enter?token=...)
  sessions        — هر بار که دبیر «شروع کلاس» می‌زند یک نشست ساخته می‌شود
  attendance      — ورود واقعی هر دانش‌آموز در هر نشست (ویزیت /enter) + زمان خروج (بستن کلاس)
"""
import os
import secrets
import sqlite3
import string
from datetime import datetime, timezone

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS teachers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,               -- pbkdf2$iter$salt_hex$hash_hex
  role TEXT NOT NULL DEFAULT 'teacher',      -- teacher | admin
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS students (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  student_code TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS classes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT UNIQUE NOT NULL,
  teacher_id INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS enrollments (
  class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  PRIMARY KEY (class_id, student_id)
);

CREATE TABLE IF NOT EXISTS entry_tokens (
  class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  token TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (class_id, student_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
  meet_id INTEGER,                          -- شناسه‌ی اتاق در PersianMeet
  teacher_pid TEXT,                         -- PID دبیر در این نشست
  admin_pass TEXT,                          -- رمز مدیر همین اتاق (برای Join دبیر)
  status TEXT NOT NULL DEFAULT 'open',       -- open | closed
  started_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE TABLE IF NOT EXISTS attendance (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  pid TEXT NOT NULL,
  user_room_id TEXT NOT NULL DEFAULT '',   -- همان ID داخل UserRoom محصول (برای تطبیق دقیق با Connection)
  entered_at TEXT NOT NULL,
  exited_at TEXT,
  -- اسنپ‌شات دقیق حضور که لحظه‌ی بستن کلاس (قبل از حذف اتاق) از جدول Connection محصول گرفته می‌شود.
  -- اگر DeleteMeet رکوردهای Connection را پاک کند، این مقادیر منبع نهایی خروجی CSV/صفحه هستند.
  snap_seconds INTEGER,
  snap_first_join INTEGER NOT NULL DEFAULT 0,
  snap_last_exit INTEGER NOT NULL DEFAULT 0,
  snap_intervals INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (session_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_attend_session ON attendance(session_id);
CREATE INDEX IF NOT EXISTS idx_enroll_class ON enrollments(class_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(attendance)")}
    if "user_room_id" not in cols:
        conn.execute("ALTER TABLE attendance ADD COLUMN user_room_id TEXT NOT NULL DEFAULT ''")
    for col, ddl in [("snap_seconds", "ALTER TABLE attendance ADD COLUMN snap_seconds INTEGER"),
                     ("snap_first_join", "ALTER TABLE attendance ADD COLUMN snap_first_join INTEGER NOT NULL DEFAULT 0"),
                     ("snap_last_exit", "ALTER TABLE attendance ADD COLUMN snap_last_exit INTEGER NOT NULL DEFAULT 0"),
                     ("snap_intervals", "ALTER TABLE attendance ADD COLUMN snap_intervals INTEGER NOT NULL DEFAULT 0")]:
        if col not in cols:
            conn.execute(ddl)


def init_db(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def new_token(nbytes: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(nbytes * 2))


def ensure_entry_token(conn: sqlite3.Connection, class_id: int, student_id: int) -> str:
    row = conn.execute("SELECT token FROM entry_tokens WHERE class_id=? AND student_id=?",
                       (class_id, student_id)).fetchone()
    if row:
        return row["token"]
    token = new_token()
    conn.execute("INSERT INTO entry_tokens(class_id, student_id, token) VALUES (?,?,?)",
                 (class_id, student_id, token))
    return token
