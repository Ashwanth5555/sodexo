import logging
from datetime import datetime

import pandas as pd

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ModelPrediction, ModelRun, TrainingJob, UploadedDataset
from app.services.datasets import guidance
from app.services.datasets import mapping as mapping_service
from app.services.datasets import series as series_builder
from app.services.datasets import storage, training

logger = logging.getLogger(__name__)

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
        "group_columns": dataset.group_columns or [],
        "frequency": dataset.frequency,
        "aggregation": dataset.aggregation,
        "horizon_periods": dataset.horizon_periods,
        "exog_columns": dataset.exog_columns or [],
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "capacity_column": dataset.capacity_column,
        "mapping_source": dataset.mapping_source,
        "mapping_reasoning": dataset.mapping_reasoning,
        "cleaning_notes": dataset.cleaning_notes or [],
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


@router.get("/requirements")
def upload_requirements():
    """What this application accepts, straight from the code that enforces it.

    Declared ahead of /{dataset_id} because FastAPI matches in order and
    "requirements" would otherwise be read as a dataset id.
    """
    return guidance.requirements()


@router.get("/template", response_class=PlainTextResponse)
def download_template():
    """A working example file, not a header row.

    Uploading this unchanged exercises every feature - grouping, drivers and
    the planned-capacity comparison - so it doubles as a way to confirm the
    install before preparing a real export.
    """
    return PlainTextResponse(
        guidance.template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="forecast_upload_template.csv"'},
    )


@router.get("/{dataset_id}")
def get_dataset(
    dataset_id: str,
    include_preview: bool = Query(False, description="Also return the first rows of the file."),
    db: Session = Depends(get_db),
):
    """One dataset.

    include_preview costs a small read of the stored file, so it is opt-in:
    the mapping form wants the column profile and nothing else, while the
    upload page wants the rows back after a refresh - without them a file that
    is still on the server looks like it was never uploaded.
    """
    dataset = db.get(UploadedDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' was not found.")
    payload = _dataset_payload(dataset)
    if include_preview:
        payload["preview"] = storage.load_preview(dataset.stored_filename)
        # Recomputed rather than stored, so a refresh shows the same caveats
        # the upload did - a series too short for the seasonal models, a
        # grouping at the cap - rather than a file that looks cleaner than it
        # is simply because the page was reloaded.
        payload["warnings"] = _restore_warnings(dataset)
    return payload


def _restore_warnings(dataset: UploadedDataset) -> list[str]:
    if dataset.status != "configured":
        return []
    try:
        specs = series_builder.list_series(dataset)
        if not specs:
            return []
        periods = len(series_builder.build_history(dataset, specs[0]))
    except (ValueError, storage.DatasetError) as exc:
        logger.info("Not restoring warnings for %s: %s", dataset.dataset_id, exc)
        return []
    return _mapping_warnings(dataset, specs, periods)


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

    # The LLM reads the columns and proposes the whole mapping; it falls back
    # to profiling heuristics on any failure, so an upload never depends on
    # an external API being up.
    proposal = mapping_service.propose(frame, profile)
    suggestion = {
        "date_column": proposal["date_column"],
        "target_column": proposal["target_column"],
        "group_columns": proposal["group_columns"],
        "capacity_column": proposal["capacity_column"],
        "driver_columns": proposal["driver_columns"],
        "group_candidates": proposal.get("group_candidates", []),
        "source": proposal["source"],
        "reasoning": proposal["reasoning"],
    }
    frequency = proposal["frequency"]

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
        group_columns=suggestion["group_columns"],
        capacity_column=suggestion["capacity_column"],
        frequency=frequency,
        aggregation=proposal["aggregation"],
        horizon_periods=series_builder.default_horizon(frequency),
        exog_columns=suggestion["driver_columns"],
        mapping_source=proposal["source"],
        mapping_reasoning=proposal["reasoning"],
        cleaning_notes=list(frame.attrs.get("cleaning_notes") or []),
    )
    db.add(dataset)

    # Mark it ready here rather than waiting for a confirmation click. The
    # mapping is checked against the file before it is accepted - the target
    # has to be a number in every series, the frequency has to match the
    # observed spacing - so a proposal that survives that is as good as one a
    # client would have clicked past. It stays "uploaded" if it does not, and
    # the mapping can be corrected under Labor Forecasting - Data Preprocessing.
    warnings: list[str] = []
    db.flush()
    specs = series_builder.list_series(dataset)
    unusable = series_builder.unforecastable_series(dataset) if specs else ["the whole file"]
    if specs and not unusable:
        try:
            preview_history = series_builder.build_history(dataset, specs[0])
        except ValueError as exc:
            logger.info("Not auto-configuring %s: %s", dataset_id, exc)
            # Say why. A file that stops at "uploaded" used to come back with
            # an empty warnings list, so the page showed the preview, no
            # onward button and no reason - a dead end for anyone testing
            # with their own export.
            warnings = [f"This file was read, but it cannot be forecast yet: {exc}"]
        else:
            dataset.status = "configured"
            dataset.configured_at = datetime.utcnow()
            warnings = _mapping_warnings(dataset, specs, len(preview_history))
    elif not specs:
        missing = []
        if not dataset.date_column:
            missing.append("which column holds the date")
        if not dataset.target_column:
            missing.append("which column holds the value to forecast")
        warnings = [
            "This file was read, but it cannot be forecast yet: we could not work out "
            + " or ".join(missing or ["how to split it into a series"])
            + ". A forecast needs one column of dates and one column of numbers "
            "recorded against them."
        ]
    else:
        shown = ", ".join(unusable[:4]) + (" and others" if len(unusable) > 4 else "")
        warnings = [
            f"This file was read, but it cannot be forecast yet: {shown} has no period with both "
            f"a usable date and a number in '{dataset.target_column}'. Check that column, or "
            f"correct the mapping under Labor Forecasting."
        ]
    db.commit()

    return {
        **_dataset_payload(dataset),
        "suggestion": suggestion,
        "warnings": warnings,
        "frequencies": storage.FREQUENCIES,
        "aggregations": storage.AGGREGATIONS,
        "preview": frame.head(8).astype(object).where(frame.head(8).notna(), None).to_dict("records"),
    }


class ConfigureRequest(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    date_column: str
    target_column: str
    # One or more columns. Several means the series are the combinations
    # that actually occur in the data.
    group_columns: list[str] = Field(default_factory=list)
    # The column recording what was already planned/rostered/budgeted, used
    # by Staffing Impact. Optional: most files only record demand.
    capacity_column: str | None = None
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
    for column in payload.group_columns:
        if column not in known:
            raise HTTPException(status_code=400, detail=f"'{column}' is not a column in this file (group).")
        if column in (payload.date_column, payload.target_column, payload.capacity_column):
            raise HTTPException(
                status_code=400,
                detail=f"'{column}' is already used as the date, target or planned-capacity column - "
                       "it cannot also split the file into series.",
            )
    if payload.capacity_column:
        if payload.capacity_column not in known:
            raise HTTPException(status_code=400, detail=f"'{payload.capacity_column}' is not a column in this file (planned capacity).")
        if not series_builder.is_plausible_capacity(payload.capacity_column):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{payload.capacity_column}' is a calendar part, not a roster. Staffing Impact "
                    "compares the forecast against what was planned, so this column needs to record "
                    "rostered hours, scheduled headcount or a budget."
                ),
            )
        if payload.capacity_column == payload.target_column:
            raise HTTPException(
                status_code=400,
                detail="The planned-capacity column must differ from the column being forecast - "
                       "comparing a column against itself always shows a perfect match.",
            )
    if payload.date_column == payload.target_column:
        raise HTTPException(status_code=400, detail="The date column and the forecast column must be different.")
    if payload.frequency not in storage.FREQUENCY_VALUES:
        raise HTTPException(status_code=400, detail=f"Unsupported frequency '{payload.frequency}'.")

    # Capacity is excluded from the drivers on purpose: it records what was
    # planned in response to demand, so feeding it to the models lets them
    # predict demand from the roster that was built for it.
    reserved = {payload.date_column, payload.target_column, payload.capacity_column, *payload.group_columns}
    exog = [c for c in payload.exog_columns if c in known and c not in reserved]

    dataset.name = (payload.name or dataset.name)[:160]
    dataset.date_column = payload.date_column
    dataset.target_column = payload.target_column
    dataset.group_columns = list(payload.group_columns) or None
    dataset.capacity_column = payload.capacity_column or None
    dataset.frequency = payload.frequency
    dataset.aggregation = payload.aggregation if payload.aggregation in ("sum", "mean") else "sum"
    dataset.horizon_periods = payload.horizon_periods
    dataset.exog_columns = exog
    dataset.status = "configured"
    dataset.configured_at = datetime.utcnow()
    # Flushed, not committed: list_series and build_history read the mapping
    # back off the dataset, but a mapping that turns out to be unusable must
    # not survive the request that rejected it.
    db.flush()

    # Surface an unusable mapping now, at configure time, rather than letting
    # the user press Train and watch every model fail.
    specs = series_builder.list_series(dataset)
    if not specs:
        db.rollback()
        raise HTTPException(status_code=400, detail="That mapping produces no series - check the group column.")

    # Checked across every series, not just the first: a target that only some
    # of them record fails wherever it happens to be missing, and reporting
    # that from series one alone depends on which series sorts first.
    empty = series_builder.unforecastable_series(dataset)
    if empty:
        db.rollback()
        shown = ", ".join(empty[:3]) + ("..." if len(empty) > 3 else "")
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{payload.target_column}' has no numbers for {len(empty)} of the {len(specs)} series "
                f"({shown}). That column is only recorded for part of this file. Pick a column every "
                f"series records, or drop the grouping that splits them apart."
            ),
        )
    try:
        preview = series_builder.build_history(dataset, specs[0])
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()

    return {
        "warnings": _mapping_warnings(dataset, specs, len(preview)),
        **_dataset_payload(dataset),
        "series_count": len(specs),
        "history_periods": len(preview),
        "history_start": preview["date"].iloc[0].isoformat(),
        "history_end": preview["date"].iloc[-1].isoformat(),
    }


def _mapping_warnings(dataset: UploadedDataset, specs: list, periods: int) -> list[str]:
    """Problems that do not make the mapping invalid, but will disappoint.

    The big one is splitting a modest file into many series: each one is
    trained on its own slice, so a grouping that looks harmless can leave
    every series too short for the seasonal models. Saying this at mapping
    time is worth far more than thirteen `not_applicable` rows afterwards.
    """
    warnings: list[str] = []
    grain = series_builder.grain_for(dataset.frequency or "D")
    cycle = grain["cycle"]

    if len(specs) > 1 and periods < cycle * 2:
        columns = " + ".join(dataset.group_columns or [])
        warnings.append(
            f"Splitting by {columns} gives {len(specs)} series of about {periods} "
            f"{grain['label'].lower()} periods each. Seasonal models need at least "
            f"{cycle * 2} to fit a full cycle, so several models will report as not "
            f"applicable. Grouping by fewer columns would give longer series."
        )
    elif periods < cycle * 2:
        warnings.append(
            f"About {periods} {grain['label'].lower()} periods of history. Seasonal models "
            f"need {cycle * 2} to fit a full {grain['label'].lower()} cycle, so some will "
            "report as not applicable."
        )

    # A roster column that some series do not record at all. Legitimate - a
    # plan may cover only part of the estate - but worth saying, because the
    # column that prompted this was `occupied_beds`, which exists for the
    # catering sites and not the cafes, and looked like a roster until the
    # cafes reported every period as a total shortfall.
    if dataset.capacity_column:
        missing = series_builder.series_missing_column(dataset, dataset.capacity_column)
        if missing:
            shown = ", ".join(missing[:3]) + ("..." if len(missing) > 3 else "")
            warnings.append(
                f"'{dataset.capacity_column}' has no values for {len(missing)} of the {len(specs)} "
                f"series ({shown}). Those get no over/under-coverage comparison - if this column is "
                f"meant to be the roster for every site, check the file records it for all of them."
            )

    if len(specs) >= storage.MAX_GROUP_SERIES:
        warnings.append(
            f"This grouping hits the {storage.MAX_GROUP_SERIES}-series cap; further "
            "combinations were not created."
        )

    if len(specs) > 12:
        warnings.append(
            f"{len(specs)} series means {len(specs) * 13} model runs. Expect training to take a while."
        )

    return warnings


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

    # Matched on the series key's prefix rather than by re-deriving the series
    # list. That derivation re-reads the stored file and applies the CURRENT
    # mapping, so a mapping edited since training, or a file that has gone
    # missing, left runs behind - orphans that then showed up as somebody
    # else's leaderboard and kept a finished job on screen.
    runs = db.execute(
        select(ModelRun).where(ModelRun.series_key.startswith(f"{dataset_id}__", autoescape=True))
    ).scalars().all()
    run_ids = [r.id for r in runs]
    job_ids = {r.job_id for r in runs}
    if run_ids:
        for prediction in db.execute(
            select(ModelPrediction).where(ModelPrediction.model_run_id.in_(run_ids))
        ).scalars():
            db.delete(prediction)
    for run in runs:
        db.delete(run)
    db.flush()

    # A job outlives its runs otherwise, and the training page shows the most
    # recent one - so deleting the data left "Last run completed. 26/26" above
    # a leaderboard saying no training run had happened.
    for job_id in job_ids:
        remaining = db.execute(
            select(ModelRun.id).where(ModelRun.job_id == job_id).limit(1)
        ).scalars().first()
        if remaining is None:
            job = db.get(TrainingJob, job_id)
            if job is not None:
                db.delete(job)

    storage.delete_stored_file(dataset.stored_filename)
    db.delete(dataset)
    db.commit()
    return {"deleted": dataset_id, "model_runs_removed": len(run_ids)}

