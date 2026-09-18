"""Business logic behind the Labor Forecasting workspace's supporting tabs
(Demand Drivers, Data Analysis, Confidence, Sensitivity, Drift, Staffing
Impact). The core train/forecast loop lives in engine.py; this module reads
already-persisted results and the canonical DB to answer workspace
questions without retraining, except for Sensitivity which does one live
refit of the strongest available exogenous-aware model.
"""
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import ModelPrediction, ModelRun, TrainingJob
from app.services.forecasting import data as series_data
from app.services.datasets import workspace as dataset_workspace
from app.services.forecasting import results
from app.services.forecasting.contract import BLOCK_HOURS, FORECAST_HORIZON_SLOTS, LAG_SUFFIX, SERIES, SERIES_BY_KEY, SLOTS_PER_DAY, SLOTS_PER_WEEK
from app.services.forecasting.models import FITTERS, ModelContext
from app.services.forecasting.validation_plan import evaluation_mode, fold_validation_size, resolve_seasonal_period, rolling_folds

NOT_IN_DATASET_DRIVERS = [
    {"name": "Weather", "note": "No weather data exists anywhere in the mock enterprise API or synthetic dataset."},
    {"name": "Marketing campaign detail", "note": "Only a promotion_flag boolean exists on cafe sales - no campaign name/spend/channel data."},
    {"name": "Local event detail", "note": "Only an event_flag boolean exists on cafe sales - no event name/size/type data."},
    {"name": "Employee availability", "note": "/mock/workday/availability is an explicit 'not available' stub in this POC."},
]


def _canonical_series(series_key: str, feature: str):
    """Resolve a built-in series, with a clear message for uploaded ones.

    SERIES_BY_KEY[...] on an uploaded key would raise KeyError and surface as a
    500; these raise LookupError, which the routes already return as a 404 with
    the message shown in the UI.
    """
    if dataset_workspace.is_uploaded_series(series_key):
        raise LookupError(f"{feature} is only available for the built-in Sodexo series - it depends on the canonical schema, which an uploaded file does not have.")
    series = SERIES_BY_KEY.get(series_key)
    if series is None:
        raise LookupError(f"Unknown series '{series_key}'.")
    return series


def _display_name(column: str) -> str:
    return column[: -len(LAG_SUFFIX)] if column.endswith(LAG_SUFFIX) else column


def get_drivers(db: Session) -> dict:
    series_payload = []
    for series in SERIES:
        all_columns = series_data.exog_columns_for(series)
        disabled = series_data.get_disabled_columns(db, series.key)
        enabled_columns = [column for column in all_columns if column not in disabled]
        series_payload.append({
            "series_key": series.key, "series_label": series.label,
            "mapping": {
                "target_column": series.target_column,
                "date_time_grain": f"{series.date_column} + {series.hour_column} (hourly)",
                "dimensions": ["location_id", "department (fixed per series)"],
                "frequency": "Hourly",
            },
            "exogenous_drivers_used": [_display_name(column) for column in enabled_columns],
            "available_input_columns": [
                {"column": _display_name(column), "raw": column, "enabled": column not in disabled}
                for column in all_columns
            ],
            "primary_demand_signal": series.var_pair_column,
        })
    return {
        "series": series_payload,
        "not_in_dataset": NOT_IN_DATASET_DRIVERS,
        "note": (
            "occupied_beds/transaction_count etc. are the real demand signal, but the SAME-hour value is "
            "contemporaneous with the target and would leak the answer if used directly, so a 7-day-lagged "
            "version (same hour, one week ago) is used behind the scenes instead - a genuinely known past "
            "value for every step of the forecast horizon, since the horizon is also 7 days."
        ),
    }


def set_input_column_enabled(db: Session, series_key: str, column: str, enabled: bool) -> dict:
    series = _canonical_series(series_key, "Feature mapping")
    all_columns = series_data.exog_columns_for(series)
    display_to_raw = {_display_name(raw): raw for raw in all_columns}
    raw_column = column if column in all_columns else display_to_raw.get(column)
    if raw_column is None:
        raise LookupError(f"'{column}' is not a valid input column for series '{series_key}'.")
    series_data.set_column_enabled(db, series_key, raw_column, enabled)
    return get_drivers(db)


def get_preprocessing(db: Session, series_key: str) -> dict:
    """What actually happens to the raw data between Data Analysis and Model
    Training - real, computed values (window size, resolved seasonality, CV
    plan, and the actual input matrix shape/preview), not a description of
    intent."""
    if dataset_workspace.is_uploaded_series(series_key):
        return dataset_workspace.get_preprocessing(db, series_key)
    series = SERIES_BY_KEY[series_key]
    history = series_data.load_series_forecast_frame(db, series)
    train_df_full = series_data.windowed_training_frame(history)
    seasonal_period = resolve_seasonal_period(train_df_full["target"])
    validation_size = fold_validation_size(len(train_df_full))
    folds = rolling_folds(len(train_df_full), validation_size)
    mode = evaluation_mode(len(train_df_full), validation_size)
    exog_columns = series_data.enabled_exog_columns_for(db, series)

    preview_source_columns = ["date"] + list(exog_columns) + ["target"]
    preview_df = train_df_full[preview_source_columns].head(10).copy()
    preview_df["date"] = preview_df["date"].dt.strftime("%Y-%m-%d %H:%M")
    preview_df = preview_df.round(2)
    rename_map = {"target": series.target_column, **{column: _display_name(column) for column in exog_columns}}
    preview_df = preview_df.rename(columns=rename_map)

    return {
        "series_key": series_key, "series_label": series.label,
        "total_history_days": round(len(history) / SLOTS_PER_DAY),
        "training_window_days": round(len(train_df_full) / SLOTS_PER_DAY),
        "training_window_rows": len(train_df_full),
        "resolved_seasonal_period_slots": seasonal_period,
        "seasonal_period_label": {SLOTS_PER_DAY: "Daily", SLOTS_PER_WEEK: "Weekly"}.get(seasonal_period, "None resolved"),
        "validation_plan": {
            "mode": mode,
            "fold_validation_size_days": round(validation_size / SLOTS_PER_DAY),
            "fold_count": len(folds) if folds else 1,
            "note": "Rolling-origin: 3 expanding-window folds over the tail of the series." if mode == "rolling_origin" else "Not enough history for 3 folds - a single chronological holdout is used instead.",
        },
        "input_dataset": {
            "rows": len(train_df_full),
            "columns": len(preview_source_columns),
        },
        "preview_columns": list(preview_df.columns),
        "preview_rows": json.loads(preview_df.to_json(orient="records")),
        "forecast_horizon_days": round(FORECAST_HORIZON_SLOTS / SLOTS_PER_DAY),
        "block_hours": BLOCK_HOURS,
    }


def get_eda(db: Session, series_key: str) -> dict:
    if dataset_workspace.is_uploaded_series(series_key):
        return dataset_workspace.get_eda(db, series_key)
    series = SERIES_BY_KEY[series_key]
    history = series_data.load_series_history(db, series)

    def _mean_by(column: str) -> dict:
        grouped = history.groupby(column)["target"].mean()
        return {str(k): round(float(v), 2) for k, v in grouped.items()}

    holiday = history.groupby("is_holiday")["target"].mean()
    holiday_effect = {("holiday" if bool(k) else "non_holiday"): round(float(v), 2) for k, v in holiday.items()}
    holiday_lift_pct = None
    if "holiday" in holiday_effect and "non_holiday" in holiday_effect and holiday_effect["non_holiday"]:
        holiday_lift_pct = round((holiday_effect["holiday"] / holiday_effect["non_holiday"] - 1) * 100, 1)

    season_effect = {}
    for column in ("season_Spring", "season_Summer", "season_Fall", "season_Winter"):
        subset = history[history[column] == 1.0]
        if len(subset):
            season_effect[column.replace("season_", "")] = round(float(subset["target"].mean()), 2)

    flag_effects = {}
    for column in series.extra_exog_columns:
        grouped = history.groupby(column)["target"].mean()
        on_off = {("on" if bool(k) else "off"): round(float(v), 2) for k, v in grouped.items()}
        lift_pct = None
        if "on" in on_off and "off" in on_off and on_off["off"]:
            lift_pct = round((on_off["on"] / on_off["off"] - 1) * 100, 1)
        flag_effects[column] = {"on": on_off.get("on"), "off": on_off.get("off"), "lift_pct": lift_pct}

    daily = history.groupby(history["date"].dt.date)["target"].mean().sort_index()
    daily_recent = daily.tail(90)
    rolling_7d = daily_recent.rolling(7, min_periods=1).mean()
    daily_trend = [
        {"date": str(d), "actual": round(float(v), 2), "trend_7d": round(float(r), 2)}
        for d, v, r in zip(daily_recent.index, daily_recent.values, rolling_7d.values)
    ]

    return {
        "series_key": series_key, "series_label": series.label,
        "daily_trend": daily_trend,
        "by_hour_of_day": _mean_by("hour_of_day"),
        "by_day_of_week": _mean_by("dayofweek"),
        "season_effect": season_effect,
        "holiday_lift_pct": holiday_lift_pct,
        "flag_effects": flag_effects,
    }


def _latest_job(db: Session) -> TrainingJob | None:
    return db.execute(select(TrainingJob).order_by(TrainingJob.started_at.desc())).scalars().first()


def _get_model_run(db: Session, series_key: str, model_id: str | None) -> ModelRun | None:
    """Resolved per series, so a series keeps its results when a different
    series is retrained by the other training entry point."""
    return results.get_run(db, series_key, model_id)


def get_confidence(db: Session, series_key: str, model_id: str | None) -> dict:
    run = _get_model_run(db, series_key, model_id)
    if not run:
        raise LookupError(f"No completed model found for series '{series_key}'" + (f" and model '{model_id}'" if model_id else " (no champion yet)"))

    predictions = db.execute(
        select(ModelPrediction).where(ModelPrediction.model_run_id == run.id, ModelPrediction.is_future.is_(True)).order_by(ModelPrediction.business_date, ModelPrediction.hour_of_day)
    ).scalars().all()

    dates = sorted({p.business_date.isoformat() for p in predictions})
    holiday_by_date: dict[str, bool] = {}
    if dates:
        date_list_sql = ", ".join(f"'{d}'" for d in dates)
        rows = db.execute(text(f"SELECT business_date, is_holiday FROM dim_date WHERE business_date IN ({date_list_sql})")).all()
        holiday_by_date = {str(r[0])[:10]: bool(r[1]) for r in rows}

    points = []
    for p in predictions:
        width = (p.upper - p.lower) if (p.upper is not None and p.lower is not None) else None
        relative = width / max(abs(p.predicted), 1.0) if width is not None else None
        if relative is None:
            level = "Unknown"
        elif relative < 0.25:
            level = "High"
        elif relative < 0.5:
            level = "Medium"
        else:
            level = "Low"
        reasons = [f"Validated over {run.folds} rolling-origin folds" if run.evaluation_mode == "rolling_origin" else "Limited validation history (holdout fallback, not full rolling-origin CV)"]
        if holiday_by_date.get(p.business_date.isoformat()):
            reasons.append("Holiday period - demand pattern less typical")
            level = "Medium" if level == "High" else level
        points.append({
            "date": f"{p.business_date.isoformat()}T{p.hour_of_day:02d}:00:00",
            "forecast": p.predicted, "lower": p.lower, "upper": p.upper,
            "confidence_level": level, "reasons": reasons,
        })
    return {"series_key": series_key, "model_id": run.model_id, "model_label": run.model_label, "points": points}


_EXOG_MODEL_IDS = ["xgboost_exog", "sarimax_exog", "auto_arima_exog", "var_exog"]


def run_sensitivity(db: Session, series_key: str, demand_multiplier_pct: float, promotion: bool | None, event: bool | None) -> dict:
    series = _canonical_series(series_key, "What-if sensitivity")
    job = _latest_job(db)
    if not job:
        raise LookupError("No training run yet - click Train All Models first.")

    candidates = db.execute(
        select(ModelRun).where(ModelRun.job_id == job.job_id, ModelRun.series_key == series_key, ModelRun.status == "completed", ModelRun.model_id.in_(_EXOG_MODEL_IDS))
    ).scalars().all()
    if not candidates:
        raise LookupError("No exogenous-aware model completed for this series, so a what-if scenario can't be simulated.")
    run = sorted(candidates, key=lambda r: r.mape if r.mape is not None else float("inf"))[0]

    baseline_rows = db.execute(
        select(ModelPrediction).where(ModelPrediction.model_run_id == run.id, ModelPrediction.is_future.is_(True))
    ).scalars().all()
    baseline_by_key = {(r.business_date, r.hour_of_day): r.predicted for r in baseline_rows}

    history = series_data.load_series_forecast_frame(db, series)
    train_df_full = series_data.windowed_training_frame(history)
    seasonal_period = resolve_seasonal_period(train_df_full["target"])
    exog_columns = series_data.enabled_exog_columns_for(db, series)
    ctx = ModelContext(seasonal_period, exog_columns)
    last_timestamp = train_df_full["date"].iloc[-1]
    future_df = series_data.future_calendar_frame(db, series, last_timestamp, FORECAST_HORIZON_SLOTS)
    future_df = series_data.attach_future_lags(future_df, history, series)

    scenario_df = future_df.copy()
    scenario_df["demand_multiplier"] = scenario_df["demand_multiplier"] * (1 + demand_multiplier_pct / 100.0)
    if promotion is not None and "promotion_flag" in scenario_df.columns:
        scenario_df["promotion_flag"] = 1.0 if promotion else 0.0
    if event is not None and "event_flag" in scenario_df.columns:
        scenario_df["event_flag"] = 1.0 if event else 0.0

    fitter = FITTERS[run.model_id]
    scenario_predictions = fitter(train_df_full, scenario_df, ctx)

    points = []
    for timestamp, scenario in zip(future_df["date"], scenario_predictions):
        key = (timestamp.date(), int(timestamp.hour))
        base_v = baseline_by_key.get(key)
        scen_v = float(scenario) if pd.notna(scenario) else None
        points.append({
            "date": timestamp.isoformat(),
            "baseline_forecast": base_v, "scenario_forecast": scen_v,
            "baseline_headcount": int(np.ceil(base_v)) if base_v is not None else None,
            "scenario_headcount": int(np.ceil(scen_v)) if scen_v is not None else None,
            "difference": round(scen_v - base_v, 2) if base_v is not None and scen_v is not None else None,
        })
    return {
        "series_key": series_key, "model_id": run.model_id, "model_label": run.model_label,
        "scenario": {"demand_multiplier_pct": demand_multiplier_pct, "promotion": promotion, "event": event},
        "points": points,
    }


def get_drift(db: Session, series_key: str, model_id: str | None) -> dict:
    if dataset_workspace.is_uploaded_series(series_key):
        return dataset_workspace.get_drift(db, series_key, model_id)
    series = SERIES_BY_KEY[series_key]
    run = _get_model_run(db, series_key, model_id)
    if not run:
        raise LookupError(f"No completed model found for series '{series_key}'")

    history = series_data.load_series_history(db, series)
    last_date = history["date"].max()
    recent = history[history["date"] > last_date - pd.Timedelta(days=7)]
    prior = history[(history["date"] <= last_date - pd.Timedelta(days=7)) & (history["date"] > last_date - pd.Timedelta(days=37))]
    recent_mean = float(recent["var_pair"].mean()) if len(recent) else None
    prior_mean = float(prior["var_pair"].mean()) if len(prior) else None
    pct_change = round((recent_mean - prior_mean) / prior_mean * 100, 1) if recent_mean is not None and prior_mean else None

    if pct_change is None:
        status = "Unknown"
    elif abs(pct_change) < 15:
        status = "Stable"
    elif abs(pct_change) < 30:
        status = "Watch"
    else:
        status = "Drift Detected"

    # Seasonality drift: has the shape of the daily demand curve itself
    # changed, not just its average level? Compares the recent-7-day and
    # prior-30-day average target by hour-of-day so both curves can be
    # plotted on the same chart.
    # The demand signal itself (occupied_beds / transaction_count) tracked day
    # by day, recent window vs the prior one - this is the thing whose shift
    # actually causes model drift, so it belongs on a chart rather than buried
    # in a single percentage.
    signal_column = series.var_pair_column

    # How strongly this signal actually tracks staffing, measured at the grain
    # the models forecast at. Worth stating rather than asserting: cafe
    # transaction_count correlates ~0.95+, but hospital occupied_beds is much
    # weaker (~0.34) because kitchen staffing is set by meal-service windows,
    # not by how many beds are full in that particular block.
    slots = series_data.to_slots(history, series)
    correlation = slots["target"].corr(slots[signal_column]) if len(slots) > 2 else None
    correlation = None if correlation is None or pd.isna(correlation) else round(float(correlation), 2)
    if correlation is None:
        strength = "unknown"
    elif abs(correlation) >= 0.8:
        strength = "strong"
    elif abs(correlation) >= 0.5:
        strength = "moderate"
    else:
        strength = "weak"

    demand_signal_trend = []
    for window_label, frame in (("prior", prior), ("recent", recent)):
        daily = frame.groupby(frame["date"].dt.date)[signal_column].mean().round(2)
        for day, value in daily.items():
            demand_signal_trend.append({"date": str(day), "window": window_label, "value": float(value)})
    demand_signal_trend.sort(key=lambda row: row["date"])

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
        "series_key": series_key, "model_id": run.model_id, "model_label": run.model_label,
        "model_age_days": (datetime.utcnow() - run.trained_at).days if run.trained_at else None,
        "historical_backtest_mape": run.mape, "historical_backtest_bias": run.bias,
        "feature_distribution": {
            "signal": series.var_pair_column,
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


def get_staffing_impact(db: Session, series_key: str, days: int = 14) -> dict:
    series = _canonical_series(series_key, "Staffing Impact")
    max_schedule_date = db.execute(text("SELECT MAX(shift_date) FROM fact_schedule")).scalar()
    if not max_schedule_date:
        return {"series_key": series_key, "points": [], "note": "No schedule data ingested yet - run ingestion first."}

    max_date = date.fromisoformat(str(max_schedule_date)[:10])
    start_date = max_date - timedelta(days=days - 1)
    department_id = "DEP01" if series.fact_model.__tablename__ == "fact_census" else "DEP02"
    table = series.fact_model.__tablename__

    required_rows = db.execute(text(
        f"SELECT business_date, hour_of_day, required_manpower FROM {table} "
        "WHERE location_id=:loc AND business_date BETWEEN :start AND :end ORDER BY business_date, hour_of_day"
    ), {"loc": series.location_id, "start": start_date.isoformat(), "end": max_date.isoformat()}).all()

    schedule_rows = db.execute(text(
        "SELECT shift_date, scheduled_start, scheduled_end FROM fact_schedule "
        "WHERE location_id=:loc AND department_id=:dept AND shift_date BETWEEN :start AND :end"
    ), {"loc": series.location_id, "dept": department_id, "start": start_date.isoformat(), "end": max_date.isoformat()}).all()

    scheduled_by_hour: dict[tuple, int] = {}
    for _shift_date, start, end in schedule_rows:
        hour = pd.Timestamp(start).replace(minute=0, second=0, microsecond=0)
        end_ts = pd.Timestamp(end)
        while hour < end_ts:
            key = (hour.date().isoformat(), hour.hour)
            scheduled_by_hour[key] = scheduled_by_hour.get(key, 0) + 1
            hour += pd.Timedelta(hours=1)

    points = []
    for business_date, hour_of_day, required in required_rows:
        key = (str(business_date)[:10], hour_of_day)
        scheduled = scheduled_by_hour.get(key, 0)
        gap = scheduled - required
        status = "Understaffed" if gap < 0 else "Overstaffed" if gap > 2 else "Balanced"
        points.append({
            "date": f"{str(business_date)[:10]}T{hour_of_day:02d}:00:00",
            "required_headcount": required, "scheduled_headcount": scheduled, "gap": gap, "status": status,
        })

    return {
        "series_key": series_key, "window_start": start_date.isoformat(), "window_end": max_date.isoformat(),
        "note": "Uses the most recent window where both real demand and real schedule data exist - future schedules don't exist yet, which is exactly the gap forecasting closes.",
        "points": points,
    }
