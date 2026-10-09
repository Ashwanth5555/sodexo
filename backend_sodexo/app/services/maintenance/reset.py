"""Wipe every trace of a previous tenant's data.

This deployment is a single-tenant POC handed to one client at a time: they
upload their own file, look at it, and the container is restarted before the
next person sees it. Leaving data behind between restarts is not just untidy,
it is actively misleading - a page that still holds the previous dataset's
numbers looks like the client's own data until someone reads the series name.

So the rule is blunt on purpose: on startup, unless RESET_ON_STARTUP is turned
off, everything goes. Canonical facts, uploaded datasets, the files behind
them, every model run and training job. The app comes up empty and the client
either uploads their file or loads the Sodexo sample deliberately.

RESET_ON_STARTUP=false is for local development, where losing an ingested
database on every reload would be miserable.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, inspect
from sqlalchemy.orm import Session

from app.models import (
    DataQualityIssue,
    IngestionBatch,
    ModelPrediction,
    ModelRun,
    SeriesFeatureSelection,
    TrainingJob,
    UploadedDataset,
)

logger = logging.getLogger(__name__)

# Child-before-parent, so a delete never trips a foreign key on a backend that
# enforces them. SQLite does not by default, but Postgres (the documented
# alternative in .env.example) does, and this should not depend on which.
DELETION_ORDER = [
    ModelPrediction,
    ModelRun,
    TrainingJob,
    SeriesFeatureSelection,
    DataQualityIssue,
    IngestionBatch,
    UploadedDataset,
]


def purge_upload_files(upload_dir: Path) -> int:
    """Delete the stored CSVs behind uploaded datasets.

    Done by directory sweep rather than by walking UploadedDataset rows: a row
    whose file write succeeded but whose commit did not would otherwise leave
    an orphan on disk forever, and on a POC container that disk is the thing
    filling up.
    """
    if not upload_dir.exists():
        return 0
    removed = 0
    for path in upload_dir.iterdir():
        if not path.is_file():
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            # A locked file must not stop the database reset - the row is going
            # either way, and an orphaned file is far less harmful than coming
            # up with the previous tenant's data still queryable.
            logger.warning("Could not delete upload file %s: %s", path, exc)
    return removed


def reset_all(db: Session, upload_dir: Path | None = None) -> dict:
    """Empty every application table and remove uploaded files.

    Returns a per-table count of what was deleted, so startup can log what it
    actually did rather than claiming success blindly.
    """
    existing_tables = set(inspect(db.get_bind()).get_table_names())

    deleted: dict[str, int] = {}
    for model in DELETION_ORDER:
        table = model.__tablename__
        # create_all runs before this, but a table can still be absent if the
        # model was added after an older database file was created.
        if table not in existing_tables:
            continue
        result = db.execute(delete(model))
        if result.rowcount:
            deleted[table] = result.rowcount
    db.commit()

    if upload_dir is not None:
        files = purge_upload_files(upload_dir)
        if files:
            deleted["<upload files>"] = files

    return deleted


def reset_on_startup(upload_dir: Path | None = None) -> dict:
    """Entry point for the FastAPI startup hook."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        deleted = reset_all(db, upload_dir)
    finally:
        db.close()

    if deleted:
        summary = ", ".join(f"{table}={count}" for table, count in sorted(deleted.items()))
        logger.info("Startup reset removed: %s", summary)
    else:
        logger.info("Startup reset: nothing to remove, already empty.")
    return deleted
