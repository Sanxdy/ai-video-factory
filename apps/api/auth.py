"""Login gate for the public AVF dashboard.

Single shared password (settings key `auth.password`, stored as salted hash).
Session = signed cookie (itsdangerous), 30-day expiry. All /api/* routes are
protected by HTTP middleware except /api/auth/* (login/logout/status) and
/api/health. Static frontend stays open — the data lives behind the API.

Set the initial password:  avf auth set-password   (or POST /api/auth/login
with `auth.setup` bootstrap when no password exists yet).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.settings import get_setting, set_setting

COOKIE = "avf_session"
SESSION_DAYS = 30
_MAX_AGE = SESSION_DAYS * 86400

# paths reachable without a session
OPEN = ("/api/auth/", "/api/health")


def _secret() -> str:
    """Signing key for session tokens; auto-generated once."""
    key = get_setting("auth.secret")
    if not key:
        key = secrets.token_hex(32)
        set_setting("auth.secret", key)
    return key


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                               200_000).hex()


def set_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = os.urandom(16).hex()
    set_setting("auth.salt", salt)
    set_setting("auth.password", _hash_password(password, salt))


def verify_password(password: str) -> bool:
    stored = get_setting("auth.password") or ""
    salt = get_setting("auth.salt") or ""
    if not stored or not salt:
        return False
    return hmac.compare_digest(stored, _hash_password(password, salt))


def has_password() -> bool:
    return bool(get_setting("auth.password"))


def make_token() -> str:
    """Signed token: random session id + timestamp-free signature (30d expiry
    carried by the cookie itself)."""
    import time
    msg = f"{secrets.token_hex(16)}.{int(time.time())}"
    sig = hmac.new(_secret().encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def valid_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE, "")
    parts = token.split(".")
    if len(parts) != 3:
        return False
    sid, ts, sig = parts
    want = hmac.new(_secret().encode(), f"{sid}.{ts}".encode(),
                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return False
    try:
        age_ok = int(ts) + _MAX_AGE > int(__import__("time").time())
    except ValueError:
        return False
    return age_ok
