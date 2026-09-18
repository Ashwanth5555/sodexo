"""Training entry point for uploaded datasets.

Builds each series' frames from the user's column mapping and hands them to the
same run_portfolio the built-in series use, so an uploaded dataset gets the
full 13-model portfolio, rolling-origin CV, the same metrics and the same
champion selection.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import ModelRun, TrainingJob, UploadedDataset
from app.services.datasets import series as series_builder
from app.services.forecasting.contract import MODEL_SPECS
from app.services.forecasting.engine import run_portfolio
from app.services.forecasting.validation_plan import resolve_seasonal_period

_lock = threading.Lock()
_active_job_id: str | None = None


def is_training_active() -> bool:
    with _lock:
        return _active_job_id is not None


def train_uploaded_series(db: Session, dataset: UploadedDataset, spec: series_builder.UploadedSeries, job_id: str, on_step=None) -> list[ModelRun]:
    history = series_builder.build_history(dataset, spec)
    grain = series_builder.grain_for(dataset.frequency or "D")
    seasonal_period = resolve_seasonal_period(history["target"], grain["candidates"], grain["cycle"])
    ctx = series_builder.context_for(dataset, history, seasonal_period)
    horizon = dataset.horizon_periods or grain["default_horizon"]
    future = series_builder.build_future(dataset, history, horizon)
    return run_portfolio(db, spec.key, history, future, ctx, job_id, on_step=on_step)


def start_dataset_training(dataset_id: str) -> str:
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

    thread = threading.Thread(target=_run_job, args=(job_id, dataset_id), daemon=True)
    thread.start()
    return job_id


def _run_job(job_id: str, dataset_id: str) -> None:
    global _active_job_id
    db = SessionLocal()
    try:
        job = db.get(TrainingJob, job_id)
        dataset = db.get(UploadedDataset, dataset_id)
        specs = series_builder.list_series(dataset)
        completed_steps = 0

        def on_step(model_label: str, series_label: str) -> None:
            nonlocal completed_steps
            completed_steps += 1
            job.current_series = series_label[:60]
            job.current_model = model_label
            job.completed_steps = completed_steps
            db.commit()

        failures: list[str] = []
        for spec in specs:
            try:
                train_uploaded_series(db, dataset, spec, job_id, on_step=lambda label, _s=spec: on_step(label, _s.label))
            except Exception as exc:
                # One unusable series (too little history, bad mapping for that
                # group) must not abandon the rest of the dataset. Record every
                # model as failed for it so the leaderboard still accounts for
                # the series, and carry on.
                db.rollback()
                failures.append(f"{spec.label}: {exc}")
                for model_id, label, _dependency in MODEL_SPECS:
                    db.add(ModelRun(
                        job_id=job_id, series_key=spec.key, model_id=model_id, model_label=label,
                        status="failed", failure_reason=str(exc)[:490],
                    ))
                    completed_steps += 1
                db.commit()

        job.status = "COMPLETED" if len(failures) < len(specs) else "FAILED"
        if failures:
            job.error_message = " | ".join(failures)[:990]
        job.completed_at = datetime.utcnow()
        job.current_series = None
        job.current_model = None
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(TrainingJob, job_id)
        if job is not None:
            job.status = "FAILED"
            job.error_message = str(exc)[:990]
            job.completed_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()
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
