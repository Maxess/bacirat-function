#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""اجرای LiveClass — python3 run.py serve --port 8200 | init-admin ..."""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import db as dbmod  # noqa: E402


def init_admin(args):
    dbmod.init_db()
    from app.security import hash_password
    with dbmod.connect() as conn:
        if conn.execute("SELECT id FROM users WHERE username=?", (args.username,)).fetchone():
            print(f"کاربری با نام {args.username} وجود دارد؛ رمز تغییر نکرد.")
            return 1
        conn.execute("INSERT INTO users(username, full_name, password_hash, role) VALUES (?,?,?, 'admin')",
                     (args.username, args.full_name, hash_password(args.password)))
        conn.commit()
    print(f"ادمین ساخت شد: {args.username}")
    return 0


def serve(args):
    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init-admin", help="ساخت ادمین اولیه")
    p_init.add_argument("--username", required=True)
    p_init.add_argument("--password", default=None)
    p_init.add_argument("--full-name", default="مدیر سیستم")
    p_init.set_defaults(func=init_admin)

    p_serve = sub.add_parser("serve", help="اجرای سرور")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8200)
    p_serve.set_defaults(func=serve)

    args = ap.parse_args()
    if getattr(args, "password", None) is None and args.cmd == "init-admin":
        args.password = getpass.getpass("رمز ادمین: ")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
