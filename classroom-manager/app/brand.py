# -*- coding: utf-8 -*-
"""مدیریت برند پلتفرم برای PersianMeet — ذخیره‌ی اتمیک brand.json و فایل‌های آیکون/لوگو.

فایل‌ها داخل  $PERSIANMEET_DIST_DIR/brand/  نوشته می‌شوند (پیش‌فرض:
$PERSIANMEET_INSTALL_DIR/dist/brand یا /opt/persianmeet/dist/brand).

چرا در dist؟ چون محصول، فایل‌های استاتیک را از دیسک سرو می‌کند (آزمایش شده)، پس هر
فایل جدیدی که در brand/ بگذاریم بلافاصله قابل آدرس‌دهی است؛ و index.html با پل
brand-bridge محتوای برند را در لحظه به‌روز می‌کند.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_NAME = 64
MAX_SHORT = 24
MAX_TAGLINE = 140

# تصویرها: magic byte های مجاز
_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),   # نیازمند بررسی WEBP بودن در ادامه
    (b"<svg", "svg"),    # بعد از strip فاصله/BOM چک می‌شود
    (b"<?xml", "svg"),
]


def dist_dir() -> Path:
    custom = os.environ.get("PERSIANMEET_DIST_DIR")
    if custom:
        return Path(custom)
    install_dir = os.environ.get("PERSIANMEET_INSTALL_DIR", "/opt/persianmeet")
    return Path(install_dir) / "dist"


def brand_dir() -> Path:
    return dist_dir() / "brand"


def brand_file() -> Path:
    return brand_dir() / "brand.json"


def default_brand() -> dict:
    return {
        "version": 1,
        "siteName": "PersianMeet",
        "siteNameShort": "PersianMeet",
        "tagline": "",
        "primaryColor": "#000000",
        "hasLogo": False,
        "hasIcon": False,
    }


def load_brand() -> dict:
    try:
        data = json.loads(brand_file().read_text("utf-8"))
        if isinstance(data, dict):
            brand = default_brand()
            brand.update({k: data[k] for k in default_brand() if k in data})
            return brand
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return default_brand()


def sniff_image(blob: bytes) -> str | None:
    head = blob[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    for sig, kind in _SIGNATURES:
        if head.startswith(sig):
            if kind == "webp":
                return "webp" if blob[8:12] == b"WEBP" else None
            if kind == "svg":
                return "svg" if b"<svg" in blob[:2048] else None
            return kind
    return None


def sanitize_text(value: str | None, max_len: int, *, field: str) -> str:
    text = (value or "").strip()
    if len(text) > max_len:
        raise ValueError(f"{field} بیش از حد مجاز است (حداکثر {max_len} کاراکتر)")
    # کنترل‌کاراکترها ممنوع (RTL override و ...)
    if any(ord(c) < 32 and c not in "\t" for c in text):
        raise ValueError(f"{field} شامل کاراکتر غیرمجاز است")
    return text


def sanitize_color(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not COLOR_RE.fullmatch(text):
        raise ValueError("رنگ باید به شکل #rrggbb باشد")
    return text


def _atomic_write(path: Path, blob: bytes) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_bytes(blob)
    os.replace(tmp, path)


def save_brand(*, site_name: str, site_name_short: str, tagline: str, primary_color: str,
               logo: tuple[bytes, str] | None, icon: tuple[bytes, str] | None) -> dict:
    """اعتبارسنجی + نوشتن اتمیک brand.json و فایل‌های برند. برگرداندن brand نهایی."""
    brand = load_brand()

    site_name = sanitize_text(site_name, MAX_NAME, field="نام پلتفرم")
    if not site_name:
        raise ValueError("نام پلتفرم الزامی است")
    site_name_short = sanitize_text(site_name_short, MAX_SHORT, field="نام کوتاه") or site_name[:MAX_SHORT]
    tagline = sanitize_text(tagline, MAX_TAGLINE, field="توضیح کوتاه")
    primary_color = sanitize_color(primary_color)

    brand.update({
        "siteName": site_name,
        "siteNameShort": site_name_short,
        "tagline": tagline,
        "primaryColor": primary_color,
    })

    bdir = brand_dir()
    bdir.mkdir(parents=True, exist_ok=True)

    for field, pair, target in (("لوگو", logo, bdir / "logo.png"), ("آیکون", icon, bdir / "icon.png")):
        if not pair:
            continue
        blob, filename = pair
        if not blob:
            continue
        if len(blob) > MAX_IMAGE_BYTES:
            raise ValueError(f"{field}: حجم فایل بیش از ۲ مگابایت است")
        kind = sniff_image(blob)
        if kind is None:
            raise ValueError(f"{field}: فرمت تصویر شناخته نشد (PNG/JPEG/GIF/WEBP/SVG مجاز است)")
        # همه‌چیز را به نام ثابت ذخیره می‌کنیم تا آدرس /brand/logo.png همیشه معتبر بماند
        _atomic_write(target, blob)
        if field == "لوگو":
            brand["hasLogo"] = True
        else:
            brand["hasIcon"] = True

    brand["version"] = int(time.time())
    _atomic_write(brand_file(), json.dumps(brand, ensure_ascii=False, indent=2).encode("utf-8"))
    return brand
