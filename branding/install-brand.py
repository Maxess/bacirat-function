#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""نصب/به‌روزرسانی برند پویا روی dist محصول PersianMeet.

کارها (همه idempotent):
  1) کپی payload/brand-bridge.js و payload/brand.json به  $DIST/brand/
  2) تزریق یک‌خطی <script src="/brand/brand-bridge.js"> در <head> صفحه‌ی index.html
     (بلافاصله بعد از <script src="/translate.js"> تا i18n قبل از Vue وصله شود)
  3) حذف ریدایرکت فروشنده به app2.learn100.ir (اختیاری؛ پیش‌فرض: حذف — برای هاست خودتان لازم است)

استفاده:
  sudo python3 branding/install-brand.py --dist /opt/persianmeet/dist \
        [--site-name "مدرسه ما"] [--tagline "..."] [--primary-color "#0ea5e9"] \
        [--keep-learn100-redirect] [--dry-run]

نکته: فایل‌های index.html / translate.js فروشنده با این ابزار ویرایش می‌شوند؛ backup در کنارشان ساخته می‌شود.
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parent / "payload"
MARKER = "<!-- pm-brand-bridge -->"

def log(msg: str) -> None:
    print(f"[brand] {msg}")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True, help="مسیر پوشه‌ی dist محصول (مثلاً /opt/persianmeet/dist)")
    ap.add_argument("--site-name", default=None, help="نام پیش‌فرض پلتفرم (اگر brand.json وجود ندارد استفاده می‌شود)")
    ap.add_argument("--tagline", default=None)
    ap.add_argument("--primary-color", default=None, help="#rrggbb")
    ap.add_argument("--keep-learn100-redirect", action="store_true", help="بلاک ریدایرکت app.learn100 را حذف نکن")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dist = Path(args.dist).expanduser().resolve()
    index = dist / "index.html"
    if not index.is_file():
        print(f"خطا: {index} پیدا نشد — مسیر dist درست نیست؟", file=sys.stderr)
        return 1

    brand_dir = dist / "brand"
    if args.dry_run:
        log(f"(dry-run) ایجاد {brand_dir} و تزریق اسکریپت در {index}")
    else:
        brand_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PAYLOAD / "brand-bridge.js", brand_dir / "brand-bridge.js")
        log(f"کپی شد: {brand_dir/'brand-bridge.js'}")
        brand_file = brand_dir / "brand.json"
        if not brand_file.exists():
            default_brand = json.loads((PAYLOAD / "brand.json").read_text("utf-8"))
            if args.site_name:
                default_brand["siteName"] = args.site_name
                default_brand["siteNameShort"] = args.site_name[:24]
            if args.tagline:
                default_brand["tagline"] = args.tagline
            if args.primary_color:
                if not re.fullmatch(r"#[0-9a-fA-F]{6}", args.primary_color):
                    print("خطا: --primary-color باید به شکل #rrggbb باشد", file=sys.stderr)
                    return 1
                default_brand["primaryColor"] = args.primary_color.lower()
            brand_file.write_text(json.dumps(default_brand, ensure_ascii=False, indent=2), "utf-8")
            log(f"ایجاد شد: {brand_file}")
        else:
            log(f"موجود است، دست‌نخورده می‌ماند: {brand_file}")

    html = index.read_text("utf-8")
    original_html = html

    # (3) حذف بلاک ریدایرکت فروشنده به app2.learn100.ir
    if not args.keep_learn100_redirect:
        pattern = re.compile(r"<script>\s*let href = window\.location\.href;.*?</script>\s*", re.S)
        if pattern.search(html):
            html = pattern.sub("", html, count=1)
            log("بلاک ریدایرکت learn100 حذف شد" if not args.dry_run else "(dry-run) بلاک ریدایرکت learn100 حذف خواهد شد")

    # (2) تزریق اسکریپت برند بلافاصله بعد از translate.js
    if MARKER in html or "/brand/brand-bridge.js" in html:
        log("اسکریپت برند از قبل تزریق شده است؛ رد شد")
    else:
        anchor = '<script src="/translate.js"></script>'
        if anchor not in html:
            print("خطا: الگوی <script src=\"/translate.js\"></script> در index.html پیدا نشد — نسخه‌ی پکیج عوض شده؟", file=sys.stderr)
            return 1
        injection = f"{anchor}\n    {MARKER}\n    <script src=\"/brand/brand-bridge.js\"></script>"
        html = html.replace(anchor, injection, 1)
        log("اسکریپت برند در index.html تزریق شد")

    if not args.dry_run and html != original_html:
        backup = index.with_suffix(f".html.pmbrand-{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(index, backup)
        index.write_text(html, "utf-8")
        log(f"پشتیبان: {backup}")

    log("تمام شد. اگر PersianMeet در حال اجراست، یک‌بار ری‌استارتش کنید: systemctl restart persianmeet")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
