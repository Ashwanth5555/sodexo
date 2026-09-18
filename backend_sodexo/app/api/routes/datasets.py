from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ModelPrediction, ModelRun, UploadedDataset
from app.services.datasets import series as series_builder
from app.services.datasets import storage, training

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _dataset_payload(dataset: UploadedDataset, include_profile: bool = True) -> dict:
    payload = {
        "dataset_id": dataset.dataset_id,
        "name": dataset.name,
        "original_filename": dataset.original_filename,
        "status": dataset.status,
        "row_count": dataset.row_count,
        "date_column": dataset.date_column,
        "target_column": dataset.target_column,
        "group_column": dataset.group_column,
        "frequency": dataset.frequency,
        "aggregation": dataset.aggregation,
        "horizon_periods": dataset.horizon_periods,
        "exog_columns": dataset.exog_columns or [],
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "series": [
            {"series_key": s.key, "series_label": s.label}
            for s in (series_builder.list_series(dataset) if dataset.status == "configured" else [])
        ],
    }
    if include_profile:
        payload["column_profile"] = dataset.column_profile or []
    return payload


@router.get("")
def list_datasets(db: Session = Depends(get_db)):
    datasets = db.execute(select(UploadedDataset).order_by(UploadedDataset.created_at.desc())).scalars().all()
    return {
        "datasets": [_dataset_payload(d, include_profile=False) for d in datasets],
        "frequencies": storage.FREQUENCIES,
        "aggregations": storage.AGGREGATIONS,
    }


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    dataset = db.get(UploadedDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' was not found.")
    return _dataset_payload(dataset)


@router.post("/upload")
async def upload_dataset(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Accept any CSV/Excel file, profile its columns and suggest a mapping.

    Nothing is forecast yet - the user confirms which column is the date and
    which is the value to forecast via /configure.
    """
    raw = await file.read()
    try:
        dataset_id, frame, profile = storage.save_upload(file.filename or "upload.csv", raw)
    except storage.DatasetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    suggestion = storage.suggest_mapping(profile, len(frame))
    frequency = storage.infer_frequency(frame, suggestion["date_column"]) if suggestion["date_column"] else "D"

    name = (file.filename or "Uploaded dataset").rsplit(".", 1)[0][:160]
    dataset = UploadedDataset(
        dataset_id=dataset_id,
        name=name,
        original_filename=file.filename or "upload.csv",
        stored_filename=f"{dataset_id}.csv",
        status="uploaded",
        row_count=len(frame),
        column_profile=profile,
        date_column=suggestion["date_column"],
        target_column=suggestion["target_column"],
        group_column=None,
        frequency=frequency,
        horizon_periods=series_builder.default_horizon(frequency),
        exog_columns=[],
    )
    db.add(dataset)
    db.commit()

    return {
        **_dataset_payload(dataset),
        "suggestion": suggestion,
        "frequencies": storage.FREQUENCIES,
        "aggregations": storage.AGGREGATIONS,
        "preview": frame.head(8).astype(object).where(frame.head(8).notna(), None).to_dict("records"),
    }


class ConfigureRequest(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    date_column: str
    target_column: str
    group_column: str | None = None
    frequency: str = "D"
    aggregation: str = "sum"
    horizon_periods: int = Field(default=14, ge=1, le=365)
    exog_columns: list[str] = Field(default_factory=list)


@router.post("/{dataset_id}/configure")
def configure_dataset(dataset_id: str, payload: ConfigureRequest, db: Session = Depends(get_db)):
    """Record how the file maps onto a time series, so it can be trained."""
    dataset = db.get(UploadedDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' was not found.")

    known = {c["name"] for c in (dataset.column_profile or [])}
    for column, role in ((payload.date_column, "date"), (payload.target_column, "target")):
        if column not in known:
            raise HTTPException(status_code=400, detail=f"'{column}' is not a column in this file ({role}).")
    if payload.group_column and payload.group_column not in known:
        raise HTTPException(status_code=400, detail=f"'{payload.group_column}' is not a column in this file (group).")
    if payload.date_column == payload.target_column:
        raise HTTPException(status_code=400, detail="The date column and the forecast column must be different.")
    if payload.frequency not in storage.FREQUENCY_VALUES:
        raise HTTPException(status_code=400, detail=f"Unsupported frequency '{payload.frequency}'.")

    exog = [c for c in payload.exog_columns if c in known and c not in (payload.date_column, payload.target_column, payload.group_column)]

    dataset.name = (payload.name or dataset.name)[:160]
    dataset.date_column = payload.date_column
    dataset.target_column = payload.target_column
    dataset.group_column = payload.group_column or None
    dataset.frequency = payload.frequency
    dataset.aggregation = payload.aggregation if payload.aggregation in ("sum", "mean") else "sum"
    dataset.horizon_periods = payload.horizon_periods
    dataset.exog_columns = exog
    dataset.status = "configured"
    dataset.configured_at = datetime.utcnow()
    db.commit()

    # Surface an unusable mapping now, at configure time, rather than letting
    # the user press Train and watch every model fail.
    specs = series_builder.list_series(dataset)
    if not specs:
        raise HTTPException(status_code=400, detail="That mapping produces no series - check the group column.")
    try:
        preview = series_builder.build_history(dataset, specs[0])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        **_dataset_payload(dataset),
        "series_count": len(specs),
        "history_periods": len(preview),
        "history_start": preview["date"].iloc[0].isoformat(),
        "history_end": preview["date"].iloc[-1].isoformat(),
    }


@router.post("/{dataset_id}/train")
def train_dataset(dataset_id: str, db: Session = Depends(get_db)):
    """Run the full 13-model portfolio over every series in this dataset."""
    try:
        job_id = training.start_dataset_training(dataset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "RUNNING"}


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str, db: Session = Depends(get_db)):
    """Remove a dataset, its stored file and everything trained from it."""
    dataset = db.get(UploadedDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' was not found.")

    keys = [s.key for s in series_builder.list_series(dataset)] if dataset.date_column else []
    if keys:
        run_ids = db.execute(select(ModelRun.id).where(ModelRun.series_key.in_(keys))).scalars().all()
        if run_ids:
            for prediction in db.execute(select(ModelPrediction).where(ModelPrediction.model_run_id.in_(run_ids))).scalars():
                db.delete(prediction)
        for run in db.execute(select(ModelRun).where(ModelRun.series_key.in_(keys))).scalars():
            db.delete(run)

    storage.delete_stored_file(dataset.stored_filename)
    db.delete(dataset)
    db.commit()
    return {"deleted": dataset_id}
