"""The application logins.

POC scope: the user directory is a fixed, in-code list rather than a database
table, so there is no signup or user-admin flow to maintain. Passwords come
from settings (env-overridable) and are hashed once at import time - nothing
is compared in plain text at request time.

Usernames are `firstname.lastname`, which is what a real deployment issues
and what a client expects to be handed. The previous set - `admin`,
`manager`, `analyst`, with `Admin@2026` and friends - announced both an
account's privilege and its password from the username alone.
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
    job_title: str
    role: str
    password_hash: str


@lru_cache
def _directory() -> dict[str, AppUser]:
    settings = get_settings()
    # Five accounts covering the people who would actually open this: whoever
    # sets it up, two who run sites day to day, the analyst who questions the
    # numbers, and the director who reads the summary.
    people = [
        ("priya.raghavan", "Priya Raghavan", "IT Systems Administrator", "ADMIN", settings.auth_password_1),
        ("daniel.okonkwo", "Daniel Okonkwo", "Regional Operations Manager", "OPERATIONS_MANAGER", settings.auth_password_2),
        ("sofia.marchetti", "Sofia Marchetti", "Site Operations Manager", "OPERATIONS_MANAGER", settings.auth_password_3),
        ("thomas.beaumont", "Thomas Beaumont", "Workforce Data Analyst", "DATA_ANALYST", settings.auth_password_4),
        ("grace.lindqvist", "Grace Lindqvist", "Director of Operations", "EXECUTIVE", settings.auth_password_5),
    ]
    users = [
        AppUser(
            username=username,
            full_name=full_name,
            job_title=job_title,
            role=role,
            password_hash=hash_password(password),
        )
        for username, full_name, job_title, role, password in people
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


def list_users() -> list[AppUser]:
    """Every account, for a handover sheet. Never carries a password."""
    return list(_directory().values())
