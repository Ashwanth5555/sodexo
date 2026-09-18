"""Overtime, Absenteeism, Schedule-Adherence, and Payroll-Anomaly risk -
real GradientBoostingClassifier models trained on real historical labels
from fact_timecard/fact_attendance, with real recency features. Current
risk per employee is the model's own predicted probability on that
employee's most recent feature snapshot - not a lookup or a guess.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.predictions.common import ModelNotApplicable, add_recency_features, risk_level, train_and_evaluate


def _employee_lookup(db: Session) -> pd.DataFrame:
    sql = """
        SELECT e.employee_id, e.employee_name, e.location_id, e.department_id,
               l.location_name, d.department_name
        FROM dim_employee e
        JOIN dim_location l ON l.location_id = e.location_id
        JOIN dim_department d ON d.department_id = e.department_id
    """
    return pd.read_sql_query(text(sql), db.get_bind()).set_index("employee_id")


def _timecard_frame(db: Session) -> pd.DataFrame:
    df = pd.read_sql_query(text("""
        SELECT employee_id, work_date, scheduled_hours, regular_hours, overtime_hours, total_worked_hours, exception_flag
        FROM fact_timecard
    """), db.get_bind())
    df["work_date"] = pd.to_datetime(df["work_date"])
    return df


def _attendance_frame(db: Session) -> pd.DataFrame:
    df = pd.read_sql_query(text("""
        SELECT employee_id, work_date, attendance_status, late_minutes, missed_punch_flag
        FROM fact_attendance
    """), db.get_bind())
    df["work_date"] = pd.to_datetime(df["work_date"])
    return df


def _current_risk_per_employee(df: pd.DataFrame, model, feature_columns: list[str], employees: pd.DataFrame) -> list[dict]:
    latest = df.sort_values("work_date").groupby("employee_id").tail(1)
    probabilities = model.predict_proba(latest[feature_columns])[:, 1]
    results = []
    for (_, row), probability in zip(latest.iterrows(), probabilities):
        info = employees.loc[row["employee_id"]] if row["employee_id"] in employees.index else None
        results.append({
            "employee_id": row["employee_id"],
            "employee_name": info["employee_name"] if info is not None else row["employee_id"],
            "location_id": info["location_id"] if info is not None else None,
            "location_name": info["location_name"] if info is not None else None,
            "department_id": info["department_id"] if info is not None else None,
            "department_name": info["department_name"] if info is not None else None,
            "risk_probability": round(float(probability), 3),
            "risk_level": risk_level(float(probability)),
        })
    return sorted(results, key=lambda r: r["risk_probability"], reverse=True)


def overtime_risk(db: Session) -> dict:
    tc = _timecard_frame(db)
    tc["label"] = (tc["overtime_hours"] > 0).astype(int)
    tc = add_recency_features(tc, "employee_id", "work_date", "label")
    features = ["recent_7d", "recent_14d", "recent_30d", "dayofweek", "is_weekend", "scheduled_hours"]
    result = train_and_evaluate(tc, features, "label", "work_date")
    employees = _employee_lookup(db)
    return {
        "model_id": "overtime_risk", "model_label": "Overtime Risk", "model_version": "gbc-v1",
        "metrics": result["metrics"], "top_drivers": result["top_drivers"],
        "train_rows": result["train_rows"], "test_rows": result["test_rows"], "positive_rate": result["positive_rate"],
        "employees": _current_risk_per_employee(tc, result["model"], features, employees),
    }


def absenteeism_risk(db: Session) -> dict:
    att = _attendance_frame(db)
    att["label"] = (att["attendance_status"] == "Absent").astype(int)
    att = add_recency_features(att, "employee_id", "work_date", "label")
    features = ["recent_7d", "recent_14d", "recent_30d", "dayofweek", "is_weekend"]
    result = train_and_evaluate(att, features, "label", "work_date")
    employees = _employee_lookup(db)
    return {
        "model_id": "absenteeism_risk", "model_label": "Absenteeism Risk", "model_version": "gbc-v1",
        "metrics": result["metrics"], "top_drivers": result["top_drivers"],
        "train_rows": result["train_rows"], "test_rows": result["test_rows"], "positive_rate": result["positive_rate"],
        "employees": _current_risk_per_employee(att, result["model"], features, employees),
    }


def schedule_adherence_risk(db: Session) -> dict:
    att = _attendance_frame(db)
    att["label"] = ((att["late_minutes"] > 5) | att["missed_punch_flag"]).astype(int)
    att = add_recency_features(att, "employee_id", "work_date", "label")
    features = ["recent_7d", "recent_14d", "recent_30d", "dayofweek", "is_weekend"]
    result = train_and_evaluate(att, features, "label", "work_date")
    employees = _employee_lookup(db)
    return {
        "model_id": "schedule_adherence_risk", "model_label": "Schedule Adherence Risk", "model_version": "gbc-v1",
        "metrics": result["metrics"], "top_drivers": result["top_drivers"],
        "train_rows": result["train_rows"], "test_rows": result["test_rows"], "positive_rate": result["positive_rate"],
        "employees": _current_risk_per_employee(att, result["model"], features, employees),
    }


def payroll_anomaly_risk(db: Session) -> dict:
    tc = _timecard_frame(db)
    tc["label"] = tc["exception_flag"].astype(int)
    tc = add_recency_features(tc, "employee_id", "work_date", "label")
    features = ["recent_7d", "recent_14d", "recent_30d", "dayofweek", "is_weekend", "scheduled_hours", "overtime_hours"]
    result = train_and_evaluate(tc, features, "label", "work_date")
    employees = _employee_lookup(db)
    return {
        "model_id": "payroll_anomaly_risk", "model_label": "Payroll Anomaly Risk", "model_version": "gbc-v1",
        "metrics": result["metrics"], "top_drivers": result["top_drivers"],
        "train_rows": result["train_rows"], "test_rows": result["test_rows"], "positive_rate": result["positive_rate"],
        "employees": _current_risk_per_employee(tc, result["model"], features, employees),
    }
