# -*- coding: utf-8 -*-
"""لایه‌ی دیتابیس SQLite برای لایوکلاس (LiveClass) — تنها وابستگی: stdlib پایتون."""
from __future__ import annotations

import os
import sqlite3
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,                 -- pbkdf2$iter$salt_hex$hash_hex
  role TEXT NOT NULL DEFAULT 'teacher',        -- teacher | admin
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
  teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
  room_name TEXT UNIQUE NOT NULL,              -- نام اتاق در LiveKit
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',         -- open | closed
  started_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE TABLE IF NOT EXISTS attendance (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  joined_at TEXT NOT NULL,
  left_at TEXT,
  last_heartbeat TEXT,
  seconds INTEGER NOT NULL DEFAULT 0,          -- تجمیع شده (برای گزارش بعد از بستن)
  PRIMARY KEY (session_id, student_id)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_ops (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  op_id TEXT UNIQUE NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  op TEXT NOT NULL,                          -- JSON: {t:'stroke', tool, color, size, pts:[...]} | {t:'clear'}
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_enroll_class ON enrollments(class_id);
CREATE INDEX IF NOT EXISTS idx_attend_session ON attendance(session_id);
CREATE INDEX IF NOT EXISTS idx_board_session ON board_ops(session_id, seq);
"""

DEFAULT_SETTINGS = {
    "site_name": "LiveClass",
    "site_name_short": "LiveClass",
    "tagline": "کلاس آنلاین با تخته تعاملی",
    "primary_color": "#2563eb",
    "has_logo": "0",
    "has_icon": "0",
    "brand_version": "1",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def db_path() -> str:
    custom = os.environ.get("LIVECLASS_DB")
    if custom:
        return custom
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "liveclass.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    # تنظیمات بهینه برای سرورهای ضعیف: WAL + همگام‌سازی عادی + کش حافظه
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA cache_size=-8000")      # ~8MB کش صفحه
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: str | None = None) -> str:
    path = path or db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "board_open" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN board_open INTEGER NOT NULL DEFAULT 0")
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        conn.commit()
    return path


def new_token(nbytes: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(nbytes * 2))


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None
