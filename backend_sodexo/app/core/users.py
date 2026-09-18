"""The three application logins.

POC scope: the user directory is a fixed, in-code list rather than a database
table, so there is no signup/admin-user flow to maintain. Passwords are read
from settings (env-overridable) and hashed once at import time - nothing is
compared in plain text at request time.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings
from app.core.security import hash_password, verify_password


@dataclass(frozen=True)
class AppUser:
    username: str
    full_name: str
    role: str
    password_hash: str


@lru_cache
def _directory() -> dict[str, AppUser]:
    settings = get_settings()
    users = [
        AppUser(
            username="admin",
            full_name="Alex Nguyen",
            role="ADMIN",
            password_hash=hash_password(settings.auth_admin_password),
        ),
        AppUser(
            username="manager",
            full_name="Omar Diaz",
            role="OPERATIONS_MANAGER",
            password_hash=hash_password(settings.auth_manager_password),
        ),
        AppUser(
            username="analyst",
            full_name="Dana Singh",
            role="DATA_ANALYST",
            password_hash=hash_password(settings.auth_analyst_password),
        ),
    ]
    return {u.username: u for u in users}


def authenticate(username: str, password: str) -> AppUser | None:
    user = _directory().get(username.strip().lower())
    if user is None:
        # Still run a hash comparison against a throwaway value so that an
        # unknown username takes the same time as a known one with a bad
        # password (no user enumeration via response timing).
        verify_password(password, hash_password("no-such-user"))
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_user(username: str) -> AppUser | None:
    return _directory().get(username.strip().lower())
