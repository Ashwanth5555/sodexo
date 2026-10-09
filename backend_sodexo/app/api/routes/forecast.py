from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ModelPrediction, ModelRun, TrainingJob
from app.models import UploadedDataset
from app.services.datasets import series as dataset_series
from app.services.datasets import training as dataset_training
from app.services.datasets import workspace as dataset_workspace
from app.services.forecasting import results

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("/series")
def list_series(
    scope: str = Query("trained", pattern="^(trained|mapped)$"),
    db: Session = Depends(get_db),
):
    """Every series the app can offer - all of them uploaded.

    Two scopes, because the pages need different things:

    - "trained" (default) lists only series with a completed model run. Anything
      reading a forecast needs that, or selecting a series errors with "no
      completed model found".
    - "mapped" lists every series the column mapping produces, trained or not.
      The exploratory pages - data analysis, pre-processing, drivers - read the
      history rather than a forecast, so they work the moment a file is mapped,
      and making them wait for a training run left them showing an empty picker
      and a spinner that never resolved.
    """
    uploaded = [
        {"series_key": s.key, "series_label": s.label, "source": "uploaded", "dataset_id": s.dataset_id}
        for s in dataset_training.all_uploaded_series(db, trained_only=scope == "trained")
    ]
    return {"series": uploaded}


@router.get("")
def get_forecast(
    series_key: str = Query(...),
    model_id: str | None = Query(None, description="Omit to use the series champion"),
    db: Session = Depends(get_db),
):
    # Per series, not "the latest job": training an uploaded dataset must not
    # blank out the built-in series' forecasts, or vice versa.
    run = results.get_run(db, series_key, model_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"No completed model found for series '{series_key}'" + (f" and model '{model_id}'" if model_id else " (no champion - all models may be not_applicable or failed)"))

    predictions = db.execute(
        select(ModelPrediction).where(ModelPrediction.model_run_id == run.id).order_by(ModelPrediction.business_date, ModelPrediction.hour_of_day)
    ).scalars().all()

    # What this series actually forecasts, so the UI never has to assume the
    # canonical target/grain - an uploaded series has its own.
    # No canonical default to fall back on - every series is an upload, and a
    # label borrowed from data that no longer exists would name the wrong
    # column at the wrong grain.
    target_label, frequency_label = None, None
    if dataset_workspace.is_uploaded_series(series_key):
        dataset = db.get(UploadedDataset, series_key.split("__", 1)[0])
        if dataset is not None:
            target_label = dataset.target_column or "value"
            frequency_label = dataset_series.grain_for(dataset.frequency or "D")["label"]

    return {
        "series_key": run.series_key, "model_id": run.model_id, "model_label": run.model_label, "is_champion": run.is_champion,
        "target_label": target_label, "frequency_label": frequency_label,
        "trained_at": run.trained_at.isoformat() if run.trained_at else None,
        "evaluation_mode": run.evaluation_mode, "folds": run.folds, "train_rows": run.train_rows, "test_rows": run.test_rows,
        "metrics": {"mape": run.mape, "rmse": run.rmse, "mae": run.mae, "wape": run.wape, "bias": run.bias, "accuracy": run.accuracy},
        "points": [
            {
                "date": f"{p.business_date.isoformat()}T{p.hour_of_day:02d}:00:00",
                "actual": p.actual, "predicted": p.predicted, "lower": p.lower, "upper": p.upper,
                "is_future": p.is_future, "fold": p.fold,
            }
            for p in predictions
        ],
    }
