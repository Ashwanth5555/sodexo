"""Reading trained results back, per series.

There are two ways to start a training run - "Train All Models" for the built-in
series, and Train on an uploaded dataset - so the most recent job does NOT
cover every series. Reading results from "the latest job" would make whichever
set was not trained last look untrained, silently hiding real forecasts.

Everything here therefore resolves results per series: each series' own most
recent training job, independent of any other series.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ModelRun


def latest_job_id_for_series(db: Session, series_key: str) -> str | None:
    """The job_id of the most recent run that trained this series."""
    return db.execute(
        select(ModelRun.job_id)
        .where(ModelRun.series_key == series_key)
        .order_by(ModelRun.trained_at.desc(), ModelRun.id.desc())
        .limit(1)
    ).scalars().first()


def get_run(db: Session, series_key: str, model_id: str | None = None) -> ModelRun | None:
    """One series' champion (or a named model) from its own latest job."""
    job_id = latest_job_id_for_series(db, series_key)
    if job_id is None:
        return None
    query = select(ModelRun).where(ModelRun.job_id == job_id, ModelRun.series_key == series_key)
    if model_id:
        query = query.where(ModelRun.model_id == model_id)
    else:
        query = query.where(ModelRun.is_champion.is_(True))
    return db.execute(query).scalars().first()


def latest_runs_by_series(db: Session) -> dict[str, dict[str, ModelRun]]:
    """{series_key: {model_id: run}} taking each series' own latest job."""
    # One row per series: its newest trained_at, then the job that produced it.
    newest = (
        select(ModelRun.series_key, func.max(ModelRun.trained_at).label("trained_at"))
        .group_by(ModelRun.series_key)
        .subquery()
    )
    job_ids = db.execute(
        select(ModelRun.series_key, ModelRun.job_id)
        .join(newest, (ModelRun.series_key == newest.c.series_key) & (ModelRun.trained_at == newest.c.trained_at))
    ).all()
    latest_job_for = {series_key: job_id for series_key, job_id in job_ids}
    if not latest_job_for:
        return {}

    runs = db.execute(select(ModelRun).where(ModelRun.job_id.in_(set(latest_job_for.values())))).scalars().all()
    out: dict[str, dict[str, ModelRun]] = {}
    for run in runs:
        if latest_job_for.get(run.series_key) != run.job_id:
            continue
        out.setdefault(run.series_key, {})[run.model_id] = run
    return out


def champions(db: Session) -> list[ModelRun]:
    """Every series' current champion, each from that series' own latest job."""
    return [
        run
        for runs in latest_runs_by_series(db).values()
        for run in runs.values()
        if run.is_champion
    ]
