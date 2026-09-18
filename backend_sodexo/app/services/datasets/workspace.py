"""Workspace-tab data for uploaded datasets.

Returns the same response shapes as the canonical implementations in
forecasting/workspace.py so the existing tab components render an uploaded
series without any change - only the source of the history differs. Where a tab
is inherently tied to the Sodexo schema (staffing standards, holiday calendar,
promotion/event flags) the canonical module raises LookupError instead, which
the routes already turn into a clean 404 message rather than a 500.
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelRun, TrainingJob, UploadedDataset
from app.services.datasets import series as series_builder
from app.services.forecasting import results
from app.services.forecasting.validation_plan import (
    evaluation_mode,
    fold_validation_size,
    resolve_seasonal_period,
    rolling_folds,
)

_MONTH_SEASON = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
                 6: "Summer", 7: "Summer", 8: "Summer", 9: "Fall", 10: "Fall", 11: "Fall"}


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
