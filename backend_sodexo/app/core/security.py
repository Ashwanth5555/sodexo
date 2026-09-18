"""Authentication primitives for the POC: password hashing and signed tokens.

Deliberately stdlib-only (hashlib/hmac/secrets) so the POC gains a real login
without pulling passlib/python-jose into requirements.txt. The token is the
same shape as a JWT (base64url header.payload.signature, HS256) so it can be
swapped for a proper JWT library later without touching any caller.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

PBKDF2_ROUNDS = 240_000


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    """Return a 'pbkdf2_sha256$rounds$salt$hash' string for storage."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64url_decode(salt), int(rounds))
    # compare_digest: constant time, so a wrong password can't be narrowed down
    # by timing how long the comparison took.
    return hmac.compare_digest(candidate, _b64url_decode(expected))


def create_access_token(*, subject: str, role: str, full_name: str, secret: str, expires_minutes: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role,
        "name": full_name,
        "iat": now,
        "exp": now + expires_minutes * 60,
    }
    signing_input = f"{_b64url_encode(_compact(header))}.{_b64url_encode(_compact(payload))}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str, *, secret: str) -> dict | None:
    """Return the token payload, or None if it is malformed, forged or expired."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    signing_input = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected, _b64url_decode(parts[2])):
            return None
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def _compact(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
