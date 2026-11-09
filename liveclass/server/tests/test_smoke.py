# -*- coding: utf-8 -*-
"""تست یکپارچه‌ی مسیر کامل: لاگین → ساخت کلاس/دانش‌آموز → شروع جلسه → ورود دانش‌آموز → حضور → پایان → CSV"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="liveclass-test-")
os.environ["LIVECLASS_DB"] = str(Path(TMP) / "test.db")
os.environ["LIVECLASS_SECRET"] = "test-secret"
os.environ["LIVECLASS_LIVEKIT_URL"] = "ws://127.0.0.1:7880"
os.environ["LIVECLASS_LIVEKIT_MOCK"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import create_app  # noqa: E402
from app import db as dbmod  # noqa: E402
from app.security import hash_password  # noqa: E402


def seed():
    dbmod.init_db()
    with dbmod.connect() as conn:
        conn.execute("INSERT INTO users(username, full_name, password_hash, role) VALUES ('admin','مدیر',?, 'admin')",
                     (hash_password("Admin123!"),))
        conn.execute("INSERT INTO users(username, full_name, password_hash, role) VALUES ('teacher1','دبیر یک',?, 'teacher')",
                     (hash_password("Teach123!"),))
        conn.commit()


class TestFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed()
        cls.client = TestClient(create_app())

    def login(self, username, password):
        r = self.client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return r.cookies.get("lc_session")

    def test_full_flow(self):
        # --- لاگین ادمین و ساخت کلاس برای دبیر ---
        admin_cookie = self.login("admin", "Admin123!")
        r = self.client.get("/api/teachers", cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200)
        teacher_id = next(t["id"] for t in r.json()["teachers"] if t["username"] == "teacher1")

        r = self.client.post("/api/classes", json={"title": "ریاضی هفتم", "teacher_id": teacher_id},
                             cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200, r.text)
        class_id = r.json()["class"]["id"]

        # --- افزودن دانش‌آموز با پنل ---
        r = self.client.post(f"/api/classes/{class_id}/students",
                             json={"student_code": "1001", "full_name": "سارا تستی"},
                             cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200, r.text)

        # --- توکن ورود دانش‌آموز ساخته می‌شود ---
        r = self.client.get(f"/api/classes/{class_id}", cookies={"lc_session": admin_cookie})
        students = r.json()["students"]
        self.assertEqual(len(students), 1)
        # توکن را خودمان از دیتابیس می‌خوانیم (API توکن را در لیست نمایش می‌دهد)
        entry_token = students[0].get("token")
        self.assertTrue(entry_token)
        print("entry token:", entry_token)

        # --- دبیر لاگین می‌کند؛ قبل از شروع جلسه، ورود دانش‌آموز رد می‌شود ---
        teacher_cookie = self.login("teacher1", "Teach123!")
        r = self.client.post("/api/student/enter", json={"token": entry_token})
        self.assertEqual(r.status_code, 409)

        # --- شروع جلسه ---
        r = self.client.post(f"/api/classes/{class_id}/start", cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200, r.text)
        session = r.json()["session"]
        self.assertEqual(session["status"], "open")
        session_id = session["id"]

        # توکن معلم برای اتاق
        r = self.client.get(f"/api/sessions/{session_id}/token", cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["token"])

        # --- دانش‌آموز وارد می‌شود ---
        r = self.client.post("/api/student/enter", json={"token": entry_token})
        self.assertEqual(r.status_code, 200, r.text)
        enter = r.json()
        self.assertEqual(enter["session_id"], session_id)
        self.assertEqual(enter["student_name"], "سارا تستی")

        # heartbeat
        r = self.client.post("/api/student/heartbeat", json={"token": entry_token, "session_id": session_id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["updated"], 1)

        # --- حضور را ببینیم ---
        r = self.client.get(f"/api/sessions/{session_id}/attendance", cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200)
        att = r.json()["attendance"]
        self.assertEqual(len(att), 1)
        self.assertEqual(att[0]["student_code"], "1001")
        self.assertTrue(att[0]["online"])

        # --- پایان جلسه ---
        r = self.client.post(f"/api/sessions/{session_id}/end", cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["session"]["status"], "closed")

        # بعد از بسته‌شدن، ورود دانش‌آموز رد می‌شود
        r = self.client.post("/api/student/enter", json={"token": entry_token})
        self.assertEqual(r.status_code, 409)

        # CSV حضور
        r = self.client.get(f"/api/sessions/{session_id}/attendance.csv", cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200)
        self.assertIn("سارا تستی", r.text)

    def test_brand_admin_only(self):
        admin_cookie = self.login("admin", "Admin123!")
        teacher_cookie = self.login("teacher1", "Teach123!")

        r = self.client.get("/api/brand")
        self.assertEqual(r.status_code, 200)

        r = self.client.put("/api/admin/brand",
                            data={"site_name": "هک", "primary_color": "#123456"},
                            cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 403)

        r = self.client.put("/api/admin/brand",
                            data={"site_name": "آکادمی ما", "tagline": "بهترین کلاس", "primary_color": "#0ea5e9"},
                            cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["brand"]["site_name"], "آکادمی ما")


if __name__ == "__main__":
    unittest.main()
