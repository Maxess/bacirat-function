# -*- coding: utf-8 -*-
"""تنظیمات برند لایوکلاس — در دیتابیس ذخیره می‌شود و فرانت همه‌جا از همین می‌خواند."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import db as dbmod

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]


def uploads_dir() -> Path:
    d = Path(dbmod.db_path()).parent / "brand"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings(db_path: str | None = None) -> dict:
    with dbmod.connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = dict(dbmod.DEFAULT_SETTINGS)
    data.update({r["key"]: r["value"] for r in rows})
    return {
        "site_name": data["site_name"],
        "site_name_short": data["site_name_short"],
        "tagline": data["tagline"],
        "primary_color": data["primary_color"],
        "has_logo": data["has_logo"] == "1",
        "has_icon": data["has_icon"] == "1",
        "brand_version": int(data["brand_version"] or "1"),
    }


def _set(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def _sniff(blob: bytes) -> str | None:
    head = blob[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith((b"<svg", b"<?xml")) and b"<svg" in blob[:2048]:
        return "svg"
    for sig, kind in _SIGNATURES:
        if head.startswith(sig):
            return kind
    if head.startswith(b"RIFF") and blob[8:12] == b"WEBP":
        return "webp"
    return None


def sniff_ok(blob: bytes) -> bool:
    return _sniff(blob) is not None


def save_brand(*, site_name: str, site_name_short: str, tagline: str, primary_color: str,
               logo: bytes | None, icon: bytes | None,
               db_path: str | None = None) -> dict:
    site_name = (site_name or "").strip()
    if not site_name or len(site_name) > 64:
        raise ValueError("نام پلتفرم الزامی است (حداکثر ۶۴ کاراکتر)")
    site_name_short = (site_name_short or "").strip()[:24] or site_name[:24]
    tagline = (tagline or "").strip()[:140]
    primary_color = (primary_color or "").strip().lower()
    if not COLOR_RE.fullmatch(primary_color):
        raise ValueError("رنگ باید به شکل #rrggbb باشد")

    with dbmod.connect(db_path) as conn:
        _set(conn, "site_name", site_name)
        _set(conn, "site_name_short", site_name_short)
        _set(conn, "tagline", tagline)
        _set(conn, "primary_color", primary_color)
        if logo is not None:
            if len(logo) > MAX_IMAGE_BYTES:
                raise ValueError("لوگو بیش از حد مجاز است (۲MB)")
            if not sniff_ok(logo):
                raise ValueError("فرمت لوگو ناشناخته است (PNG/JPEG/GIF/WEBP/SVG)")
            (uploads_dir() / "logo.png").write_bytes(logo)
            _set(conn, "has_logo", "1")
        if icon is not None:
            if len(icon) > MAX_IMAGE_BYTES:
                raise ValueError("آیکون بیش از حد مجاز است (۲MB)")
            if not sniff_ok(icon):
                raise ValueError("فرمت آیکون ناشناخته است (PNG/JPEG/GIF/WEBP/SVG)")
            (uploads_dir() / "icon.png").write_bytes(icon)
            _set(conn, "has_icon", "1")
        _set(conn, "brand_version", str(int(time.time())))
        conn.commit()
    return load_settings(db_path)


def brand_file(kind: str) -> Path | None:
    path = uploads_dir() / f"{kind}.png"
    return path if path.exists() else None
