"""Staffing-Gap / Peak-Workload Risk - a real classifier predicting
P(understaffed) for a given hour, trained on the real overlap window where
both true required labor (fact_census/fact_cafe_sales.required_manpower)
and real schedules (fact_schedule) exist. Calendar-only features
(hour_of_day, day_of_week, is_holiday), so the model can score any future
hour without needing data that doesn't exist yet.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.forecasting.contract import SERIES
from app.services.predictions.common import ModelNotApplicable, risk_level, train_and_evaluate

_FEATURES = ["hour_of_day", "dayofweek", "is_holiday"]


def _build_frame(db: Session, series) -> pd.DataFrame:
    max_schedule_date = db.execute(text("SELECT MAX(shift_date) FROM fact_schedule")).scalar()
    if not max_schedule_date:
        raise ModelNotApplicable("No schedule data ingested yet.")
    max_date = date.fromisoformat(str(max_schedule_date)[:10])
    min_schedule_date = db.execute(text("SELECT MIN(shift_date) FROM fact_schedule")).scalar()
    start_date = date.fromisoformat(str(min_schedule_date)[:10])
    department_id = "DEP01" if series.fact_model.__tablename__ == "fact_census" else "DEP02"
    table = series.fact_model.__tablename__

    required = pd.read_sql_query(text(
        f"SELECT business_date, hour_of_day, required_manpower FROM {table} "
        "WHERE location_id=:loc AND business_date BETWEEN :start AND :end"
    ), db.get_bind(), params={"loc": series.location_id, "start": start_date.isoformat(), "end": max_date.isoformat()})

    schedule = db.execute(text(
        "SELECT shift_date, scheduled_start, scheduled_end FROM fact_schedule "
        "WHERE location_id=:loc AND department_id=:dept AND shift_date BETWEEN :start AND :end"
    ), {"loc": series.location_id, "dept": department_id, "start": start_date.isoformat(), "end": max_date.isoformat()}).all()

    scheduled_by_hour: dict[tuple, int] = {}
    for _shift_date, start, end in schedule:
        hour = pd.Timestamp(start).replace(minute=0, second=0, microsecond=0)
        end_ts = pd.Timestamp(end)
        while hour < end_ts:
            key = (hour.date().isoformat(), hour.hour)
            scheduled_by_hour[key] = scheduled_by_hour.get(key, 0) + 1
            hour += pd.Timedelta(hours=1)

    holidays = {str(r[0])[:10] for r in db.execute(text("SELECT business_date FROM dim_date WHERE is_holiday=1")).all()}

    required["business_date"] = pd.to_datetime(required["business_date"])
    required["scheduled"] = required.apply(lambda r: scheduled_by_hour.get((r["business_date"].date().isoformat(), r["hour_of_day"]), 0), axis=1)
    required["label"] = (required["scheduled"] < required["required_manpower"]).astype(int)
    required["dayofweek"] = required["business_date"].dt.dayofweek
    required["is_holiday"] = required["business_date"].dt.date.astype(str).isin(holidays).astype(int)
    required["work_date"] = required["business_date"]
    return required


def compute(db: Session) -> list[dict]:
    results = []
    for series in SERIES:
        entry = {"series_key": series.key, "series_label": series.label, "model_id": "staffing_gap_risk", "model_label": "Staffing Gap / Peak Workload Risk", "model_version": "gbc-v1"}
        try:
            frame = _build_frame(db, series)
            result = train_and_evaluate(frame, _FEATURES, "label", "work_date")
        except ModelNotApplicable as exc:
            entry.update({"status": "not_applicable", "reason": str(exc)})
            results.append(entry)
            continue

        next_24h = pd.DataFrame({
            "hour_of_day": list(range(24)),
            "dayofweek": [(frame["business_date"].max() + timedelta(days=1)).dayofweek] * 24,
            "is_holiday": [0] * 24,
        })
        probabilities = result["model"].predict_proba(next_24h[_FEATURES])[:, 1]
        hourly_risk = [{"hour_of_day": h, "risk_probability": round(float(p), 3), "risk_level": risk_level(float(p))} for h, p in zip(range(24), probabilities)]

        entry.update({
            "status": "completed", "metrics": result["metrics"], "top_drivers": result["top_drivers"],
            "train_rows": result["train_rows"], "test_rows": result["test_rows"], "positive_rate": result["positive_rate"],
            "hourly_risk_next_day": sorted(hourly_risk, key=lambda r: r["risk_probability"], reverse=True),
        })
        results.append(entry)
    return results
