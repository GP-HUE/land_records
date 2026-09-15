"""Authentication: password hashing (PBKDF2) + signed session tokens (HMAC).

Zero external dependencies — uses only the Python standard library.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from . import paths

SECRET_FILE = os.path.join(paths.data_dir(), "data", "secret.key")

TOKEN_TTL = 60 * 60 * 12          # 12 hours
PBKDF2_ITERATIONS = 200_000

# Strong password policy (issue #2 security hardening).
MIN_PASSWORD_LEN = 8
DEFAULT_ADMIN_PASSWORD = "Admin@123"


def password_meets_policy(pw: str) -> bool:
    """A safe password: >=8 chars, has a letter AND a number (or symbol)."""
    if not pw or len(pw) < MIN_PASSWORD_LEN:
        return False
    has_alpha = any(c.isalpha() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    has_sym = any(not c.isalnum() for c in pw)
    return has_alpha and (has_digit or has_sym)


def weak_password_reason(pw: str) -> str:
    """Human-readable reason a password was rejected (empty if OK)."""
    if len(pw or "") < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    has_alpha = any(c.isalpha() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    has_sym = any(not c.isalnum() for c in pw)
    if not has_alpha:
        return "Password must contain at least one letter."
    if not (has_digit or has_sym):
        return "Password must contain at least one number or symbol."
    return ""

ROLES = ["viewer", "operator", "verifier", "admin"]
ROLE_LABELS = {
    "viewer": "Viewer (read-only)",
    "operator": "Data Operator",
    "verifier": "Verification Officer",
    "admin": "Administrator",
}
ROLE_LEVELS = {"viewer": 1, "operator": 2, "verifier": 3, "admin": 4}


def _secret() -> bytes:
    """Load the signing secret, creating a persistent one on first run."""
    os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
    if not os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "wb") as f:
            f.write(secrets.token_bytes(32))
        os.chmod(SECRET_FILE, 0o600)
    with open(SECRET_FILE, "rb") as f:
        return f.read()


# ---------- Passwords ----------
def hash_password(password: str, salt: str = None) -> tuple:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(),
                             PBKDF2_ITERATIONS)
    return salt, dk.hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    _, dk = hash_password(password, salt)
    return hmac.compare_digest(dk, expected)


# ---------- Session tokens ----------
def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_token(user_id: str, role: str, token_version: int, ttl: int = TOKEN_TTL) -> str:
    payload = {"uid": user_id, "role": role, "ver": token_version,
               "exp": int(time.time()) + ttl}
    body = _b64e(json.dumps(payload).encode())
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return body + "." + _b64e(sig)


def verify_token(token: str) -> dict:
    """Return payload dict if the token is valid, else None."""
    try:
        body, sig = token.split(".")
    except ValueError:
        return None
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64e(expected).encode(), sig.encode()):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload
