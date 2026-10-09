from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ModelRun, TrainingJob, UploadedDataset
from app.services.datasets import training as dataset_training
from app.services.forecasting import results
from app.services.forecasting.contract import MODEL_SPECS

router = APIRouter(prefix="/api/models", tags=["models"])


@router.post("/train")
def train_all_models(window_days: int | None = Query(None, ge=60, le=730, description="Train on only the most recent N days. Fewer rows means faster fits; ignored if it would leave too little history to validate against."), db: Session = Depends(get_db)):
    """Train the whole model portfolio on the uploaded data.

    Every series in this app comes from a file the client uploaded, so this
    trains the most recently configured one. It used to call the built-in
    runner, which has had nothing to train since the sample data was removed -
    the only working path was a button on the Data Quality page, and that page
    is now upload and inspection only.
    """
    dataset = db.execute(
        select(UploadedDataset)
        .where(UploadedDataset.status == "configured")
        .order_by(UploadedDataset.configured_at.desc())
    ).scalars().first()
    if dataset is None:
        raise HTTPException(
            status_code=409,
            detail="No data to train. Upload a CSV or Excel file on the Data Quality page first.",
        )
    try:
        job_id = dataset_training.start_dataset_training(dataset.dataset_id, window_days=window_days)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"job_id": job_id, "status": "RUNNING", "window_days": window_days}


@router.get("/training-status")
def training_status(db: Session = Depends(get_db)):
    """The run the training page reports on.

    A finished job is only reported while the runs it produced still exist.
    Deleting a dataset now takes its jobs with it, but a job can also outlive
    its data another way - an older database, a run whose series were remapped -
    and the page reads "Last run completed, 26/26" over a leaderboard that says
    no training has happened. A running job is always reported, because it has
    not written its runs yet.
    """
    job = db.execute(select(TrainingJob).order_by(TrainingJob.started_at.desc())).scalars().first()
    if not job:
        return {"job": None}
    if job.status != "RUNNING":
        produced = db.execute(
            select(ModelRun.id).where(ModelRun.job_id == job.job_id).limit(1)
        ).scalars().first()
        if produced is None:
            return {"job": None}
    return {
        "job": {
            "job_id": job.job_id, "status": job.status, "started_at": job.started_at, "completed_at": job.completed_at,
            "current_series": job.current_series, "current_model": job.current_model,
            "total_steps": job.total_steps, "completed_steps": job.completed_steps, "error_message": job.error_message,
        }
    }


def _latest_job(db: Session) -> TrainingJob | None:
    return db.execute(select(TrainingJob).order_by(TrainingJob.started_at.desc())).scalars().first()


def _run_payload(model_id: str, label: str, run: ModelRun | None) -> dict:
    if run is None:
        return {
            "model_id": model_id, "model_label": label, "status": "pending",
            "evaluation_mode": None, "folds": None, "train_rows": None, "test_rows": None,
            "mape": None, "rmse": None, "mae": None, "wape": None, "bias": None, "accuracy": None,
            "rank": None, "is_champion": False, "runtime_seconds": None, "failure_reason": None, "trained_at": None,
        }
    return {
        "model_id": run.model_id, "model_label": run.model_label, "status": run.status,
        "evaluation_mode": run.evaluation_mode, "folds": run.folds,
        "train_rows": run.train_rows, "test_rows": run.test_rows,
        "mape": run.mape, "rmse": run.rmse, "mae": run.mae, "wape": run.wape, "bias": run.bias, "accuracy": run.accuracy,
        "rank": run.rank, "is_champion": run.is_champion, "runtime_seconds": run.runtime_seconds,
        "failure_reason": run.failure_reason, "trained_at": run.trained_at.isoformat(),
    }


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    """Every model's outcome for every series from the most recent training
    run - completed, failed, not_applicable, or (while a run is still in
    progress) pending, so the full model roster is always visible per
    series rather than only whichever models have finished so far."""
    job = _latest_job(db)
    by_series = results.latest_runs_by_series(db)

    # Built-in series first, then every configured uploaded one, each showing
    # its own most recent training run.
    known: list[tuple[str, str]] = []
    known += [(s.key, s.label) for s in dataset_training.all_uploaded_series(db)]

    series_payload = []
    for key, label in known:
        series_runs = by_series.get(key, {})
        if not series_runs:
            continue  # an uploaded series that has never been trained
        models = [_run_payload(model_id, model_label, series_runs.get(model_id)) for model_id, model_label, _dep in MODEL_SPECS]
        models.sort(key=lambda m: (m["status"] == "pending", m["rank"] is None, m["rank"] if m["rank"] is not None else 0, m["model_label"]))
        series_payload.append({"series_key": key, "series_label": label, "models": models})
    return {
        "job_id": job.job_id if job else None,
        "job_status": job.status if job else None,
        "series": series_payload,
    }


@router.get("/champion")
def champions(db: Session = Depends(get_db)):
    job = _latest_job(db)
    runs = results.champions(db)
    labels: dict[str, str] = {}
    labels.update({s.key: s.label for s in dataset_training.all_uploaded_series(db)})
    return {
        "job_id": job.job_id if job else None,
        "champions": [
            {
                "series_key": r.series_key,
                "series_label": labels.get(r.series_key, r.series_key),
                "model_id": r.model_id, "model_label": r.model_label,
                "mape": r.mape, "accuracy": r.accuracy, "rmse": r.rmse,
            }
            for r in runs
        ],
    }
