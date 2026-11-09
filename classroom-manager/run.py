#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""راه‌انداز ماژول مدیریت کلاس.

    python3 run.py init-admin --username admin --password 'S3cret!' [--full-name 'مدیر سامانه']
    python3 run.py serve [--host 0.0.0.0] [--port 8090]
"""
import argparse
import getpass
import sys

from app import db as dbmod
from app.auth import hash_password
from app.web import make_server, Config


def cmd_init_admin(args):
    cfg = Config()
    dbmod.init_db(cfg.db_path)
    password = args.password or getpass.getpass("رمز ادمین: ")
    with dbmod.connect(cfg.db_path) as conn:
        row = conn.execute("SELECT id FROM teachers WHERE username=?", (args.username,)).fetchone()
        if row:
            conn.execute("UPDATE teachers SET role='admin', password_hash=?, full_name=? WHERE id=?",
                         (hash_password(password), args.full_name, row["id"]))
            print(f"ادمین «{args.username}» بروزرسانی شد.")
        else:
            conn.execute("INSERT INTO teachers(username, full_name, password_hash, role) VALUES (?,?,?,'admin')",
                         (args.username, args.full_name, hash_password(password)))
            print(f"ادمین «{args.username}» ساخته شد.")


def cmd_serve(args):
    httpd = make_server(args.host, args.port)
    print(f"سامانه‌ی مدیریت کلاس روی http://{args.host}:{args.port} در حال اجراست — Ctrl+C برای توقف")
    print(f"اتصال به PersianMeet: PERSIANMEET_URL={httpd.config.pm_url}  close_strategy={httpd.config.close_strategy}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nتوقف.")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("init-admin", help="ساخت/بازنشانی حساب ادمین")
    a.add_argument("--username", default="admin")
    a.add_argument("--password", default=None)
    a.add_argument("--full-name", default="مدیر سامانه")
    a.set_defaults(func=cmd_init_admin)

    s = sub.add_parser("serve", help="اجرای سرور وب")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8090)
    s.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
