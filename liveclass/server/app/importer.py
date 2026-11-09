# -*- coding: utf-8 -*-
"""ایمپورت CSV — نسخه‌ی مستقل برای LiveClass (دبیران/کلاس‌ها/دانش‌آموزان)."""
from __future__ import annotations

import csv
import io
from . import db as dbmod
from .security import hash_password

REQUIRED = {
    "teachers": ["username", "password", "full_name"],
    "classes": ["class_title", "teacher_username"],
    "students": ["student_code", "full_name", "class_title"],
}
ALIASES = {
    "نام کاربری": "username", "نام‌کاربری": "username", "user": "username", "user_name": "username",
    "رمز": "password", "پسورد": "password", "pass": "password",
    "نام و نام خانوادگی": "full_name", "نام": "full_name", "name": "full_name", "fullname": "full_name",
    "کلاس": "class_title", "عنوان کلاس": "class_title", "class": "class_title", "title": "class_title",
    "کلاس استاد": "class_title",
    "نام کاربری دبیر": "teacher_username", "دبیر": "teacher_username", "teacher": "teacher_username",
    "کد دانش‌آموز": "student_code", "کد": "student_code", "code": "student_code", "student_id": "student_code",
}


def _rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for raw in reader:
        row = {}
        for k, v in raw.items():
            key = (k or "").strip().lower().replace("-", "_")
            key = ALIASES.get((k or "").strip(), ALIASES.get((k or "").strip().lower(), key))
            row[key] = (v or "").strip()
        out.append(row)
    return out


def import_teachers(text: str, db_path: str | None = None) -> dict:
    created = skipped = 0
    errors = []
    with dbmod.connect(db_path) as conn:
        for i, row in enumerate(_rows(text), start=2):
            u, p, name = row.get("username"), row.get("password"), row.get("full_name")
            if not u or not p or not name:
                errors.append({"line": i, "reason": "ستون‌های username/password/full_name الزامی است"})
                continue
            exists = conn.execute("SELECT id FROM users WHERE username=?", (u,)).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute("INSERT INTO users(username, full_name, password_hash, role) VALUES (?,?,?, 'teacher')",
                         (u, name, hash_password(p)))
            created += 1
        conn.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


def import_classes(text: str, db_path: str | None = None) -> dict:
    created = skipped = 0
    errors = []
    with dbmod.connect(db_path) as conn:
        for i, row in enumerate(_rows(text), start=2):
            title, teacher_username = row.get("class_title"), row.get("teacher_username")
            if not title or not teacher_username:
                errors.append({"line": i, "reason": "class_title و teacher_username الزامی است"})
                continue
            teacher = conn.execute("SELECT id FROM users WHERE username=?", (teacher_username,)).fetchone()
            if not teacher:
                errors.append({"line": i, "reason": f"دبیری با نام کاربری {teacher_username} وجود ندارد"})
                continue
            exists = conn.execute("SELECT id FROM classes WHERE title=?", (title,)).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute("INSERT INTO classes(title, teacher_id) VALUES (?,?)", (title, teacher["id"]))
            created += 1
        conn.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


def import_students(text: str, db_path: str | None = None) -> dict:
    created = enrolled = skipped = 0
    errors = []
    with dbmod.connect(db_path) as conn:
        for i, row in enumerate(_rows(text), start=2):
            code, name, class_title = row.get("student_code"), row.get("full_name"), row.get("class_title")
            if not code or not name or not class_title:
                errors.append({"line": i, "reason": "student_code/full_name/class_title الزامی است"})
                continue
            cls = conn.execute("SELECT id FROM classes WHERE title=?", (class_title,)).fetchone()
            if not cls:
                errors.append({"line": i, "reason": f"کلاسی با عنوان {class_title} وجود ندارد"})
                continue
            student = conn.execute("SELECT id FROM students WHERE student_code=?", (code,)).fetchone()
            if student:
                student_id = student["id"]
            else:
                cur = conn.execute("INSERT INTO students(student_code, full_name) VALUES (?,?)", (code, name))
                student_id = cur.lastrowid
                created += 1
            exists = conn.execute("SELECT 1 FROM enrollments WHERE class_id=? AND student_id=?",
                                  (cls["id"], student_id)).fetchone()
            if exists:
                skipped += 1
            else:
                conn.execute("INSERT INTO enrollments(class_id, student_id) VALUES (?,?)", (cls["id"], student_id))
                enrolled += 1
            # توکن ورود اختصاصی هم ساخته شود (اگر از قبل دارد، همان می‌ماند)
            existing_token = conn.execute("SELECT 1 FROM entry_tokens WHERE class_id=? AND student_id=?",
                                          (cls["id"], student_id)).fetchone()
            if not existing_token:
                conn.execute("INSERT INTO entry_tokens(class_id, student_id, token) VALUES (?,?,?)",
                             (cls["id"], student_id, dbmod.new_token()))
        conn.commit()
    return {"created": created, "enrolled": enrolled, "already_enrolled": skipped, "errors": errors}
