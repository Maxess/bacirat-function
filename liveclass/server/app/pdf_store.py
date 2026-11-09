# -*- coding: utf-8 -*-
"""ذخیره و سرو PDFهای تخته — روی دیسک کنار دیتابیس؛ رندر کاملاً سمت کلاینت (بدون بار سرور)."""
from __future__ import annotations

import secrets
from pathlib import Path

from . import db as dbmod

MAX_PDF_BYTES = 25 * 1024 * 1024   # 25MB — یک کتاب درسی استاندارد


def pdf_dir() -> Path:
    d = Path(dbmod.db_path()).parent / "pdf"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_pdf(blob: bytes) -> bool:
    return blob[:5] == b"%PDF-"


def save_pdf(blob: bytes) -> str:
    if not is_pdf(blob):
        raise ValueError("فایل PDF معتبر نیست")
    if len(blob) > MAX_PDF_BYTES:
        raise ValueError("حجم PDF از ۲۵ مگابایت بیشتر است")
    pdf_id = secrets.token_urlsafe(18)
    (pdf_dir() / f"{pdf_id}.pdf").write_bytes(blob)
    return pdf_id


def pdf_path(pdf_id: str) -> Path | None:
    safe = "".join(c for c in pdf_id if c.isalnum() or c in "-_")
    if not safe:
        return None
    path = pdf_dir() / f"{safe}.pdf"
    return path if path.is_file() else None
