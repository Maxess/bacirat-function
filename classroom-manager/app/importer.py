# -*- coding: utf-8 -*-
"""ایمپورت داده‌ها از CSV (با پشتیبانی از UTF-8 BOM و هدر فارسی/انگلیسی)."""
import csv
import io
import re
import sqlite3

from .auth import hash_password
from .db import connect

_KEY_ALIASES = {
    "username": "username", "user": "username", "نام‌کاربری": "username", "نام کاربری": "username", "کاربری": "username",
    "password": "password", "pass": "password", "رمز": "password", "رمزعبور": "password", "رمز عبور": "password",
    "full_name": "full_name", "fullname": "full_name", "name": "full_name", "نام": "full_name", "نام‌ونام‌خانوادگی": "full_name", "نام و نام خانوادگی": "full_name",
    "class_title": "class_title", "class": "class_title", "title": "class_title", "کلاس": "class_title", "نام کلاس": "class_title", "عنوان کلاس": "class_title",
    "teacher_username": "teacher_username", "teacher": "teacher_username", "دبیر": "teacher_username", "نام‌کاربری دبیر": "teacher_username",
    "student_code": "student_code", "code": "student_code", "کد": "student_code", "کد دانش‌آموز": "student_code", "کد دانش آموز": "student_code", "کدملی": "student_code",
}


def _norm_key(key: str) -> str:
    k = (key or "").strip().lower().replace("‌", " ")
    k = re.sub(r"\s+", " ", k)
    return _KEY_ALIASES.get(k, k)


def _rows(text: str):
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    for raw in reader:
        row = {_norm_key(k): (v or "").strip() for k, v in raw.items()}
        yield row


def import_teachers(db_path: str, text: str) -> dict:
    """teachers.csv → username,password,full_name — موجود را آپدیت می‌کند (رمز جدید اگر داده شده باشد)."""
    report = {"created": 0, "updated": 0, "errors": []}
    with connect(db_path) as conn:
        for i, row in enumerate(_rows(text), start=2):
            username, password, full_name = row.get("username"), row.get("password"), row.get("full_name")
            if not username or not full_name:
                report["errors"].append({"line": i, "reason": "username/full_name خالی است"})
                continue
            cur = conn.execute("SELECT id FROM teachers WHERE username=?", (username,))
            existing = cur.fetchone()
            if existing:
                if password:
                    conn.execute("UPDATE teachers SET full_name=?, password_hash=? WHERE id=?",
                                 (full_name, hash_password(password), existing["id"]))
                else:
                    conn.execute("UPDATE teachers SET full_name=? WHERE id=?", (full_name, existing["id"]))
                report["updated"] += 1
            else:
                if not password:
                    report["errors"].append({"line": i, "reason": "رمز برای کاربر جدید الزامی است"})
                    continue
                conn.execute("INSERT INTO teachers(username, full_name, password_hash, role) VALUES (?,?,?,'teacher')",
                             (username, full_name, hash_password(password)))
                report["created"] += 1
    return report


def import_classes(db_path: str, text: str) -> dict:
    """classes.csv → class_title,teacher_username"""
    report = {"created": 0, "updated": 0, "errors": []}
    with connect(db_path) as conn:
        for i, row in enumerate(_rows(text), start=2):
            title, teacher_username = row.get("class_title"), row.get("teacher_username")
            if not title or not teacher_username:
                report["errors"].append({"line": i, "reason": "class_title/teacher_username خالی است"})
                continue
            t = conn.execute("SELECT id FROM teachers WHERE username=?", (teacher_username,)).fetchone()
            if not t:
                report["errors"].append({"line": i, "reason": f"دبیر با نام کاربری «{teacher_username}» وجود ندارد (اول teachers.csv را ایمپورت کنید)"})
                continue
            cur = conn.execute("SELECT id FROM classes WHERE title=?", (title,))
            existing = cur.fetchone()
            if existing:
                conn.execute("UPDATE classes SET teacher_id=? WHERE id=?", (t["id"], existing["id"]))
                report["updated"] += 1
            else:
                conn.execute("INSERT INTO classes(title, teacher_id) VALUES (?,?)", (title, t["id"]))
                report["created"] += 1
    return report


def import_students(db_path: str, text: str) -> dict:
    """students.csv → student_code,full_name,class_title — دانش‌آموز را می‌سازد و در کلاس ثبت‌نام می‌کند."""
    report = {"created": 0, "enrolled": 0, "already_enrolled": 0, "errors": []}
    with connect(db_path) as conn:
        for i, row in enumerate(_rows(text), start=2):
            code, full_name, class_title = row.get("student_code"), row.get("full_name"), row.get("class_title")
            if not code or not full_name:
                report["errors"].append({"line": i, "reason": "student_code/full_name خالی است"})
                continue
            cls = None
            if class_title:
                cls = conn.execute("SELECT id FROM classes WHERE title=?", (class_title,)).fetchone()
                if not cls:
                    report["errors"].append({"line": i, "reason": f"کلاس «{class_title}» پیدا نشد (اول classes.csv را ایمپورت کنید)"})
                    continue
            st = conn.execute("SELECT id FROM students WHERE student_code=?", (code,)).fetchone()
            if st:
                student_id = st["id"]
                conn.execute("UPDATE students SET full_name=? WHERE id=?", (full_name, student_id))
            else:
                cur = conn.execute("INSERT INTO students(student_code, full_name) VALUES (?,?)", (code, full_name))
                student_id = cur.lastrowid
                report["created"] += 1
            if cls:
                cur = conn.execute("INSERT OR IGNORE INTO enrollments(class_id, student_id) VALUES (?,?)",
                                   (cls["id"], student_id))
                if cur.rowcount:
                    report["enrolled"] += 1
                else:
                    report["already_enrolled"] += 1
    return report
