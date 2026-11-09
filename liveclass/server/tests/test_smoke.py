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

    def test_board_flow(self):
        admin_cookie = self.login("admin", "Admin123!")
        teacher_cookie = self.login("teacher1", "Teach123!")

        # کلاس + دانش‌آموز + توکن ورود
        r = self.client.post("/api/classes", json={"title": "علوم ششم", "teacher_id": 2},
                             cookies={"lc_session": admin_cookie})
        class_id = r.json()["class"]["id"]
        r = self.client.post(f"/api/classes/{class_id}/students",
                             json={"student_code": "2001", "full_name": "علی تستی"},
                             cookies={"lc_session": admin_cookie})
        entry_token = r.json().get("token") or self.client.get(
            f"/api/classes/{class_id}", cookies={"lc_session": admin_cookie}).json()["students"][0]["token"]

        r = self.client.post(f"/api/classes/{class_id}/start", cookies={"lc_session": teacher_cookie})
        session = r.json()["session"]
        session_id = session["id"]
        r = self.client.post("/api/student/enter", json={"token": entry_token})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["can_publish"])   # نفر اول → اجازه‌ی انتشار دارد
        self.client.cookies.clear()                # کوکی‌ها پاک می‌شود؛ دانش‌آموز کوکی ندارد

        # دبیر حالت تخته را باز می‌کند
        r = self.client.post(f"/api/sessions/{session_id}/board/state",
                             json={"open": True}, cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["open"])

        # دانش‌آموز یک ضربه روی تخته ثبت می‌کند
        stroke = {"id": "op-test-1", "t": "stroke", "tool": "pen", "color": "#ffffff",
                  "size": 3, "pts": [0.1, 0.1, 0.5, 0.5]}
        r = self.client.post(f"/api/sessions/{session_id}/board/op",
                             json={"token": entry_token, "op": stroke})
        self.assertEqual(r.status_code, 200, r.text)

        # دبیر نقطه را می‌بیند
        r = self.client.post(f"/api/sessions/{session_id}/board/fetch", json={},
                             cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["open"])
        self.assertEqual(len(data["ops"]), 1)
        self.assertEqual(data["ops"][0]["id"], "op-test-1")

        # دانش‌آموز نمی‌تواند تخته را پاک کند
        r = self.client.post(f"/api/sessions/{session_id}/board/clear",
                             json={"token": entry_token})
        self.assertEqual(r.status_code, 403)

        # دبیر پاک می‌کند
        r = self.client.post(f"/api/sessions/{session_id}/board/clear", json={},
                             cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/api/sessions/{session_id}/board/fetch", json={},
                             cookies={"lc_session": teacher_cookie})
        ops = r.json()["ops"]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["t"], "clear")

    @staticmethod
    def make_pdf() -> bytes:
        content = b"BT /F1 24 Tf 100 700 Td (LiveClass Test) Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        out = b"%PDF-1.4\n"
        offsets = []
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n" + b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF"
        return out

    def test_password_and_preferences(self):
        admin_cookie = self.login("admin", "Admin123!")
        # تغییر رمز توسط خود کاربر
        cookie = self.login("teacher1", "Teach123!")
        r = self.client.post("/api/me/password",
                             json={"current_password": "Teach123!", "new_password": "NewTeach456!"},
                             cookies={"lc_session": cookie})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post("/api/auth/login", json={"username": "teacher1", "password": "Teach123!"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/auth/login", json={"username": "teacher1", "password": "NewTeach456!"})
        self.assertEqual(r.status_code, 200)
        new_cookie = r.cookies.get("lc_session")
        # رمز را برگردان (برای تست‌های دیگر)
        r = self.client.post("/api/me/password",
                             json={"current_password": "NewTeach456!", "new_password": "Teach123!"},
                             cookies={"lc_session": new_cookie})
        self.assertEqual(r.status_code, 200)
        # رمز فعلی اشتباه
        r = self.client.post("/api/me/password",
                             json={"current_password": "WRONG", "new_password": "Whatever123!"},
                             cookies={"lc_session": cookie})
        self.assertEqual(r.status_code, 400)

        # ترجیحات کیفیت ویدیو
        r = self.client.put("/api/me/preferences",
                            json={"video_preset": "quality", "video_fps": 24},
                            cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/me/preferences", cookies={"lc_session": admin_cookie})
        self.assertEqual(r.json()["video_preset"], "quality")
        self.assertEqual(r.json()["video_fps"], 24)
        r = self.client.put("/api/me/preferences",
                            json={"video_preset": "quality", "video_fps": 18},
                            cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/me/preferences",
                            json={"video_preset": "ultra", "video_fps": 24},
                            cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 400)
        # برگرداندن پیش‌فرض
        self.client.put("/api/me/preferences", json={"video_preset": "optimized", "video_fps": 15},
                        cookies={"lc_session": admin_cookie})

    def test_guest_access_and_backup(self):
        admin_cookie = self.login("admin", "Admin123!")
        # ادمین یک کاربر مهمان می‌سازد
        r = self.client.post("/api/users",
                             json={"username": "guest1", "password": "Guest123!",
                                   "full_name": "مهمان اول", "role": "guest"},
                             cookies={"lc_session": admin_cookie})
        self.assertIn(r.status_code, (200, 409), r.text)
        guest_cookie = self.login("guest1", "Guest123!")
        # مهمان اول هیچ کلاسی نمی‌بیند
        self.client.cookies.clear()
        r = self.client.get("/api/my/classes", cookies={"lc_session": guest_cookie})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["classes"], [])
        # مهمان نمی‌تواند کلاس بسازد
        r = self.client.post("/api/classes", json={"title": "کلاس مهمان"},
                             cookies={"lc_session": guest_cookie})
        self.assertEqual(r.status_code, 403)

        # ادمین کلاس می‌سازد و به مهمان دسترسی می‌دهد
        r = self.client.post("/api/classes", json={"title": "کلاس دسترسی مهمان", "teacher_id": 2},
                             cookies={"lc_session": admin_cookie})
        class_id = r.json()["class"]["id"]
        r = self.client.post(f"/api/classes/{class_id}/access", json={"username": "guest1"},
                             cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200, r.text)
        # حالا مهمان کلاس را می‌بیند و می‌تواند جلسه را شروع کند
        r = self.client.get("/api/my/classes", cookies={"lc_session": guest_cookie})
        titles = [c["title"] for c in r.json()["classes"]]
        self.assertIn("کلاس دسترسی مهمان", titles)
        r = self.client.post(f"/api/classes/{class_id}/start", cookies={"lc_session": guest_cookie})
        self.assertEqual(r.status_code, 200, r.text)
        session_id = r.json()["session"]["id"]
        # اما مهمان نمی‌تواند دسترسی‌ها را مدیریت کند
        r = self.client.post(f"/api/classes/{class_id}/access", json={"username": "teacher1"},
                             cookies={"lc_session": guest_cookie})
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/api/sessions/{session_id}/end", cookies={"lc_session": guest_cookie})
        self.assertEqual(r.status_code, 200)
        # ادمین دسترسی را می‌گیرد
        r = self.client.get(f"/api/classes/{class_id}/access", cookies={"lc_session": admin_cookie})
        grant_id = r.json()["grants"][0]["id"]
        r = self.client.delete(f"/api/classes/{class_id}/access/{grant_id}",
                               cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/api/classes/{class_id}/start", cookies={"lc_session": guest_cookie})
        self.assertEqual(r.status_code, 403)
        # ادمین رمز مهمان را ریست می‌کند
        users = self.client.get("/api/users", cookies={"lc_session": admin_cookie}).json()["users"]
        guest_id = next(u["id"] for u in users if u["username"] == "guest1")
        r = self.client.put(f"/api/users/{guest_id}/password", json={"password": "Guest456!"},
                            cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/auth/login", json={"username": "guest1", "password": "Guest456!"})
        self.assertEqual(r.status_code, 200)

        # بکاپ کامل: باید zip باشد و backup.json داخلش
        r = self.client.get("/api/admin/backup", cookies={"lc_session": admin_cookie})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content[:2] == b"PK")
        import zipfile, io as _io, json as _json
        zf = zipfile.ZipFile(_io.BytesIO(r.content))
        self.assertIn("backup.json", zf.namelist())
        self.assertIn("liveclass.db", zf.namelist())
        dump = _json.loads(zf.read("backup.json").decode("utf-8"))
        self.assertIn("users", dump["tables"])
        self.assertGreaterEqual(len(dump["tables"]["classes"]), 1)
        # دانش‌آموز عادی (کوکی معلم) نمی‌تواند بکاپ بگیرد
        r = self.client.get("/api/admin/backup", cookies={"lc_session": self.login("teacher1", "Teach123!")})
        self.assertEqual(r.status_code, 403)

    def test_pdf_on_board(self):
        admin_cookie = self.login("admin", "Admin123!")
        teacher_cookie = self.login("teacher1", "Teach123!")
        r = self.client.post("/api/classes", json={"title": "کتاب علوم", "teacher_id": 2},
                             cookies={"lc_session": admin_cookie})
        class_id = r.json()["class"]["id"]
        r = self.client.post(f"/api/classes/{class_id}/students",
                             json={"student_code": "3001", "full_name": "کتابی سارا"},
                             cookies={"lc_session": admin_cookie})
        entry_token = self.client.get(f"/api/classes/{class_id}",
                                      cookies={"lc_session": admin_cookie}).json()["students"][0]["token"]
        r = self.client.post(f"/api/classes/{class_id}/start", cookies={"lc_session": teacher_cookie})
        session_id = r.json()["session"]["id"]
        # PDF خراب رد می‌شود
        r = self.client.post(f"/api/sessions/{session_id}/board/pdf",
                             files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
                             cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 400)
        # PDF سالم بارگذاری می‌شود
        r = self.client.post(f"/api/sessions/{session_id}/board/pdf",
                             files={"file": ("olom.pdf", self.make_pdf(), "application/pdf")},
                             cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200, r.text)
        pdf_id = r.json()["pdf"]["id"]
        self.assertEqual(r.json()["pdf"]["page"], 1)
        # صفحه عوض می‌شود
        r = self.client.post(f"/api/sessions/{session_id}/board/pdf/page", json={"page": 3},
                             cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200)
        # معلم وضعیت تخته را می‌بیند
        r = self.client.post(f"/api/sessions/{session_id}/board/fetch", json={},
                             cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.json()["pdf"]["page"], 3)
        self.assertEqual(r.json()["pdf"]["name"], "olom.pdf")
        # دانش‌آموز همان کلاس می‌تواند PDF را دانلود کند
        self.client.cookies.clear()
        r = self.client.get(f"/api/board/pdf/{pdf_id}?token={entry_token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF-"))
        # توکن بی‌ربط رد می‌شود
        r = self.client.get(f"/api/board/pdf/{pdf_id}?token=deadbeef")
        self.assertEqual(r.status_code, 403)
        # دانش‌آموز نمی‌تواند صفحه عوض کند
        r = self.client.post(f"/api/sessions/{session_id}/board/pdf/page",
                             json={"page": 99, "token": entry_token})
        self.assertEqual(r.status_code, 403)
        # دبیر می‌بندد
        r = self.client.post(f"/api/sessions/{session_id}/board/pdf/close", json={},
                             cookies={"lc_session": teacher_cookie})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/api/sessions/{session_id}/board/fetch", json={},
                             cookies={"lc_session": teacher_cookie})
        self.assertIsNone(r.json()["pdf"])
        self.client.post(f"/api/sessions/{session_id}/end", cookies={"lc_session": teacher_cookie})

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
