"""Workspace-tab data for the Labor Forecasting pages.

Every series comes from a file the client uploaded, so this module is now a
thin delegation layer over datasets/workspace.py, which does the real work
against the stored CSV and its mapping.

It used to hold two parallel implementations - one reading the canonical
Sodexo tables, one reading uploads - with a dispatch on the series key, and
three tabs that refused uploaded series outright because they depended on
the canonical schema. The canonical half is gone, so the dispatch and the
refusals go with it.

Kept as a module rather than folded into the routes because the route layer
should not know which storage a series lives in, and because a second
storage backend (a warehouse connector, say) would slot in here.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UploadedDataset
from app.services.datasets import workspace as dataset_workspace


def _require_uploaded(series_key: str) -> None:
    """Every series must be an uploaded one now.

    A stale bookmark or a cached frontend can still ask for a built-in key
    like LOC01_CAFE. Saying so plainly beats a KeyError surfacing as a 500.
    """
    if not dataset_workspace.is_uploaded_series(series_key):
        raise LookupError(
            f"Unknown series '{series_key}'. This application forecasts uploaded datasets only - "
            "upload a file on the Data Quality page to get started."
        )


def get_drivers(db: Session) -> dict:
    """Target and input columns for every configured dataset."""
    series_payload: list[dict] = []
    for dataset in db.execute(
        select(UploadedDataset).where(UploadedDataset.status == "configured").order_by(UploadedDataset.created_at)
    ).scalars().all():
        series_payload.extend(dataset_workspace.get_drivers_for_dataset(dataset))

    return {
        "series": series_payload,
        # There is no fixed schema to compare against any more, so nothing
        # can be listed as "missing from the dataset" - whatever the client
        # did not upload is simply not there, and only they know what that is.
        "not_in_dataset": [],
        "note": (
            "Input columns come from the uploaded file itself. Driver columns are fed to the models as "
            "exogenous inputs; the planned-capacity column is deliberately excluded, because it records "
            "what was scheduled in response to demand and would let a model predict demand from the roster "
            "built for it."
        ),
    }


def set_input_column_enabled(db: Session, series_key: str, column: str, enabled: bool) -> dict:
    _require_uploaded(series_key)
    dataset_workspace.set_input_column_enabled(db, series_key, column, enabled)
    return get_drivers(db)


def get_preprocessing(db: Session, series_key: str) -> dict:
    _require_uploaded(series_key)
    return dataset_workspace.get_preprocessing(db, series_key)


def get_eda(db: Session, series_key: str) -> dict:
    _require_uploaded(series_key)
    return dataset_workspace.get_eda(db, series_key)


def get_confidence(db: Session, series_key: str, model_id: str | None) -> dict:
    _require_uploaded(series_key)
    return dataset_workspace.get_confidence(db, series_key, model_id)


def run_sensitivity(db: Session, series_key: str, demand_multiplier_pct: float, promotion: bool | None = None, event: bool | None = None) -> dict:
    """Scale this series' driver columns and re-forecast.

    promotion/event are ignored: they were canonical boolean flags, and the
    route still accepts them so an older frontend build does not 422. An
    uploaded file's equivalent is simply one of its own driver columns.
    """
    _require_uploaded(series_key)
    return dataset_workspace.run_sensitivity(db, series_key, demand_multiplier_pct)


def get_drift(db: Session, series_key: str, model_id: str | None) -> dict:
    _require_uploaded(series_key)
    return dataset_workspace.get_drift(db, series_key, model_id)


def get_staffing_impact(db: Session, series_key: str, days: int = 14) -> dict:
    _require_uploaded(series_key)
    return dataset_workspace.get_staffing_impact(db, series_key, days)
