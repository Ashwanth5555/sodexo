"""Data Quality over the client's uploaded files.

This used to report the ingestion_batch lineage from 16 mock source
endpoints. With no canonical dataset there is no ingestion, so the question
changes from "did every source system sync cleanly" to "is the file you gave
us actually usable" - which is the only data-quality question a client
uploading one file can act on.

Everything here is computed live from the stored file and its mapping, and
every issue is phrased in terms of the columns the client chose.
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UploadedDataset
from app.services.datasets import series as series_builder
from app.services.datasets import storage

logger = logging.getLogger(__name__)


def _quality_status(usable: int, total: int) -> str:
    """Graded on the share of rows that survive the mapping."""
    if total == 0:
        return "No Data"
    rate = 1 - (usable / total)
    if rate == 0:
        return "Good"
    if rate < 0.10:
        return "Warning"
    return "Poor"


def _dataset_health(dataset: UploadedDataset) -> dict:
    """Row counts and issues for one uploaded file."""
    received = dataset.row_count or 0
    row = {
        "source_system": "Upload",
        "source_endpoint": dataset.original_filename,
        "dataset_label": dataset.name,
        "dataset_id": dataset.dataset_id,
        "status": "CONFIGURED" if dataset.status == "configured" else "AWAITING MAPPING",
        "started_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "completed_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "records_received": received,
        "records_loaded": 0,
        "records_rejected": 0,
        "freshness_hours": None,
        "quality_status": "No Data",
        "issues": [],
        "mapping_source": dataset.mapping_source,
        "series_count": 0,
    }

    if dataset.created_at:
        row["freshness_hours"] = round((datetime.utcnow() - dataset.created_at).total_seconds() / 3600, 1)

    # What the reader changed to make the file usable. Listed first: it
    # explains the row counts underneath it, and a client checking whether
    # the app understood their spreadsheet looks here.
    row["issues"].extend(dataset.cleaning_notes or [])

    if not dataset.date_column or not dataset.target_column:
        row["issues"].append("Not mapped yet - choose a date column and a column to forecast.")
        return row

    try:
        frame = storage.load_frame(dataset.stored_filename)
    except storage.DatasetError as exc:
        row["status"] = "FILE MISSING"
        row["quality_status"] = "Poor"
        row["issues"].append(str(exc))
        return row

    # "Loaded" means a row the mapping can actually use: a readable date and
    # a numeric target. Anything else is dropped before training, so counting
    # it as loaded would overstate what the models saw.
    dates = pd.to_datetime(frame.get(dataset.date_column), errors="coerce")
    values = pd.to_numeric(frame.get(dataset.target_column), errors="coerce")
    usable_mask = dates.notna() & values.notna()
    usable = int(usable_mask.sum())

    row["records_loaded"] = usable
    row["records_rejected"] = max(0, len(frame) - usable)
    row["quality_status"] = _quality_status(usable, len(frame))

    bad_dates = int(dates.isna().sum())
    bad_values = int(values.isna().sum())
    if bad_dates:
        row["issues"].append(f"{bad_dates:,} row(s) have no readable date in '{dataset.date_column}'.")
    if bad_values:
        row["issues"].append(f"{bad_values:,} row(s) have no number in '{dataset.target_column}'.")

    if dataset.capacity_column and dataset.capacity_column in frame.columns:
        planned = pd.to_numeric(frame[dataset.capacity_column], errors="coerce")
        missing_planned = int((usable_mask & planned.isna()).sum())
        if missing_planned:
            row["issues"].append(
                f"{missing_planned:,} usable row(s) have no value in '{dataset.capacity_column}' - "
                "those periods are left out of Staffing Impact rather than counted as nothing planned."
            )

    # Duplicated periods are not an error - the aggregation setting exists
    # for exactly that - but they change what the numbers mean, so say so.
    subset = [dataset.date_column]
    subset.extend(c for c in (dataset.group_columns or []) if c in frame.columns)
    duplicates = int(frame.loc[usable_mask].duplicated(subset=subset).sum())
    if duplicates:
        row["issues"].append(
            f"{duplicates:,} row(s) share a period with another row; they are combined by "
            f"{'sum' if dataset.aggregation == 'sum' else 'average'}."
        )

    try:
        series = series_builder.list_series(dataset)
        row["series_count"] = len(series)
        if not series:
            row["issues"].append("This mapping produces no forecastable series - check the group column.")
    except Exception as exc:
        logger.info("Could not list series for %s", dataset.dataset_id, exc_info=True)
        row["issues"].append(f"Could not build series from this mapping: {exc}")

    return row


def get_summary(db: Session) -> dict:
    datasets = db.execute(
        select(UploadedDataset).order_by(UploadedDataset.created_at.desc())
    ).scalars().all()

    sources = [_dataset_health(d) for d in datasets]
    received = sum(s["records_received"] for s in sources)
    rejected = sum(s["records_rejected"] for s in sources)

    # Worst-case roll-up rather than an aggregate rate: one badly broken file
    # among several good ones is the thing the client needs to see.
    if not sources:
        overall = "No Data"
    elif any(s["quality_status"] == "Poor" for s in sources):
        overall = "Poor"
    elif any(s["quality_status"] == "Warning" for s in sources):
        overall = "Warning"
    else:
        overall = "Good"

    return {
        "sources": sources,
        "summary": {
            "total_sources": len(sources),
            "total_records_received": received,
            "total_records_rejected": rejected,
            "overall_quality_status": overall,
        },
        "empty_message": "No data yet. Upload a CSV or Excel file to forecast it." if not sources else None,
    }
