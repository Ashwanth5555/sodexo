"""Run the full ingestion pipeline: mock API -> validate -> canonical DB.

Usage (from project root, with the mock API already running on :8100):
    ./.venv/Scripts/python scripts/run_ingestion.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
from app.services.ingestion.pipeline import run_full_pipeline  # noqa: E402


def main() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        batches = run_full_pipeline(db)
        print(f"\n{'Source':<10} {'Endpoint':<32} {'Status':<22} {'Received':>9} {'Loaded':>8} {'Rejected':>9}")
        for b in batches:
            print(
                f"{b.source_system:<10} {b.source_endpoint:<32} {b.status:<22} "
                f"{b.records_received:>9} {b.records_loaded:>8} {b.records_rejected:>9}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
