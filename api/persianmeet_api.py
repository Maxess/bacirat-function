#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
persianmeet_api.py — کلاینت رسمی API محصول PersianMeet (learn100.ir)

بر اساس مستندات رسمی (docs/00-official-docs.md):
    POST {base_url}/api
    Content-Type: application/json
    بدنه: { "Secret": "...", "Type": "<RequestType>", "Data": { ... } }

بدون هیچ وابستگی خارجی (فقط کتابخانه‌ی استاندارد پایتون ≥ 3.7).

نمونه‌ی استفاده‌ی کتابخانه‌ای:

    from persianmeet_api import PersianMeetAPI
    api = PersianMeetAPI("https://class.example.com", secret="mysecret")
    room = api.create_meet(title="کلاس ریاضی", user_count=50, admin_need_pass=True, admin_pass="s3cret")
    print(room["ID"], room.get("Url"))
    link = api.join(meet_id=room["ID"], username="سارا", join_role=0)
    print(link["Url"])   # /user/<pid> → لینک خصوصی ورود

نمونه‌ی CLI:

    python3 persianmeet_api.py --base-url https://class.example.com create --title "کلاس" --user-count 40
    python3 persianmeet_api.py --base-url https://class.example.com join --meet-id 21 --username ali --role 1 --pass s3cret
    python3 persianmeet_api.py --base-url https://class.example.com list --secret mysecret
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

__all__ = ["PersianMeetAPI", "PersianMeetError"]


class PersianMeetError(RuntimeError):
    """خطای سمت کلاینت یا پاسخ غیراوکی از سرور PersianMeet."""


class PersianMeetAPI:
    """کلاینت همگام و ساده برای endpoint یگانه‌ی /api."""

    def __init__(self, base_url: str, secret: str | None = None, timeout: float = 15.0):
        if not base_url:
            raise ValueError("base_url الزامی است، مثلاً https://class.example.com")
        self.base_url = base_url.rstrip("/")
        self.secret = secret if secret is not None else os.environ.get("PERSIANMEET_SECRET") or None
        self.timeout = timeout

    # ---------- هسته ----------
    def call(self, type_: str, data: dict | None = None, secret: str | None = None) -> dict:
        """ارسال خام یک درخواست به /api و برگرداندن دیکشنری پاسخ."""
        payload: dict = {"Type": type_}
        effective_secret = secret if secret is not None else self.secret
        if effective_secret is not None:
            payload["Secret"] = effective_secret
        else:
            payload["Secret"] = ""
        if data is not None:
            payload["Data"] = data

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise PersianMeetError(f"HTTP {exc.code} از سرور: {raw[:500]}") from exc
        except urllib.error.URLError as exc:
            raise PersianMeetError(f"اتصال به سرور برقرار نشد ({self.base_url}): {exc.reason}") from exc

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise PersianMeetError(f"پاسخ سرور JSON نبود: {raw[:500]}")
        if not isinstance(result, dict):
            raise PersianMeetError(f"پاسخ غیرمنتظره از سرور: {raw[:500]}")
        return result

    @staticmethod
    def _require_ok(result: dict) -> dict:
        status = str(result.get("Status", "")).lower()
        if status and status != "ok":
            raise PersianMeetError(f"سرور وضعیت غیرموفق برگرداند: {json.dumps(result, ensure_ascii=False)}")
        return result

    # ---------- Type های رسمی (مستندات docs/00-official-docs.md) ----------
    def create_meet(
        self,
        title: str,
        user_count: int = 100,
        user_need_pass: bool = False,
        user_pass: str = "",
        admin_need_pass: bool = True,
        admin_pass: str = "",
        secret: str | None = None,
    ) -> dict:
        """ساخت اتاق جدید → {Status, ID, Url (/room/<id>), Title, ...}"""
        if not title:
            raise ValueError("title الزامی است")
        data = {
            "Title": title,
            "UserCount": int(user_count),
            "UserNeedPass": bool(user_need_pass),
            "UserPass": user_pass,
            "AdminNeedPass": bool(admin_need_pass),
            "AdminPass": admin_pass,
        }
        return self._require_ok(self.call("CreateMeet", data, secret=secret))

    def get_meet_info(self, meet_id: int, secret: str | None = None) -> dict:
        """اطلاعات اتاق → {ID, UserNeedPass, AdminNeedPass, Title, ...}"""
        return self._require_ok(self.call("GetMeetInfo", {"ID": int(meet_id)}, secret=secret))

    def join(self, meet_id: int, username: str, join_role: int = 0, pass_: str = "", secret: str | None = None) -> dict:
        """
        پیوستن به اتاق → {PID, Url(/user/<pid>)}
        join_role: 0 = کاربر عادی، 1 = مدیر اتاق
        """
        if join_role not in (0, 1):
            raise ValueError("join_role باید 0 (عادی) یا 1 (مدیر) باشد")
        if not username:
            raise ValueError("username الزامی است")
        data = {"meetID": int(meet_id), "userName": username, "Pass": pass_, "JoinRole": int(join_role)}
        return self._require_ok(self.call("Join", data, secret=secret))

    def meet_list(self, secret: str | None = None) -> dict:
        """لیست آخرین اتاق‌ها — طبق مستندات رسمی نیاز به secret دارد."""
        return self._require_ok(self.call("MeetList", secret=secret))

    def active_meets(self, secret: str | None = None) -> dict:
        """اتاق‌هایی که کاربر آنلاین دارند — نیاز به secret دارد."""
        return self._require_ok(self.call("ActiveMeets", secret=secret))

    def delete_meet(self, meet_id: int, secret: str | None = None) -> dict:
        """
        حذف اتاق و تمام داده‌هایش — غیرقابل بازگشت!
        طبق مستندات رسمی نیاز به secret دارد.
        """
        return self._require_ok(self.call("DeleteMeet", {"ID": int(meet_id)}, secret=secret))


# ======================================================================
# CLI
# ======================================================================
def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CLI برای API سرور PersianMeet (learn100.ir)")
    p.add_argument("--base-url", default=os.environ.get("PERSIANMEET_URL", "http://127.0.0.1:8003"),
                   help="آدرس ریشه‌ی سرور (پیش‌فرض: http://127.0.0.1:8003 یا env PERSIANMEET_URL)")
    p.add_argument("--secret", default=os.environ.get("PERSIANMEET_SECRET", ""),
                   help="secret API (پیش‌فرض: env PERSIANMEET_SECRET)")
    p.add_argument("--timeout", type=float, default=15.0)
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="ساخت اتاق جدید")
    c.add_argument("--title", required=True)
    c.add_argument("--user-count", type=int, default=100)
    c.add_argument("--user-need-pass", action="store_true")
    c.add_argument("--user-pass", default="")
    c.add_argument("--admin-need-pass", action="store_true", default=True)
    c.add_argument("--no-admin-pass", dest="admin_need_pass", action="store_false")
    c.add_argument("--admin-pass", default="")

    j = sub.add_parser("join", help="پیوستن به اتاق و گرفتن لینک خصوصی")
    j.add_argument("--meet-id", type=int, required=True)
    j.add_argument("--username", required=True)
    j.add_argument("--role", type=int, choices=[0, 1], default=0, help="0=عادی (پیش‌فرض) 1=مدیر")
    j.add_argument("--pass", dest="pass_", default="")

    g = sub.add_parser("info", help="اطلاعات اتاق")
    g.add_argument("--meet-id", type=int, required=True)

    sub.add_parser("list", help="لیست آخرین اتاق‌ها (نیاز به secret)")
    sub.add_parser("active", help="اتاق‌های دارای کاربر آنلاین (نیاز به secret)")

    d = sub.add_parser("delete", help="حذف اتاق + همه‌ی داده‌ها (secret لازم؛ غیرقابل بازگشت!)")
    d.add_argument("--meet-id", type=int, required=True)
    d.add_argument("--yes", action="store_true", help="بدون پرسش تأیید")

    sub.add_parser("raw", help="ارسال خام Type دلخواه (دیباگ)").add_argument("type")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secret = args.secret or None
    api = PersianMeetAPI(args.base_url, secret=secret, timeout=args.timeout)

    try:
        if args.command == "create":
            _print_json(api.create_meet(
                title=args.title, user_count=args.user_count,
                user_need_pass=args.user_need_pass, user_pass=args.user_pass,
                admin_need_pass=args.admin_need_pass, admin_pass=args.admin_pass))
        elif args.command == "join":
            _print_json(api.join(meet_id=args.meet_id, username=args.username,
                                 join_role=args.role, pass_=args.pass_))
        elif args.command == "info":
            _print_json(api.get_meet_info(args.meet_id))
        elif args.command == "list":
            _print_json(api.meet_list())
        elif args.command == "active":
            _print_json(api.active_meets())
        elif args.command == "delete":
            if not args.yes:
                ans = input(f"اتاق {args.meet_id} و تمام داده‌هایش برای همیشه حذف شود؟ [yes/N]: ").strip().lower()
                if ans != "yes":
                    print("لغو شد.")
                    return 1
            _print_json(api.delete_meet(args.meet_id))
        elif args.command == "raw":
            _print_json(api.call(args.type, secret=secret))
        else:
            raise SystemExit(f"دستور ناشناخته: {args.command}")
    except PersianMeetError as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
