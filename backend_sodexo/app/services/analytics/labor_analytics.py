"""Labor Analytics - real aggregations over the canonical DB (fact_timecard,
fact_attendance, fact_punch, dim_employee/job/department/location,
employee_pay_rate, fact_labor_budget, fact_census/fact_cafe_sales). No
number here is hardcoded - everything is computed from whatever the DB
currently holds, so a re-ingest or a schema/rate change changes these
outputs on the next request.

Grouping is location + department + cost_center only (no region dimension
exists in this dataset - confirmed with the user when scoping this phase).
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

_LATE_GRACE_MINUTES = 5
_NEAR_OT_THRESHOLD_PCT = 0.9  # flag employees whose worked hours reach 90%+ of their standard weekly hours

# Scorecard scales. Each metric is scored between a "good" anchor (=100) and a
# "poor" anchor (=0), rather than pass/fail against a single target.
#
# A single hard target does not work here: overtime and budget both pin at 100
# against a fixed target, which squashes every total into a meaningless band.
# Scoring across a range spreads the sites out and still says plainly what good
# and poor look like. Both anchors and the target are surfaced on the dashboard
# - these are POC defaults, not Sodexo policy.
#
# schedule_adherence is deliberately NOT scored. Every site sits at 84-87%
# against any sensible target, so it marked all four "off target" every time
# and separated nobody. It is still returned as schedule_adherence_pct for
# anyone who wants it; it just does not feed the score.
_SCORECARD_SCALES = {
    # metric: (good_anchor, poor_anchor, target, higher_is_better)
    "overtime_control": (0.0, 15.0, 5.0, False),
    "punch_accuracy": (0.0, 10.0, 2.0, False),
    "budget_control": (-5.0, 20.0, 0.0, False),
}
# Spending far below budget is not automatically a win - it can mean the budget
# is wrong or shifts went uncovered, so it gets called out rather than praised.
_UNDERSPEND_REVIEW_PCT = -15.0


def get_filters(db: Session) -> dict:
    locations = db.execute(text("SELECT location_id, location_name FROM dim_location ORDER BY location_id")).mappings().all()
    departments = db.execute(text("SELECT department_id, department_name, cost_center_id FROM dim_department ORDER BY department_id")).mappings().all()
    bounds = db.execute(text("SELECT MIN(work_date), MAX(work_date) FROM fact_timecard")).first()
    return {
        "locations": [dict(r) for r in locations],
        "departments": [dict(r) for r in departments],
        "date_range": {"min": bounds[0], "max": bounds[1]} if bounds and bounds[0] else None,
    }


def _timecard_frame(db: Session, location_id: str | None, department_id: str | None, start: str | None, end: str | None) -> pd.DataFrame:
    sql = """
        SELECT t.employee_id, t.work_date, t.scheduled_hours, t.regular_hours, t.overtime_hours,
               t.total_worked_hours, t.exception_flag,
               e.location_id, e.department_id, e.job_id, e.standard_weekly_hours, e.base_hourly_rate,
               d.department_name, d.cost_center_id, l.location_name, j.job_title,
               COALESCE(p.overtime_multiplier, 1.5) AS overtime_multiplier
        FROM fact_timecard t
        JOIN dim_employee e ON e.employee_id = t.employee_id
        JOIN dim_department d ON d.department_id = e.department_id
        JOIN dim_location l ON l.location_id = e.location_id
        JOIN dim_job j ON j.job_id = e.job_id
        LEFT JOIN employee_pay_rate p ON p.employee_id = t.employee_id
        WHERE t.work_date BETWEEN :start AND :end
    """
    params = {"start": start, "end": end}
    if location_id:
        sql += " AND e.location_id = :location_id"
        params["location_id"] = location_id
    if department_id:
        sql += " AND e.department_id = :department_id"
        params["department_id"] = department_id
    df = pd.read_sql_query(text(sql), db.get_bind(), params=params)
    df["regular_cost"] = df["regular_hours"] * df["base_hourly_rate"]
    df["overtime_cost"] = df["overtime_hours"] * df["base_hourly_rate"] * df["overtime_multiplier"]
    df["total_cost"] = df["regular_cost"] + df["overtime_cost"]
    df["work_date"] = pd.to_datetime(df["work_date"])
    return df


def _attendance_frame(db: Session, location_id: str | None, department_id: str | None, start: str | None, end: str | None) -> pd.DataFrame:
    sql = """
        SELECT a.employee_id, a.work_date, a.attendance_status, a.late_minutes, a.missed_punch_flag,
               e.location_id, e.department_id, l.location_name, d.department_name
        FROM fact_attendance a
        JOIN dim_employee e ON e.employee_id = a.employee_id
        JOIN dim_location l ON l.location_id = e.location_id
        JOIN dim_department d ON d.department_id = e.department_id
        WHERE a.work_date BETWEEN :start AND :end
    """
    params = {"start": start, "end": end}
    if location_id:
        sql += " AND e.location_id = :location_id"
        params["location_id"] = location_id
    if department_id:
        sql += " AND e.department_id = :department_id"
        params["department_id"] = department_id
    return pd.read_sql_query(text(sql), db.get_bind(), params=params)


def _punch_frame(db: Session, location_id: str | None, department_id: str | None, start: str | None, end: str | None) -> pd.DataFrame:
    sql = """
        SELECT p.employee_id, p.punch_date, p.exception_flag, p.manual_edit_flag,
               e.location_id, e.department_id
        FROM fact_punch p
        JOIN dim_employee e ON e.employee_id = p.employee_id
        WHERE p.punch_date BETWEEN :start AND :end
    """
    params = {"start": start, "end": end}
    if location_id:
        sql += " AND e.location_id = :location_id"
        params["location_id"] = location_id
    if department_id:
        sql += " AND e.department_id = :department_id"
        params["department_id"] = department_id
    return pd.read_sql_query(text(sql), db.get_bind(), params=params)


def _productivity(db: Session, location_id: str | None, department_id: str | None, start: str | None, end: str | None, timecards: pd.DataFrame) -> list[dict]:
    """Meals/transactions per labor hour, by location+department, for the
    same window - only departments/locations with both a demand signal and
    ingested labor hours produce a ratio."""
    results = []
    for dept, table, flow_column in (("DEP01", "fact_census", "meals_served"), ("DEP02", "fact_cafe_sales", "transaction_count")):
        if department_id and department_id != dept:
            continue
        sql = f"SELECT location_id, SUM({flow_column}) AS total_flow FROM {table} WHERE business_date BETWEEN :start AND :end"
        params = {"start": start, "end": end}
        if location_id:
            sql += " AND location_id = :location_id"
            params["location_id"] = location_id
        sql += " GROUP BY location_id"
        flow_rows = db.execute(text(sql), params).all()
        hours_by_location = timecards[timecards["department_id"] == dept].groupby("location_id")["total_worked_hours"].sum()
        for loc, total_flow in flow_rows:
            hours = float(hours_by_location.get(loc, 0.0))
            results.append({
                "location_id": loc, "department_id": dept, "signal": flow_column,
                "total_flow": float(total_flow), "total_labor_hours": round(hours, 1),
                "flow_per_labor_hour": round(total_flow / hours, 2) if hours > 0 else None,
            })
    return results


def _headline_totals(db: Session, location_id: str | None, department_id: str | None, start: str, end: str) -> dict:
    """The handful of numbers the dashboard tiles compare period-over-period.
    Deliberately light - it runs a second time for the preceding window."""
    tc = _timecard_frame(db, location_id, department_id, start, end)
    att = _attendance_frame(db, location_id, department_id, start, end)
    if tc.empty:
        return {}
    actual = float(tc["total_worked_hours"].sum())
    scheduled = float(tc["scheduled_hours"].sum())
    return {
        "total_labor_cost": round(float(tc["total_cost"].sum()), 2),
        "overtime_hours": round(float(tc["overtime_hours"].sum()), 1),
        "overtime_cost": round(float(tc["overtime_cost"].sum()), 2),
        "actual_hours": round(actual, 1),
        "attendance_rate_pct": round(float((att["attendance_status"] == "Present").mean() * 100), 1) if len(att) else None,
        "labor_utilization_pct": round(actual / scheduled * 100, 1) if scheduled else None,
        "cost_per_hour": round(float(tc["total_cost"].sum()) / actual, 2) if actual else None,
    }


def _previous_window(start: str, end: str) -> tuple[str, str]:
    """The equal-length window immediately before the selected one."""
    s, e = pd.to_datetime(start), pd.to_datetime(end)
    span = (e - s).days + 1
    prev_end = s - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=span - 1)
    return prev_start.date().isoformat(), prev_end.date().isoformat()


def _period_key(dates: pd.Series, grain: str) -> pd.Series:
    """Buckets a date column to the requested reporting grain.

    Note there is deliberately no hourly option here: labor cost, overtime,
    attendance and scheduled-vs-actual all come from fact_timecard, which is
    one row per employee per DAY. An hourly labor-cost figure does not exist
    in the source data, so offering the filter would be inventing precision.
    Time Punch Analytics does support hourly, because punches carry timestamps.
    """
    if grain == "monthly":
        return dates.dt.to_period("M").dt.start_time.dt.date
    if grain == "weekly":
        return (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.date
    return dates.dt.date


def compute(
    db: Session,
    location_id: str | None = None,
    department_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    grain: str = "daily",
    last_days: int | None = None,
) -> dict:
    if not start_date or not end_date:
        bounds = db.execute(text("SELECT MIN(work_date), MAX(work_date) FROM fact_timecard")).first()
        start_date = start_date or (bounds[0] if bounds else None)
        end_date = end_date or (bounds[1] if bounds else None)
        # "Last N days" is anchored to the newest data, not to today - the
        # dataset ends before the current date, so anchoring to today would
        # return an empty window.
        if last_days and end_date:
            start_date = (pd.to_datetime(end_date) - pd.Timedelta(days=last_days - 1)).date().isoformat()
    if not start_date or not end_date:
        return {"error": "No timecard data ingested yet - run ingestion first."}

    tc = _timecard_frame(db, location_id, department_id, start_date, end_date)
    att = _attendance_frame(db, location_id, department_id, start_date, end_date)
    punch = _punch_frame(db, location_id, department_id, start_date, end_date)

    if tc.empty:
        return {"error": "No timecard rows in the selected window/filters."}

    total_scheduled = float(tc["scheduled_hours"].sum())
    total_actual = float(tc["total_worked_hours"].sum())
    total_ot_hours = float(tc["overtime_hours"].sum())
    total_cost = float(tc["total_cost"].sum())
    total_ot_cost = float(tc["overtime_cost"].sum())

    budget_sql = "SELECT location_id, department_id, year, month, labor_budget, overtime_budget FROM fact_labor_budget WHERE 1=1"
    budget_params: dict = {}
    if location_id:
        budget_sql += " AND location_id=:loc"
        budget_params["loc"] = location_id
    if department_id:
        budget_sql += " AND department_id=:dept"
        budget_params["dept"] = department_id
    years_months = sorted({(d.year, d.month) for d in tc["work_date"]})
    budget_rows = db.execute(text(budget_sql), budget_params).all()
    relevant_budget = [b for b in budget_rows if (b.year, b.month) in years_months]
    total_budget = sum(b.labor_budget for b in relevant_budget)
    total_ot_budget = sum(b.overtime_budget for b in relevant_budget)
    cost_vs_budget_pct = round((total_cost - total_budget) / total_budget * 100, 1) if total_budget else None

    kpis = {
        "total_labor_cost": round(total_cost, 2),
        "total_budget": round(total_budget, 2) if total_budget else None,
        "labor_cost_vs_budget_pct": cost_vs_budget_pct,
        "scheduled_hours": round(total_scheduled, 1),
        "actual_hours": round(total_actual, 1),
        "variance_hours": round(total_actual - total_scheduled, 1),
        "overtime_hours": round(total_ot_hours, 1),
        "overtime_cost": round(total_ot_cost, 2),
        "overtime_budget": round(total_ot_budget, 2) if total_ot_budget else None,
        "attendance_rate_pct": round(float((att["attendance_status"] == "Present").mean() * 100), 1) if len(att) else None,
        "labor_utilization_pct": round(total_actual / total_scheduled * 100, 1) if total_scheduled else None,
    }

    by_location = tc.groupby(["location_id", "location_name"]).agg(
        cost=("total_cost", "sum"), scheduled_hours=("scheduled_hours", "sum"), actual_hours=("total_worked_hours", "sum"), overtime_hours=("overtime_hours", "sum"),
    ).reset_index().round(2).to_dict("records")

    by_department = tc.groupby(["department_id", "department_name", "cost_center_id"]).agg(
        cost=("total_cost", "sum"), scheduled_hours=("scheduled_hours", "sum"), actual_hours=("total_worked_hours", "sum"), overtime_hours=("overtime_hours", "sum"),
    ).reset_index().round(2).to_dict("records")

    tc = tc.copy()
    tc["period"] = _period_key(tc["work_date"], grain)

    cost_trend = tc.groupby("period")["total_cost"].sum().round(2)
    cost_trend_series = [{"date": str(d), "cost": v} for d, v in cost_trend.items()]

    # Cost split by location and by cost centre over time, so the client can see
    # which site / cost centre is driving a movement rather than only the total.
    def _pivot_over_time(group_column: str, value_column: str, label_column: str | None = None) -> list[dict]:
        label = label_column or group_column
        grouped = tc.groupby(["period", label])[value_column].sum().round(2).reset_index()
        rows: dict[str, dict] = {}
        for record in grouped.to_dict("records"):
            row = rows.setdefault(str(record["period"]), {"date": str(record["period"])})
            row[str(record[label])] = record[value_column]
        return list(rows.values())

    cost_by_location_over_time = _pivot_over_time("location_id", "total_cost", "location_name")
    cost_by_cost_center_over_time = _pivot_over_time("cost_center_id", "total_cost")
    location_names = sorted(tc["location_name"].dropna().unique().tolist())
    cost_center_ids = sorted(tc["cost_center_id"].dropna().astype(str).unique().tolist())

    scheduled_vs_actual_trend = tc.groupby("period").agg(scheduled=("scheduled_hours", "sum"), actual=("total_worked_hours", "sum")).round(2)
    scheduled_vs_actual_series = [
        {"date": str(d), "scheduled": row.scheduled, "actual": row.actual, "variance": round(row.actual - row.scheduled, 2)}
        for d, row in scheduled_vs_actual_trend.iterrows()
    ]

    ot_trend = tc.groupby("period").agg(overtime_hours=("overtime_hours", "sum"), overtime_cost=("overtime_cost", "sum")).round(2)
    ot_trend_series = [{"date": str(d), "overtime_hours": row.overtime_hours, "overtime_cost": row.overtime_cost} for d, row in ot_trend.iterrows()]

    # Utilization / efficiency as a time series rather than a single headline
    # number, so a drift in either is visible rather than averaged away.
    eff = tc.groupby("period").agg(
        scheduled=("scheduled_hours", "sum"), actual=("total_worked_hours", "sum"),
        overtime=("overtime_hours", "sum"), cost=("total_cost", "sum"),
    )
    efficiency_series = [
        {
            "date": str(d),
            "utilization_pct": round(row.actual / row.scheduled * 100, 1) if row.scheduled else None,
            "overtime_share_pct": round(row.overtime / row.actual * 100, 1) if row.actual else None,
            "cost_per_hour": round(row.cost / row.actual, 2) if row.actual else None,
        }
        for d, row in eff.iterrows()
    ]
    ot_by_location = tc.groupby(["location_id", "location_name"])["overtime_hours"].sum().round(1).reset_index().to_dict("records")
    ot_by_department = tc.groupby(["department_id", "department_name"])["overtime_hours"].sum().round(1).reset_index().to_dict("records")

    per_employee = tc.groupby("employee_id").agg(total_worked_hours=("total_worked_hours", "sum"), standard_weekly_hours=("standard_weekly_hours", "first")).reset_index()
    weeks_covered = max(1, (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days / 7)
    per_employee["standard_hours_for_window"] = per_employee["standard_weekly_hours"] * weeks_covered
    near_ot = per_employee[per_employee["total_worked_hours"] >= per_employee["standard_hours_for_window"] * _NEAR_OT_THRESHOLD_PCT]
    employees_near_overtime = near_ot["employee_id"].tolist()

    attendance_breakdown = att["attendance_status"].value_counts().to_dict() if len(att) else {}
    absenteeism_pct = round(float((att["attendance_status"] == "Absent").mean() * 100), 1) if len(att) else None
    late_pct = round(float((att["attendance_status"] == "Late").mean() * 100), 1) if len(att) else None

    attendance_series = []
    if len(att):
        att = att.copy()
        att["period"] = _period_key(pd.to_datetime(att["work_date"]), grain)
        for period, group in att.groupby("period"):
            attendance_series.append({
                "date": str(period),
                "present_pct": round(float((group["attendance_status"] == "Present").mean() * 100), 1),
                "absent_pct": round(float((group["attendance_status"] == "Absent").mean() * 100), 1),
                "late_pct": round(float((group["attendance_status"] == "Late").mean() * 100), 1),
            })

    productivity = _productivity(db, location_id, department_id, start_date, end_date, tc)

    shift_util = tc.groupby(["location_id", "department_id"]).agg(
        avg_scheduled_hours=("scheduled_hours", "mean"), avg_actual_hours=("total_worked_hours", "mean"),
    ).round(2)
    shift_util["utilization_pct"] = (shift_util["avg_actual_hours"] / shift_util["avg_scheduled_hours"] * 100).round(1)
    shift_utilization = shift_util.reset_index().to_dict("records")

    manager_performance = []
    for (loc, dept), group in tc.groupby(["location_id", "department_id"]):
        group_punches = punch[(punch["location_id"] == loc) & (punch["department_id"] == dept)]
        exception_rate = round(float((group_punches["exception_flag"] | group_punches["manual_edit_flag"]).mean() * 100), 1) if len(group_punches) else None
        group_att = att[(att["location_id"] == loc) & (att["department_id"] == dept)]
        adherence = round(float(((group_att["attendance_status"] == "Present") & (group_att["late_minutes"] <= _LATE_GRACE_MINUTES) & (~group_att["missed_punch_flag"])).mean() * 100), 1) if len(group_att) else None
        group_budget = [b for b in relevant_budget if b.location_id == loc and b.department_id == dept]
        budget_sum = sum(b.labor_budget for b in group_budget)
        cost_vs_budget = round((group["total_cost"].sum() - budget_sum) / budget_sum * 100, 1) if budget_sum else None
        ot_pct = round(float(group["overtime_hours"].sum() / group["total_worked_hours"].sum() * 100), 1) if group["total_worked_hours"].sum() else None

        # Score each metric on its own good..poor scale so the four sites
        # actually separate, then average whatever is measurable.
        def _score(value, metric):
            if value is None:
                return None
            good, poor, _target, _higher = _SCORECARD_SCALES[metric]
            if poor == good:
                return None
            return round(max(0.0, min(100.0, (poor - value) / (poor - good) * 100)), 1)

        raw_values = {
            "overtime_control": ot_pct,
            "punch_accuracy": exception_rate,
            "budget_control": cost_vs_budget,
        }
        components = {metric: _score(value, metric) for metric, value in raw_values.items()}
        measurable = [v for v in components.values() if v is not None]
        manager_performance.append({
            "location_id": loc, "department_id": dept,
            "department_name": group["department_name"].iloc[0], "location_name": group["location_name"].iloc[0],
            "overtime_control_pct": ot_pct,
            "punch_exception_rate_pct": exception_rate,
            "schedule_adherence_pct": adherence,
            "labor_cost_vs_budget_pct": cost_vs_budget,
            "labor_cost": round(float(group["total_cost"].sum()), 2),
            "actual_hours": round(float(group["total_worked_hours"].sum()), 1),
            "headcount": int(group["employee_id"].nunique()),
            "score": round(sum(measurable) / len(measurable), 1) if measurable else None,
            "score_components": components,
            "metric_values": raw_values,
            # Strongest / weakest measure for this site, so the card can say
            # what to actually work on rather than only showing a total.
            "best_metric": max((m for m in components if components[m] is not None), key=lambda m: components[m], default=None),
            "worst_metric": min((m for m in components if components[m] is not None), key=lambda m: components[m], default=None),
            "underspend_review": bool(cost_vs_budget is not None and cost_vs_budget <= _UNDERSPEND_REVIEW_PCT),
        })
    manager_performance.sort(key=lambda m: m["score"] if m["score"] is not None else -1, reverse=True)
    for rank, manager in enumerate(manager_performance, start=1):
        manager["rank"] = rank

    # Period-over-period deltas against the equal-length preceding window, so
    # each dashboard tile can show direction rather than a bare number.
    prev_start, prev_end = _previous_window(start_date, end_date)
    previous = _headline_totals(db, location_id, department_id, prev_start, prev_end)
    current = _headline_totals(db, location_id, department_id, start_date, end_date)
    # Metrics that are already percentages are compared in percentage POINTS.
    # Dividing them would report an attendance move of 86.7% -> 86.3% as
    # "-0.5%", which reads as half a percent of absence when it is actually
    # 0.4 points - a different claim. Money and hours use percent change.
    deltas: dict[str, float | None] = {}
    delta_units: dict[str, str] = {}
    for metric, now in current.items():
        was = previous.get(metric)
        is_pct = metric.endswith("_pct")
        delta_units[metric] = "pt" if is_pct else "%"
        if now is None or was is None:
            deltas[metric] = None
        elif is_pct:
            deltas[metric] = round(now - was, 1)
        else:
            deltas[metric] = round((now - was) / abs(was) * 100, 1) if was else None

    return {
        "window": {"start": start_date, "end": end_date},
        "previous_window": {"start": prev_start, "end": prev_end},
        "kpi_deltas": deltas,
        "kpi_delta_units": delta_units,
        "grain": grain,
        "kpis": kpis,
        "labor_cost": {
            "by_location": by_location, "by_department": by_department, "trend": cost_trend_series,
            "by_location_over_time": cost_by_location_over_time, "location_names": location_names,
            "by_cost_center_over_time": cost_by_cost_center_over_time, "cost_center_ids": cost_center_ids,
        },
        "scheduled_vs_actual": {"trend": scheduled_vs_actual_series},
        "overtime": {
            "trend": ot_trend_series, "by_location": ot_by_location, "by_department": ot_by_department,
            "employees_near_overtime": employees_near_overtime,
        },
        "attendance": {
            "breakdown": attendance_breakdown, "absenteeism_pct": absenteeism_pct, "late_pct": late_pct,
            "trend": attendance_series,
        },
        "efficiency": {"trend": efficiency_series},
        "productivity": productivity,
        "shift_utilization": shift_utilization,
        "manager_performance": manager_performance,
        "manager_scales": {
            metric: {"good": good, "poor": poor, "target": target, "higher_is_better": higher}
            for metric, (good, poor, target, higher) in _SCORECARD_SCALES.items()
        },
        "underspend_review_pct": _UNDERSPEND_REVIEW_PCT,
    }
