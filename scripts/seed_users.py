"""Seed one demo user per RBAC role. POC only - plaintext demo password is
documented in README.md, never used outside local/demo environments.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
import app.models  # noqa: E402, F401
from app.models import AppUser  # noqa: E402

DEMO_PASSWORD = "Passw0rd!"

DEMO_USERS = [
    dict(username="exec1", role="EXECUTIVE", full_name="Erin Chen (Executive)", assigned_location_id=None, assigned_region_id=None),
    dict(username="ops1", role="OPERATIONS_MANAGER", full_name="Omar Diaz (Ops Manager)", assigned_location_id=None, assigned_region_id="REG01"),
    dict(username="mgr1", role="LOCATION_MANAGER", full_name="Maya Patel (Location Manager)", assigned_location_id="LOC01", assigned_region_id=None),
    dict(username="hr1", role="HR", full_name="Hana Kim (HR)", assigned_location_id=None, assigned_region_id=None),
    dict(username="payroll1", role="PAYROLL", full_name="Priya Rao (Payroll)", assigned_location_id=None, assigned_region_id=None),
    dict(username="ds1", role="DATA_SCIENTIST", full_name="Dana Singh (Data Scientist)", assigned_location_id=None, assigned_region_id=None),
    dict(username="admin1", role="ADMIN", full_name="Alex Nguyen (Admin)", assigned_location_id=None, assigned_region_id=None),
]


def main() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        for u in DEMO_USERS:
            existing = db.execute(select(AppUser).where(AppUser.username == u["username"])).scalar_one_or_none()
            if existing:
                continue
            db.add(AppUser(password_hash=hash_password(DEMO_PASSWORD), **u))
        db.commit()
        print(f"Seeded {len(DEMO_USERS)} demo users (password: {DEMO_PASSWORD})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
