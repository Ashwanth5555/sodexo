"""Training entry point for uploaded datasets.

Builds each series' frames from the user's column mapping and hands them to the
same run_portfolio the built-in series use, so an uploaded dataset gets the
full 13-model portfolio, rolling-origin CV, the same metrics and the same
champion selection.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import ModelRun, TrainingJob, UploadedDataset
from app.services.datasets import series as series_builder
from app.services.forecasting.concurrency import ProgressReporter, finalise_job, resolve_skipped_models, run_series_in_parallel
from app.services.forecasting.contract import MODEL_SPECS
from app.services.forecasting.engine import run_portfolio
from app.services.forecasting.validation_plan import resolve_seasonal_period

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_job_id: str | None = None


def is_training_active() -> bool:
    with _lock:
        return _active_job_id is not None


def train_uploaded_series(db: Session, dataset: UploadedDataset, spec: series_builder.UploadedSeries, job_id: str, on_step=None, skip_models=None, window_days: int | None = None, on_start=None) -> list[ModelRun]:
    history = series_builder.build_history(dataset, spec)
    history = _apply_window(history, window_days)
    grain = series_builder.grain_for(dataset.frequency or "D")
    seasonal_period = resolve_seasonal_period(history["target"], grain["candidates"], grain["cycle"])
    ctx = series_builder.context_for(dataset, history, seasonal_period)
    horizon = dataset.horizon_periods or grain["default_horizon"]
    future = series_builder.build_future(dataset, history, horizon)
    return run_portfolio(db, spec.key, history, future, ctx, job_id, on_step=on_step, on_start=on_start, skip_models=skip_models)


def _apply_window(history: pd.DataFrame, window_days: int | None) -> pd.DataFrame:
    """Keep only the most recent window_days of history.

    The training-window control had no effect on uploaded data: the route took
    the value and dropped it, so picking "120 days (fastest)" still fitted
    every model against two years of hourly history. Ignored when the window
    would leave too little to validate against, because a faster run that
    cannot be scored is worth less than a slow one.
    """
    if not window_days or history.empty:
        return history
    cutoff = history["date"].max() - pd.Timedelta(days=window_days)
    trimmed = history[history["date"] > cutoff]
    if len(trimmed) < series_builder.MIN_ROWS_PER_SERIES * 3:
        logger.info("Ignoring the %d-day window: it leaves only %d rows.", window_days, len(trimmed))
        return history
    return trimmed


def start_dataset_training(dataset_id: str, window_days: int | None = None) -> str:
    """Kick off training for one uploaded dataset in a background thread.

    Mirrors the built-in runner: the fitting is CPU-bound, so it must not run on
    the event loop that the status endpoint needs to keep answering.
    """
    global _active_job_id
    db = SessionLocal()
    try:
        dataset = db.get(UploadedDataset, dataset_id)
        if dataset is None:
            raise LookupError(f"Dataset '{dataset_id}' was not found.")
        if not dataset.date_column or not dataset.target_column:
            raise ValueError("Choose a date column and a column to forecast before training.")
        specs = series_builder.list_series(dataset)
        if not specs:
            raise ValueError("This dataset produced no forecastable series - check the column mapping.")
        total_steps = len(specs) * len(MODEL_SPECS)
    finally:
        db.close()

    with _lock:
        if _active_job_id is not None:
            raise RuntimeError("Training is already running. Wait for it to finish before starting another run.")
        job_id = f"JOB-{uuid.uuid4().hex[:12]}"
        _active_job_id = job_id

    db = SessionLocal()
    try:
        db.add(TrainingJob(job_id=job_id, status="RUNNING", started_at=datetime.utcnow(), total_steps=total_steps))
        db.commit()
    finally:
        db.close()

    thread = threading.Thread(target=_run_job, args=(job_id, dataset_id, window_days), daemon=True)
    thread.start()
    return job_id


def _train_one(dataset_id: str, spec: series_builder.UploadedSeries, job_id: str, progress: ProgressReporter, skip_models, window_days: int | None = None) -> None:
    """Fit one uploaded series on its own session, in its own thread."""
    db = SessionLocal()
    try:
        dataset = db.get(UploadedDataset, dataset_id)
        if dataset is None:
            raise LookupError(f"Dataset '{dataset_id}' disappeared mid-training.")
        train_uploaded_series(
            db, dataset, spec, job_id,
            on_step=lambda label: progress.step(label, spec.label),
            on_start=lambda label: progress.starting(label, spec.label),
            skip_models=skip_models,
            window_days=window_days,
        )
    finally:
        db.close()


def _write_off_series(spec: series_builder.UploadedSeries, job_id: str, exc: Exception, progress: ProgressReporter) -> None:
    """Mark every model in an unusable series as failed.

    One bad series - too little history, a group whose rows do not resample -
    must not abandon the rest of the dataset. Recording every model for it
    keeps the leaderboard accounting for the series instead of silently
    dropping it, and advances the progress bar by the whole portfolio so the
    job cannot finish reading short of its own total.
    """
    db = SessionLocal()
    try:
        for model_id, label, _dependency in MODEL_SPECS:
            db.add(ModelRun(
                job_id=job_id, series_key=spec.key, model_id=model_id, model_label=label,
                status="failed", failure_reason=str(exc)[:490],
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    progress.advance(len(MODEL_SPECS), None, spec.label)


def _run_job(job_id: str, dataset_id: str, window_days: int | None = None) -> None:
    global _active_job_id
    progress = ProgressReporter(job_id)
    try:
        db = SessionLocal()
        try:
            dataset = db.get(UploadedDataset, dataset_id)
            specs = series_builder.list_series(dataset) if dataset is not None else []
        finally:
            db.close()

        skip_models = resolve_skipped_models(len(specs))
        if skip_models:
            logger.info("Job %s: %d series, skipping %s", job_id, len(specs), ", ".join(sorted(skip_models)))
        failures = run_series_in_parallel(
            specs,
            work=lambda spec: _train_one(dataset_id, spec, job_id, progress, skip_models, window_days),
            describe=lambda spec: spec.label,
            on_failure=lambda spec, exc: _write_off_series(spec, job_id, exc, progress),
        )
        finalise_job(job_id, failures, total_units=len(specs))
    except Exception as exc:
        logger.exception("Dataset training job %s failed", job_id)
        finalise_job(job_id, [str(exc)], total_units=1)
    finally:
        with _lock:
            _active_job_id = None


def all_uploaded_series(db: Session, trained_only: bool = False) -> list[series_builder.UploadedSeries]:
    """Every configured uploaded dataset's series.

    trained_only=True keeps just the series that have at least one completed
    model run. The series picker uses it: a dataset that has been mapped but
    never trained has nothing to show, and listing it only produces a dead
    option that errors with "no completed model found" when selected.
    """
    datasets = db.execute(
        select(UploadedDataset).where(UploadedDataset.status == "configured").order_by(UploadedDataset.created_at)
    ).scalars().all()
    out: list[series_builder.UploadedSeries] = []
    for dataset in datasets:
        try:
            out.extend(series_builder.list_series(dataset))
        except Exception:
            # A dataset whose file went missing should not break the picker.
            continue

    if not trained_only or not out:
        return out

    trained_keys = set(db.execute(
        select(ModelRun.series_key)
        .where(ModelRun.series_key.in_([s.key for s in out]), ModelRun.status == "completed")
        .distinct()
    ).scalars().all())
    return [s for s in out if s.key in trained_keys]
