# -*- coding: utf-8 -*-
"""هش رمز با PBKDF2 و کوکی نشست امضاشده (HMAC) — بدون هیچ وابستگی خارجی."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

ITERATIONS = 200_000


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_hex, hash_hex = stored.split("$", 3)
        iterations = int(iter_s)
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


class SessionCodec:
    """کوکی pm_session = base64url(json{uid, role, exp}) + '.' + HMAC-SHA256"""

    def __init__(self, secret: str | bytes, max_age_seconds: int = 8 * 3600):
        self.secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        self.max_age = max_age_seconds

    def _sign(self, payload_b64: str) -> str:
        return hmac.new(self.secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()

    def encode(self, uid: int, role: str) -> str:
        payload = {"uid": uid, "role": role, "exp": int(time.time()) + self.max_age}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
        return f"{payload_b64}.{self._sign(payload_b64)}"

    def decode(self, token: str) -> dict | None:
        try:
            payload_b64, sig = token.rsplit(".", 1)
            if not hmac.compare_digest(sig, self._sign(payload_b64)):
                return None
            pad = "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
            if int(payload.get("exp", 0)) < int(time.time()):
                return None
            return payload
        except Exception:
            return None
