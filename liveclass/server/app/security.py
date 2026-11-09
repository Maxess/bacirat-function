# -*- coding: utf-8 -*-
"""هش رمز و سشن کوکی — stdlib خالص (pbkdf2 + HMAC-SHA256)."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

ITERATIONS = 200_000


def hash_password(password: str, *, salt_hex: str | None = None, iterations: int = ITERATIONS) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iter_s, salt_hex, hash_hex = stored.split("$", 3)
        expected = hash_password(password, salt_hex=salt_hex, iterations=int(iter_s))
        return hmac.compare_digest(expected, stored)
    except Exception:
        return False


def _secret() -> bytes:
    secret = os.environ.get("LIVECLASS_SECRET")
    if secret:
        return secret.encode("utf-8")
    key_file = os.path.join(os.path.dirname(os.environ.get("LIVECLASS_DB", "")) or "/tmp", ".liveclass_secret")
    if os.path.exists(key_file):
        return open(key_file, "rb").read().strip()
    secret_bytes = os.urandom(32)
    try:
        with open(key_file, "wb") as fh:
            fh.write(secret_bytes)
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return secret_bytes


def encode_session(user_id: int, role: str, ttl_seconds: int = 12 * 3600) -> str:
    payload = f"{user_id}.{role}.{int(time.time()) + ttl_seconds}"
    sig = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def decode_session(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        user_id_s, role, exp_s, sig = token.rsplit(".", 3)
        payload = f"{user_id_s}.{role}.{exp_s}"
        expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        if int(exp_s) < int(time.time()):
            return None
        return {"uid": int(user_id_s), "role": role}
    except Exception:
        return None


def new_entry_token() -> str:
    return secrets.token_urlsafe(24)
