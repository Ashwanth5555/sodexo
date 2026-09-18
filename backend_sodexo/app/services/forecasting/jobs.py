"""Background training-job runner. Training runs in a real OS thread (not
FastAPI BackgroundTasks) so the CPU-bound model fitting doesn't block the
event loop that the status-polling endpoint needs to keep answering -
mirrors Meriton's own use of a background thread for the same reason.
"""
import threading
import uuid
from datetime import datetime

from app.core.database import SessionLocal
from app.models import TrainingJob
from app.services.forecasting.contract import MODEL_SPECS, SERIES, SLOTS_PER_DAY
from app.services.forecasting.engine import train_series

_lock = threading.Lock()
_active_job_id: str | None = None


def is_training_active() -> bool:
    with _lock:
        return _active_job_id is not None


def start_training_job(window_days: int | None = None) -> str:
    global _active_job_id
    with _lock:
        if _active_job_id is not None:
            raise RuntimeError("Training is already running. Wait for it to finish before starting another run.")
        job_id = f"JOB-{uuid.uuid4().hex[:12]}"
        _active_job_id = job_id

    db = SessionLocal()
    try:
        db.add(TrainingJob(job_id=job_id, status="RUNNING", started_at=datetime.utcnow(), total_steps=len(SERIES) * len(MODEL_SPECS)))
        db.commit()
    finally:
        db.close()

    max_training_slots = window_days * SLOTS_PER_DAY if window_days and window_days > 0 else None
    thread = threading.Thread(target=_run_job, args=(job_id, max_training_slots), daemon=True)
    thread.start()
    return job_id


def _run_job(job_id: str, max_training_slots: int | None = None) -> None:
    global _active_job_id
    db = SessionLocal()
    try:
        job = db.get(TrainingJob, job_id)
        completed_steps = 0

        def on_step(model_label: str, series_key: str) -> None:
            nonlocal completed_steps
            completed_steps += 1
            job.current_series = series_key
            job.current_model = model_label
            job.completed_steps = completed_steps
            db.commit()

        for series in SERIES:
            train_series(db, series, job_id, on_step=lambda label, _series=series: on_step(label, _series.key), max_training_slots=max_training_slots)

        job.status = "COMPLETED"
        job.completed_at = datetime.utcnow()
        job.current_series = None
        job.current_model = None
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(TrainingJob, job_id)
        job.status = "FAILED"
        job.error_message = str(exc)[:990]
        job.completed_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
        with _lock:
            _active_job_id = None
