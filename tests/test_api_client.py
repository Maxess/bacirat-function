#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""تست واحد کلاینت API — با یک سرور HTTP مینیمال داخل همین فایل (بدون اینترنت)."""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from persianmeet_api import PersianMeetAPI, PersianMeetError  # noqa: E402


class MockHandler(BaseHTTPRequestHandler):
    """تقلید رفتار /api طبق مستندات رسمی."""

    def log_message(self, *args):  # سکوت لاگ‌ها
        pass

    def do_POST(self):
        if self.path != "/api":
            self.send_json({"Status": "error", "Message": "not found"}, code=404)
            return
        if self.headers.get("Content-Type") != "application/json":
            self.send_json({"Status": "error", "Message": "bad content-type"}, code=415)
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except Exception:
            self.send_json({"Status": "error", "Message": "bad json"}, code=400)
            return

        t = payload.get("Type")
        data = payload.get("Data") or {}
        secret = payload.get("Secret", "")

        if t == "CreateMeet":
            self.send_json({"Status": "ok", "ID": 21, "Url": "/room/21",
                            "Title": data.get("Title", ""), "Message": ""})
        elif t == "GetMeetInfo":
            self.send_json({"Status": "ok", "ID": data.get("ID"), "UserNeedPass": 0,
                            "AdminNeedPass": 1, "Title": "my room", "Message": ""})
        elif t == "Join":
            role = int(data.get("JoinRole", 0))
            pid = "adminpid123" if role == 1 else "userpid456"
            self.send_json({"Status": "ok", "PID": pid, "Message": "",
                            "Url": f"/user/{pid}"})
        elif t in ("MeetList", "ActiveMeets"):
            if secret != "topsecret":
                self.send_json({"Status": "error", "Message": "secret required"}, code=403)
                return
            self.send_json({"Status": "ok", "Data": {"rooms": [{"ID": 21}]}})
        elif t == "DeleteMeet":
            if secret != "topsecret":
                self.send_json({"Status": "error", "Message": "secret required"}, code=403)
                return
            self.send_json({"Status": "ok", "Message": ""})
        else:
            self.send_json({"Status": "error", "Message": f"unknown type {t}"}, code=400)

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ServerFixture:
    def __enter__(self):
        self.server = HTTPServer(("127.0.0.1", 0), MockHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self.base_url

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class TestPersianMeetAPI(unittest.TestCase):
    def setUp(self):
        self._fixture = ServerFixture()
        self.base_url = self._fixture.__enter__()
        self.api = PersianMeetAPI(self.base_url, timeout=5)

    def tearDown(self):
        self._fixture.__exit__(None, None, None)

    def test_create_meet(self):
        res = self.api.create_meet(title="کلاس ریاضی", user_count=40,
                                   admin_need_pass=True, admin_pass="x")
        self.assertEqual(res["ID"], 21)
        self.assertEqual(res["Url"], "/room/21")
        self.assertEqual(res["Title"], "کلاس ریاضی")

    def test_get_meet_info(self):
        res = self.api.get_meet_info(21)
        self.assertEqual(res["ID"], 21)
        self.assertEqual(res["Title"], "my room")

    def test_join_user_and_admin(self):
        u = self.api.join(meet_id=21, username="سارا", join_role=0)
        a = self.api.join(meet_id=21, username="دکتر", join_role=1, pass_="x")
        self.assertTrue(u["Url"].startswith("/user/"))
        self.assertNotEqual(u["PID"], a["PID"])

    def test_join_role_validation(self):
        with self.assertRaises(ValueError):
            self.api.join(meet_id=21, username="x", join_role=2)

    def test_meet_list_requires_secret(self):
        with self.assertRaises(PersianMeetError):
            self.api.meet_list(secret="wrong")
        ok = self.api.meet_list(secret="topsecret")
        self.assertEqual(ok["Status"], "ok")

    def test_active_meets_with_secret(self):
        ok = self.api.active_meets(secret="topsecret")
        self.assertIn("rooms", ok["Data"])

    def test_delete_meet(self):
        ok = self.api.delete_meet(21, secret="topsecret")
        self.assertEqual(ok["Status"], "ok")

    def test_secret_from_constructor(self):
        api2 = PersianMeetAPI(self.base_url, secret="topsecret", timeout=5)
        self.assertEqual(api2.meet_list()["Status"], "ok")

    def test_connection_error_message(self):
        api3 = PersianMeetAPI("http://127.0.0.1:1", timeout=1)  # پورت ۱ بسته است
        with self.assertRaises(PersianMeetError):
            api3.meet_list()


if __name__ == "__main__":
    unittest.main(verbosity=2)
