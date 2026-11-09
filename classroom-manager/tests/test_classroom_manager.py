#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""تست‌های واحد و یکپارچه (سرور PersianMeet موک می‌شود؛ بدون اینترنت)."""
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # classroom-manager/
REPO = ROOT.parent                                      # bacirat-function/
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from app import db as dbmod                      # noqa: E402
from app.auth import SessionCodec, hash_password, verify_password  # noqa: E402
from app.importer import import_teachers, import_classes, import_students  # noqa: E402
from app.web import make_server                    # noqa: E402

EXAMPLES = ROOT / "examples"


class MockPersianMeetHandler(BaseHTTPRequestHandler):
    """تقلید /api طبق مستندات رسمی + شمارنده‌ی فراخوانی‌ها."""
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        MockPersianMeetHandler.calls.append(payload)
        t = payload.get("Type")
        data = payload.get("Data") or {}
        if t == "panelUserList":
            # GUID ادمین آزمایشی: admin-guid-123 → ok ؛ بقیه → no
            if payload.get("Secret") == "admin-guid-123":
                resp = {"Status": "ok", "Count": 1, "Data": [{"UserName": "admin", "Role": 1}]}
            else:
                resp = {"Status": "no"}
        elif t == "CreateMeet":
            resp = {"Status": "ok", "ID": 777, "Url": "/room/777", "Title": data.get("Title"), "Message": ""}
        elif t == "Join":
            role = int(data.get("JoinRole", 0))
            name = data.get("userName", "")
            import hashlib as _hashlib
            pid = "tpid777" if role == 1 else "pid-" + _hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
            resp = {"Status": "ok", "PID": pid, "Message": "", "Url": f"/user/{pid}"}
        elif t == "DeleteMeet":
            resp = {"Status": "ok", "Message": ""}
        elif t == "GetMeetInfo":
            resp = {"Status": "ok", "ID": data.get("ID"), "UserNeedPass": 0, "AdminNeedPass": 1, "Title": "mock", "Message": ""}
        else:
            resp = {"Status": "error", "Message": "unknown"}
        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockServer:
    def __enter__(self):
        MockPersianMeetHandler.calls = []
        self.httpd = HTTPServer(("127.0.0.1", 0), MockPersianMeetHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        return self.url

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_request(method: str, url: str, body: bytes | None = None,
                 headers: dict | None = None, cookie: str | None = None):
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if cookie:
        req.add_header("Cookie", cookie)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(req, timeout=10)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "test.db")
        dbmod.init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()


class TestUnits(BaseCase):
    def test_password_roundtrip(self):
        h = hash_password("Secret123!")
        self.assertTrue(verify_password("Secret123!", h))
        self.assertFalse(verify_password("wrong", h))

    def test_session_codec(self):
        codec = SessionCodec("test-key")
        token = codec.encode(1, "admin")
        data = codec.decode(token)
        self.assertEqual(data["uid"], 1)
        self.assertIsNone(codec.decode(token + "x"))
        self.assertIsNone(codec.decode("garbage"))

    def test_csv_import_happy_path(self):
        r1 = import_teachers(self.db, (EXAMPLES / "teachers.csv").read_text("utf-8"))
        r2 = import_classes(self.db, (EXAMPLES / "classes.csv").read_text("utf-8"))
        r3 = import_students(self.db, (EXAMPLES / "students.csv").read_text("utf-8"))
        self.assertEqual(r1["created"], 3)
        self.assertEqual(r2["created"], 3)
        self.assertEqual(r3["created"], 8)
        self.assertEqual(r3["enrolled"], 8)
        # ایمپورت دوباره — نباید رکورد تکراری بسازد
        r3b = import_students(self.db, (EXAMPLES / "students.csv").read_text("utf-8"))
        self.assertEqual(r3b["created"], 0)
        self.assertEqual(r3b["already_enrolled"], 8)

    def test_csv_import_reports_errors(self):
        r = import_students(self.db, "student_code,full_name,class_title\n1001,,کلاس ناموجود\n,,\n")
        self.assertTrue(len(r["errors"]) >= 2)

    def test_csv_import_persian_headers(self):
        r = import_teachers(self.db, "نام‌کاربری,رمز,نام و نام خانوادگی\nt1,pass123,علی تست\n")
        self.assertEqual(r["created"], 1)


class TestHttpFlow(BaseCase):
    """مسیر کامل: ایمپورت → ورود ادمین → شروع کلاس → ورود دانش‌آموز → بستن → CSV"""

    def setUp(self):
        super().setUp()
        self.mock_ctx = MockServer()
        self.pm_url = self.mock_ctx.__enter__()
        self.tmp_env = {
            "PM_MANAGER_DB": self.db,
            "PM_MANAGER_SECRET": "test-secret-key",
            "PERSIANMEET_URL": self.pm_url,
            "PERSIANMEET_SECRET": "topsecret",
            "PM_MANAGER_CLOSE_STRATEGY": "delete",
        }
        self._old_env = {k: os.environ.get(k) for k in self.tmp_env}
        os.environ.update(self.tmp_env)

        self.server = make_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.mock_ctx.__exit__(None, None, None)
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def _login_admin(self) -> str:
        from app.web import Config
        cfg = Config()
        with dbmod.connect(self.db) as conn:
            conn.execute("INSERT INTO teachers(username, full_name, password_hash, role) VALUES ('admin','مدیر',?, 'admin')",
                         (hash_password("Admin123!"),))
            conn.commit()
        status, headers, _ = http_request(
            "POST", self.base + "/login",
            body=b"username=admin&password=Admin123!",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 302)
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(cookie.startswith("pm_session="))
        return cookie

    def test_full_class_lifecycle(self):
        cookie = self._login_admin()

        # داشبورد
        status, _, body = http_request("GET", self.base + "/", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("سلام".encode(), body)

        # ایمپورت CSV از طریق HTTP multipart (مسیر واقعی پنل ادمین)
        boundary = "----pmtest123"

        def multipart(field: str, filename: str, content: bytes) -> bytes:
            return (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                    f'Content-Type: text/csv\r\n\r\n').encode() + content + f"\r\n--{boundary}--\r\n".encode()

        for field, filename in (("teachers", "teachers.csv"), ("classes", "classes.csv"), ("students", "students.csv")):
            status, _, _ = http_request(
                "POST", self.base + "/admin/import",
                body=multipart(field, filename, (EXAMPLES / filename).read_bytes()),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                cookie=cookie)
            self.assertEqual(status, 200)

        # پیدا کردن کلاس و توکن دانش‌آموز از دیتابیس
        with dbmod.connect(self.db) as conn:
            cls = conn.execute("SELECT id FROM classes WHERE title='ریاضی دهم الف'").fetchone()
            student = conn.execute("SELECT id FROM students WHERE student_code='1001'").fetchone()
            token_row = conn.execute("SELECT token FROM entry_tokens WHERE class_id=? AND student_id=?",
                                     (cls["id"], student["id"])).fetchone()
        # توکن هنوز ساخته نشده — صفحه‌ی کلاس را باز کن تا توکن‌ها ساخته شوند
        status, _, _ = http_request("GET", f"{self.base}/class/{cls['id']}", cookie=cookie)
        self.assertEqual(status, 200)
        with dbmod.connect(self.db) as conn:
            token = conn.execute("SELECT token FROM entry_tokens WHERE class_id=? AND student_id=?",
                                 (cls["id"], student["id"])).fetchone()["token"]

        # قبل از شروع کلاس، /enter باید «کلاس فعال نیست» بدهد
        status, _, body = http_request("GET", f"{self.base}/enter?token={token}")
        self.assertEqual(status, 403)

        # شروع کلاس → اتاق در سرور موک ساخته می‌شود
        status, headers, _ = http_request("POST", f"{self.base}/class/{cls['id']}/start", cookie=cookie)
        self.assertEqual(status, 302)
        session_location = headers["Location"]
        self.assertRegex(session_location, r"/session/\d+")
        self.assertEqual(MockPersianMeetHandler.calls[0]["Type"], "CreateMeet")
        self.assertEqual(MockPersianMeetHandler.calls[0]["Data"]["Title"], "ریاضی دهم الف")

        # ورود دانش‌آموز از لینک پایدارش
        status, headers, _ = http_request("GET", f"{self.base}/enter?token={token}")
        self.assertEqual(status, 302)
        self.assertIn("/user/", headers["Location"])
        with dbmod.connect(self.db) as conn:
            att = conn.execute("SELECT entered_at FROM attendance").fetchone()
            self.assertIsNotNone(att["entered_at"])

        # کلیک دوم — همان PID قبلی برمی‌گردد (بدون ساخت اتاق جدید)
        first_location = headers["Location"]
        join_calls_before = sum(1 for c in MockPersianMeetHandler.calls if c["Type"] == "Join")
        status, headers, _ = http_request("GET", f"{self.base}/enter?token={token}")
        self.assertEqual(headers["Location"], first_location)
        self.assertEqual(sum(1 for c in MockPersianMeetHandler.calls if c["Type"] == "Join"), join_calls_before)

        # خروجی CSV حضور قبل از بستن
        session_id = session_location.rsplit("/", 1)[1]
        status, _, body = http_request("GET", f"{self.base}/session/{session_id}/attendance.csv", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("1001".encode(), body)
        self.assertIn("سارا کریمی".encode(), body)

        # بستن کلاس → DeleteMeet صدا زده می‌شود و همه خارج می‌شوند
        status, headers, _ = http_request("POST", f"{self.base}/session/{session_id}/close", cookie=cookie)
        self.assertEqual(status, 302)
        self.assertIn("DeleteMeet", [c["Type"] for c in MockPersianMeetHandler.calls])
        with dbmod.connect(self.db) as conn:
            row = conn.execute("SELECT status, closed_at FROM sessions WHERE id=?", (int(session_id),)).fetchone()
            self.assertEqual(row["status"], "closed")
            self.assertIsNotNone(row["closed_at"])
            att = conn.execute("SELECT exited_at FROM attendance WHERE session_id=?", (int(session_id),)).fetchone()
            self.assertIsNotNone(att["exited_at"])

        # بعد از بستن، /enter دیگر وارد نمی‌کند
        status, _, _ = http_request("GET", f"{self.base}/enter?token={token}")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
class TestBrandApi(BaseCase):
    """مسیر برند: config عمومی + save با احراز GUID ادمین محصول (محصول موک می‌شود)."""

    def setUp(self):
        super().setUp()
        self.mock_ctx = MockServer()
        self.pm_url = self.mock_ctx.__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        self.dist_dir = Path(self.tmp.name) / "dist"
        (self.dist_dir / "brand").mkdir(parents=True)
        self.tmp_env = {
            "PM_MANAGER_DB": self.db,
            "PM_MANAGER_SECRET": "test-secret-key",
            "PERSIANMEET_URL": self.pm_url,
            "PERSIANMEET_SECRET": "topsecret",
            "PERSIANMEET_DIST_DIR": str(self.dist_dir),
        }
        self._old_env = {k: os.environ.get(k) for k in self.tmp_env}
        os.environ.update(self.tmp_env)

        self.server = make_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.mock_ctx.__exit__(None, None, None)
        self.tmp.cleanup()
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    @staticmethod
    def _multipart(fields: dict, files: dict | None = None):
        boundary = "----TestBrandBoundary42"
        chunks = []
        for k, v in fields.items():
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        for k, (filename, blob, ctype) in (files or {}).items():
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode() + blob + b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        return body, f"multipart/form-data; boundary={boundary}"

    def test_brand_config_defaults(self):
        status, _, body = http_request("GET", self.base + "/brand-api/config")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["siteName"], "PersianMeet")

    def test_brand_save_requires_admin_guid(self):
        body, ctype = self._multipart({"siteName": "مدرسه تست", "primaryColor": "#0ea5e9"})
        status, _, resp = http_request("POST", self.base + "/brand-api/save", body=body,
                                       headers={"Content-Type": ctype})
        self.assertEqual(status, 403)
        status, _, resp = http_request("POST", self.base + "/brand-api/save", body=body,
                                       headers={"Content-Type": ctype, "X-PM-GUID": "not-admin"})
        self.assertEqual(status, 403)

    def test_brand_save_writes_files(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        body, ctype = self._multipart(
            {"siteName": "مدرسه تست", "siteNameShort": "تست", "tagline": "پلتفرم ما", "primaryColor": "#0ea5e9"},
            {"icon": ("icon.png", png, "image/png"), "logo": ("logo.png", png, "image/png")})
        status, _, resp = http_request("POST", self.base + "/brand-api/save", body=body,
                                       headers={"Content-Type": ctype, "X-PM-GUID": "admin-guid-123"})
        self.assertEqual(status, 200, resp)
        data = json.loads(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(data["brand"]["siteName"], "مدرسه تست")
        self.assertTrue(data["brand"]["hasLogo"] and data["brand"]["hasIcon"])
        brand_file = self.dist_dir / "brand" / "brand.json"
        saved = json.loads(brand_file.read_text("utf-8"))
        self.assertEqual(saved["siteName"], "مدرسه تست")
        self.assertEqual(saved["primaryColor"], "#0ea5e9")
        self.assertTrue((self.dist_dir / "brand" / "logo.png").exists())
        self.assertTrue((self.dist_dir / "brand" / "icon.png").exists())

    def test_brand_save_rejects_bad_color_and_bad_image(self):
        body, ctype = self._multipart({"siteName": "x", "primaryColor": "red"})
        status, _, resp = http_request("POST", self.base + "/brand-api/save", body=body,
                                       headers={"Content-Type": ctype, "X-PM-GUID": "admin-guid-123"})
        self.assertEqual(status, 400)
        bad = b"not-an-image-at-all"
        body, ctype = self._multipart({"siteName": "x", "primaryColor": "#000000"},
                                      {"icon": ("icon.png", bad, "image/png")})
        status, _, resp = http_request("POST", self.base + "/brand-api/save", body=body,
                                       headers={"Content-Type": ctype, "X-PM-GUID": "admin-guid-123"})
        self.assertEqual(status, 400)
