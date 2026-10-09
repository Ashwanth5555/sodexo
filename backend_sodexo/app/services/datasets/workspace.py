"""Workspace-tab data for uploaded datasets.

This is where every Labor Forecasting tab now gets its numbers: the client's
stored CSV plus the column mapping they confirmed. forecasting/workspace.py
is a thin pass-through in front of it.

It began as the uploaded-data half of a pair, mirroring response shapes from
a canonical implementation so the same React components could render either.
The canonical half is gone, but the shapes are kept - the frontend is built
against them, and they are reasonable shapes.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, time

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelPrediction, ModelRun, TrainingJob, UploadedDataset
from app.services.datasets import series as series_builder
from app.services.datasets import storage
from app.services.forecasting import results
from app.services.forecasting.validation_plan import (
    evaluation_mode,
    fold_validation_size,
    resolve_seasonal_period,
    rolling_folds,
)

_MONTH_SEASON = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
                 6: "Summer", 7: "Summer", 8: "Summer", 9: "Fall", 10: "Fall", 11: "Fall"}

# The models that are actually handed the exogenous matrix. Only these can
# respond to a what-if on the driver columns; the rest would return the
# baseline unchanged and make the scenario look inert.
_EXOG_AWARE_MODEL_IDS = ("xgboost_exog", "sarimax_exog", "auto_arima_exog", "var_exog")


def is_uploaded_series(series_key: str) -> bool:
    return series_key.startswith("DS-") and "__" in series_key


def resolve(db: Session, series_key: str) -> tuple[UploadedDataset, series_builder.UploadedSeries]:
    dataset_id = series_key.split("__", 1)[0]
    dataset = db.get(UploadedDataset, dataset_id)
    if dataset is None:
        raise LookupError(f"The uploaded dataset for series '{series_key}' no longer exists.")
    for spec in series_builder.list_series(dataset):
        if spec.key == series_key:
            return dataset, spec
    raise LookupError(f"Series '{series_key}' is no longer produced by its dataset - re-check the column mapping.")


def _history(db: Session, series_key: str) -> tuple[UploadedDataset, series_builder.UploadedSeries, pd.DataFrame]:
    dataset, spec = resolve(db, series_key)
    return dataset, spec, series_builder.build_history(dataset, spec)


def _grain(dataset: UploadedDataset) -> dict:
    return series_builder.grain_for(dataset.frequency or "D")


def get_eda(db: Session, series_key: str) -> dict:
    dataset, spec, history = _history(db, series_key)

    def _mean_by(column: str) -> dict:
        grouped = history.groupby(column)["target"].mean()
        return {str(k): round(float(v), 2) for k, v in grouped.items()}

    # No holiday calendar for an arbitrary file, so season comes from the month
    # of the timestamp and there is no holiday lift to report.
    seasons = history["date"].dt.month.map(_MONTH_SEASON)
    season_effect = {
        str(name): round(float(history.loc[seasons == name, "target"].mean()), 2)
        for name in ("Spring", "Summer", "Fall", "Winter")
        if (seasons == name).any()
    }

    # Any driver the user picked that is effectively binary gets the same
    # on/off lift treatment the canonical flags get.
    flag_effects = {}
    for column in spec.exog_columns:
        if column not in history.columns:
            continue
        values = history[column].dropna()
        if values.nunique() != 2:
            continue
        low, high = sorted(values.unique())
        off = round(float(history.loc[history[column] == low, "target"].mean()), 2)
        on = round(float(history.loc[history[column] == high, "target"].mean()), 2)
        flag_effects[column] = {"on": on, "off": off, "lift_pct": round((on / off - 1) * 100, 1) if off else None}

    daily = history.groupby(history["date"].dt.date)["target"].mean().sort_index()
    daily_recent = daily.tail(90)
    rolling_7d = daily_recent.rolling(7, min_periods=1).mean()
    daily_trend = [
        {"date": str(d), "actual": round(float(v), 2), "trend_7d": round(float(r), 2)}
        for d, v, r in zip(daily_recent.index, daily_recent.values, rolling_7d.values)
    ]

    return {
        "series_key": series_key,
        "series_label": spec.label,
        "daily_trend": daily_trend,
        "by_hour_of_day": _mean_by("hour_of_day"),
        "by_day_of_week": _mean_by("dayofweek"),
        "season_effect": season_effect,
        "holiday_lift_pct": None,
        "flag_effects": flag_effects,
    }


def get_preprocessing(db: Session, series_key: str) -> dict:
    dataset, spec, history = _history(db, series_key)
    grain = _grain(dataset)
    periods_per_day = grain["per_day"]
    seasonal_period = resolve_seasonal_period(history["target"], grain["candidates"], grain["cycle"])
    validation_size = fold_validation_size(len(history), grain["cycle"])
    folds = rolling_folds(len(history), validation_size)
    mode = evaluation_mode(len(history), validation_size)

    exog_columns = [c for c in spec.exog_columns if c in history.columns]
    preview_columns = ["date"] + exog_columns + ["target"]
    preview_df = history[preview_columns].head(10).copy()
    preview_df["date"] = preview_df["date"].dt.strftime("%Y-%m-%d %H:%M")
    preview_df = preview_df.round(2).rename(columns={"target": dataset.target_column or "target"})

    horizon = dataset.horizon_periods or grain["default_horizon"]
    return {
        "series_key": series_key,
        "series_label": spec.label,
        "total_history_days": round(len(history) / periods_per_day),
        "training_window_days": round(len(history) / periods_per_day),
        "training_window_rows": len(history),
        "resolved_seasonal_period_slots": seasonal_period,
        "seasonal_period_label": _seasonal_label(seasonal_period, dataset.frequency or "D"),
        "validation_plan": {
            "mode": mode,
            "fold_validation_size_days": round(validation_size / periods_per_day),
            "fold_count": len(folds) if folds else 1,
            "note": "Rolling-origin: 3 expanding-window folds over the tail of the series."
            if mode == "rolling_origin"
            else "Not enough history for 3 folds - a single chronological holdout is used instead.",
        },
        "input_dataset": {"rows": len(history), "columns": len(preview_columns)},
        "preview_columns": list(preview_df.columns),
        "preview_rows": json.loads(preview_df.to_json(orient="records")),
        "forecast_horizon_days": round(horizon / periods_per_day),
        "block_hours": 24 // periods_per_day if periods_per_day else 24,
        # The name of one row's period, so the UI never has to guess. It used
        # to say "half-day block" whatever the file contained, which is the
        # grain the canonical data happened to use and no upload ever does.
        "period_label": grain["label"],
    }


def _seasonal_label(period: int | None, frequency: str) -> str:
    if not period:
        return "None resolved"
    labels = {
        "H": {24: "Daily", 168: "Weekly"},
        "D": {7: "Weekly"},
        "W": {52: "Yearly"},
        "MS": {12: "Yearly"},
    }
    return labels.get(frequency, {}).get(period, f"{period} periods")


def _get_model_run(db: Session, series_key: str, model_id: str | None) -> ModelRun | None:
    run = results.get_run(db, series_key, model_id)
    return run if run is not None and run.status == "completed" else None


def get_drift(db: Session, series_key: str, model_id: str | None) -> dict:
    dataset, spec, history = _history(db, series_key)
    run = _get_model_run(db, series_key, model_id)
    if not run:
        raise LookupError(f"No completed model found for series '{series_key}' - train the dataset first.")

    grain = _grain(dataset)
    # Compare the last cycle against the four before it, in the series' own
    # periods, so this works the same at hourly or monthly frequency.
    cycle = max(1, grain["cycle"])
    recent = history.tail(cycle)
    prior = history.iloc[max(0, len(history) - cycle * 5) : max(0, len(history) - cycle)]

    signal_column = spec.exog_columns[0] if spec.exog_columns and spec.exog_columns[0] in history.columns else "target"
    recent_mean = float(recent[signal_column].mean()) if len(recent) else None
    prior_mean = float(prior[signal_column].mean()) if len(prior) else None
    pct_change = (
        round((recent_mean - prior_mean) / prior_mean * 100, 1)
        if recent_mean is not None and prior_mean not in (None, 0)
        else None
    )

    if pct_change is None:
        status = "Unknown"
    elif abs(pct_change) < 15:
        status = "Stable"
    elif abs(pct_change) < 30:
        status = "Watch"
    else:
        status = "Drifting"

    daily = history.groupby(history["date"].dt.date)["target"].mean().sort_index().tail(60)
    demand_signal_trend = [{"date": str(d), "value": round(float(v), 2)} for d, v in daily.items()]

    correlation = None
    if signal_column != "target" and history[signal_column].nunique() > 1:
        correlation = round(float(history["target"].corr(history[signal_column])), 3)
    strength = (
        "Strong" if correlation is not None and abs(correlation) >= 0.6
        else "Moderate" if correlation is not None and abs(correlation) >= 0.3
        else "Weak" if correlation is not None
        else "Not measured"
    )

    recent_by_hour = recent.groupby("hour_of_day")["target"].mean().round(2)
    prior_by_hour = prior.groupby("hour_of_day")["target"].mean().round(2)
    seasonality_drift = [
        {
            "hour_of_day": hour,
            "recent": float(recent_by_hour.get(hour)) if hour in recent_by_hour.index else None,
            "prior": float(prior_by_hour.get(hour)) if hour in prior_by_hour.index else None,
        }
        for hour in range(24)
    ]

    return {
        "series_key": series_key,
        "model_id": run.model_id,
        "model_label": run.model_label,
        "model_age_days": (datetime.utcnow() - run.trained_at).days if run.trained_at else None,
        "historical_backtest_mape": run.mape,
        "historical_backtest_bias": run.bias,
        "feature_distribution": {
            "signal": signal_column,
            "recent_7d_mean": round(recent_mean, 2) if recent_mean is not None else None,
            "prior_30d_mean": round(prior_mean, 2) if prior_mean is not None else None,
            "pct_change": pct_change,
        },
        "demand_signal_name": signal_column,
        "demand_signal_trend": demand_signal_trend,
        "demand_signal_correlation": correlation,
        "demand_signal_strength": strength,
        "seasonality_drift": seasonality_drift,
        "status": status,
    }


# --- Demand Drivers & Feature Mapping ---------------------------------------
#
# The canonical version reads a fixed column list off the SeriesConfig. An
# upload has no such list, so the equivalent is "every numeric column in the
# file that is not already playing another role" - the date axis, the target
# and the group key are structural, not drivers, and offering them as inputs
# would let a user feed the target to itself.

def _numeric_profile_columns(dataset: UploadedDataset) -> list[str]:
    reserved = {dataset.date_column, dataset.target_column, dataset.capacity_column, *(dataset.group_columns or [])}
    return [
        entry["name"]
        for entry in (dataset.column_profile or [])
        if entry.get("is_numeric") and not entry.get("is_date_like") and entry.get("name") not in reserved
    ]


def _driver_payload(dataset: UploadedDataset, spec: series_builder.UploadedSeries, strongest: str | None = None) -> dict:
    grain = _grain(dataset)
    available = _numeric_profile_columns(dataset)
    enabled = [c for c in (dataset.exog_columns or []) if c in available]
    return {
        "series_key": spec.key,
        "series_label": spec.label,
        "mapping": {
            "target_column": dataset.target_column,
            "date_time_grain": f"{dataset.date_column} ({grain['label'].lower()})",
            "dimensions": list(dataset.group_columns or []) or ["none - the file is one series"],
            "frequency": grain["label"],
        },
        "exogenous_drivers_used": enabled,
        "available_input_columns": [
            {"column": column, "raw": column, "enabled": column in enabled} for column in available
        ],
        "primary_demand_signal": strongest or (enabled[0] if enabled else None),
        # Column choices live on the dataset, not the series, so a file split
        # into 40 groups shares one mapping. Saying so up front avoids the
        # surprise of toggling a column on one group and seeing every other
        # group change with it.
        "scope_note": (
            f"These columns are mapped once for the whole uploaded file"
            + (f", so the change applies to all {len(series_builder.list_series(dataset))} series split out of it."
               if dataset.group_columns else ".")
        ),
        "source": "uploaded",
    }


def _strongest_driver(dataset: UploadedDataset, specs: list) -> str | None:
    """The mapped driver that actually moves most with the target.

    Shown as the primary demand signal. It used to be whichever driver the
    user listed first, which is not a claim about the data at all - and it
    disagreed with the column the VAR models actually pair with, which is
    chosen by correlation. Computed once per dataset from the first series
    rather than per series, so the tab stays cheap.
    """
    if not specs:
        return None
    try:
        history = series_builder.build_history(dataset, specs[0])
    except Exception:
        return None
    target = history["target"].astype(float)
    best, best_score = None, -1.0
    for column in (dataset.exog_columns or []):
        if column not in history.columns:
            continue
        series = history[column].astype(float)
        if series.nunique(dropna=True) <= 1:
            continue
        correlation = series.corr(target)
        score = 0.0 if pd.isna(correlation) else abs(float(correlation))
        if score > best_score:
            best, best_score = column, score
    return best


def get_drivers_for_dataset(dataset: UploadedDataset) -> list[dict]:
    """One Demand Drivers entry per series this dataset yields."""
    try:
        specs = series_builder.list_series(dataset)
    except Exception:
        # A dataset whose file went missing must not break the whole tab.
        return []
    strongest = _strongest_driver(dataset, specs)
    return [_driver_payload(dataset, spec, strongest) for spec in specs]


def set_input_column_enabled(db: Session, series_key: str, column: str, enabled: bool) -> None:
    """Add or remove a driver column for the dataset behind this series."""
    dataset, _spec = resolve(db, series_key)
    available = _numeric_profile_columns(dataset)
    if column not in available:
        raise LookupError(
            f"'{column}' is not a numeric input column of this uploaded file. "
            f"Available: {', '.join(available) if available else 'none'}."
        )

    current = [c for c in (dataset.exog_columns or []) if c in available]
    if enabled and column not in current:
        current.append(column)
    elif not enabled and column in current:
        current.remove(column)
    # Reassign rather than mutate: exog_columns is a JSON column, and
    # SQLAlchemy does not track in-place list mutation without MutableList.
    dataset.exog_columns = current
    db.commit()


# --- What-if sensitivity ----------------------------------------------------
#
# The canonical version moves demand_multiplier and flips promotion/event,
# because those columns are guaranteed by the Sodexo schema. An uploaded file
# has neither, but it does have whatever numeric drivers the client mapped -
# so the honest equivalent of "what if demand moves 10%" is "scale every
# driver this series actually uses by 10% and re-forecast".

def run_sensitivity(db: Session, series_key: str, driver_pct: float) -> dict:
    from app.services.forecasting.models import FITTERS

    dataset, spec, history = _history(db, series_key)
    grain = _grain(dataset)
    seasonal_period = resolve_seasonal_period(history["target"], grain["candidates"], grain["cycle"])
    ctx = series_builder.context_for(dataset, history, seasonal_period)

    # Only a model that was actually given the drivers can respond to them
    # being moved. Re-forecasting a driver-blind model under a scenario would
    # return the baseline unchanged and look like the scenario had no effect.
    scenario_columns = [c for c in ctx.exog_columns if c in history.columns and c != "is_weekend"]
    if not scenario_columns:
        raise LookupError(
            "This dataset has no driver columns mapped, so there is nothing to vary. "
            "Pick one or more numeric drivers in Demand Drivers & Feature Mapping first."
        )

    # Pick the strongest exog-aware model rather than the champion: the
    # champion may well be a driver-blind model (on a stable series it often
    # is), and using it would make every scenario return the baseline.
    candidates = db.execute(
        select(ModelRun).where(
            ModelRun.series_key == series_key,
            ModelRun.status == "completed",
            ModelRun.model_id.in_(_EXOG_AWARE_MODEL_IDS),
        )
    ).scalars().all()
    candidates = [r for r in candidates if r.model_id in FITTERS]
    if not candidates:
        raise LookupError(
            "No driver-aware model completed for this series, so a what-if scenario cannot be simulated. "
            "Train the dataset with at least one numeric driver mapped."
        )
    run = sorted(candidates, key=lambda r: r.mape if r.mape is not None else float("inf"))[0]

    horizon = dataset.horizon_periods or grain["default_horizon"]
    future = series_builder.build_future(dataset, history, horizon)

    fitter = FITTERS[run.model_id]
    baseline = fitter(history, future, ctx)

    scenario_future = future.copy()
    factor = 1 + driver_pct / 100.0
    for column in scenario_columns:
        if column in scenario_future.columns:
            scenario_future[column] = scenario_future[column] * factor
    scenario = fitter(history, scenario_future, ctx)

    points = []
    for timestamp, base_v, scen_v in zip(future["date"], baseline, scenario):
        base = float(base_v) if pd.notna(base_v) else None
        scen = float(scen_v) if pd.notna(scen_v) else None
        points.append({
            "date": timestamp.isoformat(),
            "baseline_forecast": round(base, 2) if base is not None else None,
            "scenario_forecast": round(scen, 2) if scen is not None else None,
            "baseline_headcount": int(math.ceil(base)) if base is not None else None,
            "scenario_headcount": int(math.ceil(scen)) if scen is not None else None,
            "difference": round(scen - base, 2) if base is not None and scen is not None else None,
        })

    return {
        "series_key": series_key,
        "model_id": run.model_id,
        "model_label": run.model_label,
        "scenario": {
            "demand_multiplier_pct": driver_pct,
            "promotion": None,
            "event": None,
            "drivers_scaled": scenario_columns,
        },
        # Both curves come from one live refit rather than comparing against
        # the stored forecast: the stored one may predate a driver-column
        # change, and a baseline that does not match the scenario's own model
        # makes the difference column meaningless.
        "baseline_note": "Baseline and scenario are both re-forecast now, from the same model.",
        "points": points,
    }


# --- Staffing Impact --------------------------------------------------------
#
# What the periods ahead need, and what that means for the roster.
#
# Every figure here is derived from the client's own file and their own
# trained champion. The requirement is the forecast; the baseline is what the
# same slot has actually been running lately; the cover buffer is the
# champion's measured error on periods it never saw during training.
#
# Where the file also records what was PLANNED - a roster, a budget, a
# scheduled headcount - that plan is set against the requirement and the gap
# is reported per period. Where it does not, this page still has a job to do,
# and used to refuse it: it can say how many are needed, how that compares to
# the recent norm, and what to roster so the model's own error is absorbed.
# Showing nothing at all because one optional column is missing threw away
# the forecast the client had just spent minutes training.

_OVERSTAFFED_TOLERANCE = 2.0

# How far back a "recent norm" looks. Long enough that a weekday has several
# observations behind it, short enough that last spring is not offered as
# evidence about next week.
_BASELINE_ROWS = 180

_PERIOD_UNIT = {"Daily": "day", "Hourly": "hour", "Weekly": "week", "Monthly": "month"}


def _baseline_lookup(history: pd.DataFrame, frequency: str):
    """What this slot has actually been running lately.

    Not a forecast and not a plan - a recent norm, so that a requirement of 60
    reads as "eight per cent above what Thursdays normally take here" rather
    than as a bare number nobody can size. Keyed on the repeating part of the
    calendar the grain actually has: hour-of-week when the file is hourly,
    weekday when it is daily, month when it is monthly.

    A slot with fewer than two observations behind it falls back to the
    overall recent mean - one previous Thursday is an anecdote.
    """
    freq = (frequency or "D").upper()
    if freq.startswith("H"):
        key_of = lambda ts: ts.weekday() * 24 + ts.hour  # noqa: E731
        keys_of = lambda d: d.dt.dayofweek * 24 + d.dt.hour  # noqa: E731
        basis = "the same hour on the same weekday"
    elif freq.startswith("W"):
        key_of = lambda ts: 0  # noqa: E731
        keys_of = lambda d: d.dt.dayofweek * 0  # noqa: E731
        basis = "recent weeks"
    elif freq.startswith("M"):
        key_of = lambda ts: ts.month  # noqa: E731
        keys_of = lambda d: d.dt.month  # noqa: E731
        basis = "the same month in earlier years"
    else:
        key_of = lambda ts: ts.weekday()  # noqa: E731
        keys_of = lambda d: d.dt.dayofweek  # noqa: E731
        basis = "the same weekday"

    recent = history.tail(_BASELINE_ROWS)
    dates = pd.to_datetime(recent["date"])
    frame = pd.DataFrame({
        "key": keys_of(dates).to_numpy(),
        "value": pd.to_numeric(recent["target"], errors="coerce").to_numpy(),
    }).dropna()
    if frame.empty:
        return (lambda _ts: None), basis, 0

    grouped = frame.groupby("key")["value"]
    counts = grouped.count()
    index = {int(k): float(v) for k, v in grouped.mean().items() if int(counts[k]) >= 2}
    overall = float(frame["value"].mean())

    def lookup(ts) -> float | None:
        value = index.get(int(key_of(ts)), overall)
        return None if value is None or not math.isfinite(value) else round(float(value), 2)

    return lookup, basis, int(len(frame))


def _cover_buffer(db: Session, series_key: str) -> dict:
    """How much to roster above the forecast so its own error is absorbed.

    The champion's average error on data held back from training, which is the
    only number in the system that has been measured against reality rather
    than asserted. Rostering exactly to a forecast means running short every
    time the forecast lands low, which is about half of them.
    """
    run = results.get_run(db, series_key)
    if run is None:
        return {"value": None, "model": None, "basis": None, "accuracy": None}
    measured = run.mae if run.mae is not None else run.rmse
    if measured is None or not math.isfinite(float(measured)):
        return {"value": None, "model": run.model_label, "basis": None, "accuracy": run.accuracy}
    value = round(float(measured), 2)
    return {
        "value": value,
        "model": run.model_label,
        "accuracy": None if run.accuracy is None else round(float(run.accuracy), 1),
        "basis": (
            f"{run.model_label} was out by {value} on average on periods held back from training, "
            f"so a roster built exactly to the forecast runs short about half the time."
        ),
    }


def _no_impact(series_key: str, spec, note: str) -> dict:
    return {
        "series_key": series_key,
        "series_label": spec.label,
        "points": [],
        "note": note,
        "needs_capacity_column": False,
        "mode": "empty",
    }


def get_staffing_impact(db: Session, series_key: str, days: int = 14) -> dict:
    dataset, spec = resolve(db, series_key)
    grain = _grain(dataset)
    unit = _PERIOD_UNIT.get(grain["label"], "period")

    # Checked rather than trusted: a dataset mapped before this rule existed
    # can still be carrying `dayofweek` as its roster, and a stored value is
    # not fixed by shipping new code.
    has_plan = series_builder.is_plausible_capacity(dataset.capacity_column)

    try:
        history = series_builder.build_history(
            dataset, spec, extra_columns=(dataset.capacity_column,) if has_plan else ()
        )
    except ValueError as exc:
        return _no_impact(series_key, spec, str(exc))

    if has_plan and dataset.capacity_column in history.columns:
        # itertuples mangles any column name that is not a valid identifier -
        # "Rostered Hours" becomes _3 - AND any name starting with an
        # underscore, so the stand-in name must be a plain identifier.
        history = history.rename(columns={dataset.capacity_column: "planned_capacity"})
    else:
        has_plan = False
        history = history.assign(planned_capacity=float("nan"))

    history = history[pd.to_numeric(history["target"], errors="coerce").notna()]
    if history.empty:
        return _no_impact(
            series_key, spec,
            f"No period in this series has a value for '{dataset.target_column}' yet.",
        )

    periods = max(1, days * grain["per_day"])
    window = history.tail(periods)
    baseline_for, baseline_basis, baseline_rows = _baseline_lookup(history, dataset.frequency or "D")
    buffer = _cover_buffer(db, series_key)

    def _point(date, required, planned, is_forecast) -> dict:
        required = round(float(required), 2)
        base = baseline_for(date)
        planned_value = None
        if planned is not None and not (isinstance(planned, float) and math.isnan(planned)):
            planned_value = round(float(planned), 2)
        entry = {
            "date": date.isoformat(),
            "required_headcount": required,
            "baseline": base,
            "vs_baseline_pct": None if not base else round((required - base) / base * 100, 1),
            # Only for periods still ahead: telling someone what they should
            # have rostered last Tuesday is hindsight, not advice.
            "recommended_headcount": (
                round(required + buffer["value"], 2)
                if is_forecast and buffer["value"] is not None
                else None
            ),
            "scheduled_headcount": planned_value,
            "gap": None,
            "status": None,
            "is_forecast": is_forecast,
        }
        if planned_value is not None:
            gap = round(planned_value - required, 2)
            entry["gap"] = gap
            # A small surplus counts as balanced rather than overstaffed:
            # planning exactly to forecast leaves no slack for one absence.
            entry["status"] = (
                "Understaffed" if gap < 0
                else "Overstaffed" if gap > _OVERSTAFFED_TOLERANCE
                else "Balanced"
            )
        return entry

    history_points = [
        _point(row.date, getattr(row, "target"), row.planned_capacity, False)
        for row in window.itertuples(index=False)
    ]

    # The periods that have not happened yet. This is the half of the page
    # worth acting on: a shortfall in the past can only be reviewed, one in
    # the week ahead can still be rostered around. The requirement comes from
    # the trained champion; the plan comes from the file, and only from the
    # file - carrying the last roster forward would invent coverage nobody
    # scheduled, which is the mistake this tab exists to catch.
    planned_ahead = _planned_by_date(dataset, spec) if has_plan else {}
    forecast_points = [
        _point(date, predicted, planned_ahead.get(date.date()), True)
        for date, predicted in _future_requirement(db, series_key)
    ]
    points = history_points + forecast_points

    covered = [p for p in forecast_points if p["scheduled_headcount"] is not None]
    short = [p for p in covered if p["status"] == "Understaffed"]
    surplus = [p for p in covered if p["status"] == "Overstaffed"]
    baselined = [p for p in forecast_points if p["baseline"] is not None]

    total_required = round(sum(p["required_headcount"] for p in forecast_points), 2) if forecast_points else None
    total_baseline = round(sum(p["baseline"] for p in baselined), 2) if baselined else None
    comparable = round(sum(p["required_headcount"] for p in baselined), 2) if baselined else None
    peak = max(forecast_points, key=lambda p: p["required_headcount"]) if forecast_points else None
    quiet = min(forecast_points, key=lambda p: p["required_headcount"]) if forecast_points else None
    worst = min(short, key=lambda p: p["gap"]) if short else None

    outlook = {
        "periods": len(forecast_points),
        "start": forecast_points[0]["date"] if forecast_points else None,
        "end": forecast_points[-1]["date"] if forecast_points else None,
        "total_required": total_required,
        "total_baseline": total_baseline,
        # Measured only over the periods that HAVE a baseline, so the
        # percentage compares like with like rather than setting a full
        # horizon against a partial norm.
        "change_pct": (
            round((comparable - total_baseline) / total_baseline * 100, 1)
            if total_baseline else None
        ),
        "recommended_total": (
            round(sum(p["recommended_headcount"] or p["required_headcount"] for p in forecast_points), 2)
            if forecast_points else None
        ),
        "peak": None if peak is None else {"date": peak["date"], "required": peak["required_headcount"]},
        "quiet": None if quiet is None else {"date": quiet["date"], "required": quiet["required_headcount"]},
        "planned_periods": len(covered),
        "total_planned": round(sum(p["scheduled_headcount"] for p in covered), 2) if covered else None,
        "short_periods": len(short),
        "surplus_periods": len(surplus),
        "total_shortfall": round(sum(-p["gap"] for p in short), 2) if short else 0.0,
        "largest_shortfall": None if worst is None else {"date": worst["date"], "gap": worst["gap"]},
        "baseline_basis": baseline_basis,
        "baseline_rows": baseline_rows,
        "buffer": buffer,
    }

    # One line each. The columns are named in the table and the model is named
    # on the leaderboard, so a paragraph here was restating both.
    if covered:
        mode = "plan"
        note = (
            f"Forecast requirement against '{dataset.capacity_column}', "
            f"for {len(covered)} of the next {len(forecast_points)} {unit}s."
        )
    elif forecast_points:
        mode = "forecast"
        source = buffer["model"] or "the champion model"
        note = f"Forecast by {source}, against what this series has been running on {baseline_basis}."
    else:
        mode = "history"
        note = f"The last {len(history_points)} {unit}s on record. Train this series to add the {unit}s ahead."

    return {
        "series_key": series_key,
        "series_label": spec.label,
        "window_start": window["date"].min().date().isoformat(),
        "window_end": window["date"].max().date().isoformat(),
        "required_label": dataset.target_column,
        "scheduled_label": dataset.capacity_column if has_plan else None,
        "period_label": grain["label"],
        "period_unit": unit,
        "mode": mode,
        "note": note,
        "outlook": outlook,
        "needs_capacity_column": not has_plan,
        "points": points,
    }


def _future_requirement(db: Session, series_key: str) -> list[tuple[datetime, float]]:
    """The champion's forward forecast, as (timestamp, value) pairs.

    Empty until the series has been trained - the page then shows history
    alone, which is what it always did.
    """
    from app.services.forecasting import results

    run = results.get_run(db, series_key)
    if run is None:
        return []
    rows = db.execute(
        select(ModelPrediction)
        .where(ModelPrediction.model_run_id == run.id, ModelPrediction.is_future.is_(True))
        .order_by(ModelPrediction.business_date, ModelPrediction.hour_of_day)
    ).scalars().all()
    return [
        (datetime.combine(r.business_date, time(hour=int(r.hour_of_day or 0))), float(r.predicted))
        for r in rows
        if r.predicted is not None
    ]


def _planned_by_date(dataset: UploadedDataset, spec) -> dict:
    """Planned capacity the file records for dates beyond the last actual.

    A real roster is written ahead, so an export often carries planned rows
    with no actual against them yet. Those rows are dropped from the training
    history - no target means nothing to learn from - which is exactly why
    they have to be read separately here.
    """
    if not dataset.capacity_column:
        return {}
    frame = storage.load_frame(dataset.stored_filename)
    if dataset.capacity_column not in frame.columns or dataset.date_column not in frame.columns:
        return {}
    for column, value in zip(dataset.group_columns or [], spec.group_values or ()):
        if column not in frame.columns:
            return {}
        frame = frame[frame[column].astype(str) == value]
    dates = pd.to_datetime(frame[dataset.date_column], errors="coerce")
    planned = pd.to_numeric(frame[dataset.capacity_column], errors="coerce")
    usable = dates.notna() & planned.notna()
    if not usable.any():
        return {}
    by_day = planned[usable].groupby(dates[usable].dt.date).sum(min_count=1)
    return {day: float(v) for day, v in by_day.items() if pd.notna(v)}


# --- Forecast confidence ----------------------------------------------------

def get_confidence(db: Session, series_key: str, model_id: str | None) -> dict:
    """Per-period prediction intervals and how much to trust each one.

    Ported from the canonical implementation, minus its dim_date join. That
    join downgraded confidence on public holidays, which only worked because
    the Sodexo schema shipped a holiday calendar. An uploaded file has no
    calendar, so the downgrade is dropped rather than guessed at - a client
    who cares can map a holiday flag as a driver column.
    """
    run = _get_model_run(db, series_key, model_id)
    if not run:
        raise LookupError(
            f"No completed model found for series '{series_key}'"
            + (f" and model '{model_id}'" if model_id else " (no champion yet)")
        )

    predictions = db.execute(
        select(ModelPrediction)
        .where(ModelPrediction.model_run_id == run.id, ModelPrediction.is_future.is_(True))
        .order_by(ModelPrediction.business_date, ModelPrediction.hour_of_day)
    ).scalars().all()

    points = []
    for p in predictions:
        width = (p.upper - p.lower) if (p.upper is not None and p.lower is not None) else None
        # Interval width relative to the value itself: a +/-5 band means
        # something very different on a forecast of 8 than on one of 800.
        relative = width / max(abs(p.predicted), 1.0) if width is not None else None
        if relative is None:
            level = "Unknown"
        elif relative < 0.25:
            level = "High"
        elif relative < 0.5:
            level = "Medium"
        else:
            level = "Low"
        reasons = [
            f"Validated over {run.folds} rolling-origin folds"
            if run.evaluation_mode == "rolling_origin"
            else "Limited validation history (holdout fallback, not full rolling-origin CV)"
        ]
        points.append({
            "date": f"{p.business_date.isoformat()}T{p.hour_of_day:02d}:00:00",
            "forecast": p.predicted, "lower": p.lower, "upper": p.upper,
            "confidence_level": level, "reasons": reasons,
        })

    return {
        "series_key": series_key,
        "model_id": run.model_id,
        "model_label": run.model_label,
        "points": points,
    }
