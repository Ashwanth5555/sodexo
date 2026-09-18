"""Turns an uploaded dataset into forecastable series.

The forecasting engine is DataFrame-driven: give it a frame with `date`,
`hour_of_day`, `dayofweek`, `month`, `target`, `var_pair` and any exogenous
columns, plus a matching future frame, and the full 13-model portfolio runs
unchanged. This module builds exactly that shape from a user's file and the
column mapping they chose, so an arbitrary CSV is forecast by the same engine,
scored by the same metrics and ranked by the same champion rule as the
built-in series.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.models import UploadedDataset
from app.services.datasets import storage
from app.services.forecasting.models import ModelContext

# Per frequency: the seasonal-cycle candidates to test, the dominant cycle used
# to size CV folds, how many periods make a day, and XGBoost's lag/rolling
# windows - all expressed in periods of that frequency.
FREQUENCY_GRAIN: dict[str, dict] = {
    "H": {
        "candidates": (24, 168), "cycle": 168, "per_day": 24,
        "xgb_lags": (1, 24, 168), "xgb_rollings": (24, 168),
        "default_horizon": 48, "label": "Hourly",
    },
    "D": {
        "candidates": (7,), "cycle": 7, "per_day": 1,
        "xgb_lags": (1, 7, 14, 28), "xgb_rollings": (7, 28),
        "default_horizon": 14, "label": "Daily",
    },
    "W": {
        "candidates": (52,), "cycle": 13, "per_day": 1,
        "xgb_lags": (1, 4, 13, 52), "xgb_rollings": (4, 13),
        "default_horizon": 8, "label": "Weekly",
    },
    "MS": {
        "candidates": (12,), "cycle": 12, "per_day": 1,
        "xgb_lags": (1, 3, 6, 12), "xgb_rollings": (3, 12),
        "default_horizon": 6, "label": "Monthly",
    },
}

MIN_ROWS_PER_SERIES = 20


@dataclass(frozen=True)
class UploadedSeries:
    """One forecastable series from an uploaded dataset - the whole file, or
    one value of the group column when the user chose to split by one."""

    key: str
    label: str
    dataset_id: str
    group_value: str | None = None
    exog_columns: tuple[str, ...] = field(default=())


def grain_for(frequency: str) -> dict:
    return FREQUENCY_GRAIN.get(frequency, FREQUENCY_GRAIN["D"])


def default_horizon(frequency: str) -> int:
    return grain_for(frequency)["default_horizon"]


def series_key_for(dataset_id: str, group_value: str | None) -> str:
    """Stable, <=60 char key - model_run.series_key is a String(60)."""
    suffix = storage.safe_key_fragment(group_value) if group_value is not None else "ALL"
    return f"{dataset_id}__{suffix}"


def list_series(dataset: UploadedDataset) -> list[UploadedSeries]:
    """The series this dataset yields, without loading the whole file if we can
    avoid it. Returns [] until the user has mapped date + target columns."""
    if not dataset.date_column or not dataset.target_column:
        return []

    exog = tuple(dataset.exog_columns or ())
    if not dataset.group_column:
        return [UploadedSeries(series_key_for(dataset.dataset_id, None), dataset.name, dataset.dataset_id, None, exog)]

    frame = storage.load_frame(dataset.stored_filename)
    if dataset.group_column not in frame.columns:
        return []
    values = [v for v in frame[dataset.group_column].dropna().unique().tolist()]
    values.sort(key=str)
    return [
        UploadedSeries(
            key=series_key_for(dataset.dataset_id, str(value)),
            label=f"{dataset.name} / {value}",
            dataset_id=dataset.dataset_id,
            group_value=str(value),
            exog_columns=exog,
        )
        for value in values[: storage.MAX_GROUP_SERIES]
    ]


def _calendar_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Calendar columns every fitter expects, derived from the timestamp alone.

    Unlike the canonical series there is no dim_date to join - but these are all
    computable for future dates too, which is what makes them usable as
    exogenous inputs without leaking anything.
    """
    frame = frame.copy()
    frame["hour_of_day"] = frame["date"].dt.hour.astype(int)
    frame["dayofweek"] = frame["date"].dt.dayofweek.astype(int)
    frame["month"] = frame["date"].dt.month.astype(int)
    frame["is_weekend"] = (frame["date"].dt.dayofweek >= 5).astype(float)
    return frame


def build_history(dataset: UploadedDataset, series: UploadedSeries) -> pd.DataFrame:
    """Load, filter, aggregate and regularise one series' history.

    Raises ValueError with a user-facing message when the mapping cannot
    produce a usable series - that surfaces as the model run's failure reason
    rather than an opaque traceback.
    """
    frame = storage.load_frame(dataset.stored_filename)

    for column, role in ((dataset.date_column, "date"), (dataset.target_column, "target")):
        if column not in frame.columns:
            raise ValueError(f"The {role} column '{column}' is no longer present in the uploaded file.")

    if series.group_value is not None:
        if dataset.group_column not in frame.columns:
            raise ValueError(f"The group column '{dataset.group_column}' is no longer present in the uploaded file.")
        frame = frame[frame[dataset.group_column].astype(str) == series.group_value]

    frame = frame[[c for c in {dataset.date_column, dataset.target_column, *series.exog_columns} if c in frame.columns]].copy()
    frame["date"] = pd.to_datetime(frame[dataset.date_column], errors="coerce")
    frame["target"] = pd.to_numeric(frame[dataset.target_column], errors="coerce")
    frame = frame.dropna(subset=["date", "target"])
    if frame.empty:
        raise ValueError(
            f"No rows have both a readable date in '{dataset.date_column}' and a number in '{dataset.target_column}'."
        )

    numeric_exog = []
    for column in series.exog_columns:
        if column in frame.columns and column not in (dataset.date_column, dataset.target_column):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            numeric_exog.append(column)

    # Collapse duplicate timestamps, then put the series on a regular grid so
    # the seasonal models see evenly spaced periods. Gaps are interpolated
    # rather than zero-filled: a missing reading is unknown, not zero demand.
    aggregation = dataset.aggregation if dataset.aggregation in ("sum", "mean") else "sum"
    value_columns = ["target", *numeric_exog]
    grouped = frame.set_index("date")[value_columns].resample(dataset.frequency or "D").agg(aggregation)
    grouped["target"] = grouped["target"].interpolate(limit_direction="both")
    for column in numeric_exog:
        grouped[column] = grouped[column].interpolate(limit_direction="both").fillna(0.0)

    history = grouped.reset_index().rename(columns={"index": "date"})
    history = history.dropna(subset=["target"])
    if len(history) < MIN_ROWS_PER_SERIES:
        raise ValueError(
            f"Only {len(history)} periods of history at {grain_for(dataset.frequency or 'D')['label'].lower()} "
            f"frequency; at least {MIN_ROWS_PER_SERIES} are needed to train and validate a model."
        )

    history = _calendar_features(history)
    history["target"] = history["target"].astype(float)
    # VAR needs a second, co-evolving series. The first exogenous driver is the
    # natural choice; with none, it stays constant and VAR is reported
    # not_applicable with that reason rather than fitting something meaningless.
    history["var_pair"] = history[numeric_exog[0]].astype(float) if numeric_exog else 0.0
    return history


def build_future(dataset: UploadedDataset, history: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Future calendar rows to forecast over, beyond the last known actual."""
    frequency = dataset.frequency or "D"
    last = history["date"].iloc[-1]
    future_dates = pd.date_range(start=last, periods=horizon + 1, freq=frequency)[1:]
    future = pd.DataFrame({"date": future_dates})
    future = _calendar_features(future)
    future["target"] = float("nan")
    # Exogenous drivers are unknown ahead - hold them at the last observed
    # value. Honest and leak-free: it uses only what was already known.
    for column in history.columns:
        if column not in future.columns and column not in ("target",):
            future[column] = history[column].iloc[-1]
    return future


def context_for(dataset: UploadedDataset, history: pd.DataFrame, seasonal_period: int | None) -> ModelContext:
    grain = grain_for(dataset.frequency or "D")
    exog = tuple(c for c in (dataset.exog_columns or ()) if c in history.columns)
    # is_weekend is derived, always known ahead, and cheap signal - include it.
    exog = exog + ("is_weekend",) if "is_weekend" in history.columns else exog
    # VAR models two series jointly. With no driver column chosen there is no
    # honest second series, so say that rather than reporting a bare
    # "one series is constant", which reads as arbitrary.
    var_pair_reason = None
    if not [c for c in (dataset.exog_columns or ()) if c in history.columns]:
        var_pair_reason = (
            "VAR forecasts two co-evolving series together, and no driver column was chosen for this "
            "dataset - so there is no second series to pair with the target. Pick a numeric driver in "
            "Data Quality -> Upload Data to enable VAR."
        )

    return ModelContext(
        seasonal_period=seasonal_period,
        exog_columns=exog,
        periods_per_day=grain["per_day"],
        periods_per_week=grain["cycle"],
        xgb_lags=grain["xgb_lags"],
        xgb_rollings=grain["xgb_rollings"],
        max_seasonal_period=max(grain["candidates"]),
        var_pair_reason=var_pair_reason,
    )
