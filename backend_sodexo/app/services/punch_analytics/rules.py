"""Real, rule-based anomaly detection over fact_punch/fact_schedule/
fact_attendance/fact_timecard - no ML, no fabrication. Every anomaly is
produced by a disclosed threshold rule so it can be explained and defended.

Buddy-punch and manual-edit patterns are reported as review flags, never as
an accusation - matching the client's explicit instruction not to label
anyone a fraud risk.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

EARLY_CLOCK_IN_MINUTES = 15
LATE_CLOCK_OUT_MINUTES = 15
LONG_BREAK_MINUTES = 45
DUPLICATE_PUNCH_MINUTES = 10
BUDDY_PUNCH_WINDOW_MINUTES = 2
MANUAL_EDIT_REPEAT_THRESHOLD = 3
EXCESS_HOURS_MARGIN = 1.10
EXCESS_HOURS_REPEAT_THRESHOLD = 3

# Alerting / watchlist thresholds - deliberately simple and disclosed.
REPEAT_EMPLOYEE_ALERT_THRESHOLD = 3  # flagged punches by one person before it is called out
WATCHLIST_MIN_RECENT = 2             # recent flags needed before predicting more


def _employee_lookup(db: Session) -> pd.DataFrame:
    sql = """
        SELECT e.employee_id, e.employee_name, e.location_id, e.department_id,
               l.location_name, d.department_name
        FROM dim_employee e
        JOIN dim_location l ON l.location_id = e.location_id
        JOIN dim_department d ON d.department_id = e.department_id
    """
    return pd.read_sql_query(text(sql), db.get_bind())


def _load_frames(db: Session, location_id: str | None, department_id: str | None, start: str, end: str):
    emp_filter = ""
    params = {"start": start, "end": end}
    if location_id:
        emp_filter += " AND e.location_id = :location_id"
        params["location_id"] = location_id
    if department_id:
        emp_filter += " AND e.department_id = :department_id"
        params["department_id"] = department_id

    punches = pd.read_sql_query(text(f"""
        SELECT p.employee_id, p.punch_timestamp, p.punch_type, p.device_id, p.manual_edit_flag, p.exception_flag
        FROM fact_punch p JOIN dim_employee e ON e.employee_id = p.employee_id
        WHERE p.punch_date BETWEEN :start AND :end {emp_filter}
    """), db.get_bind(), params=params)
    punches["punch_timestamp"] = pd.to_datetime(punches["punch_timestamp"])
    punches["punch_date"] = punches["punch_timestamp"].dt.date

    schedule = pd.read_sql_query(text(f"""
        SELECT s.employee_id, s.shift_date, s.scheduled_start, s.scheduled_end, s.scheduled_hours
        FROM fact_schedule s JOIN dim_employee e ON e.employee_id = s.employee_id
        WHERE s.shift_date BETWEEN :start AND :end {emp_filter}
    """), db.get_bind(), params=params)
    schedule["scheduled_start"] = pd.to_datetime(schedule["scheduled_start"])
    schedule["scheduled_end"] = pd.to_datetime(schedule["scheduled_end"])
    schedule["shift_date"] = pd.to_datetime(schedule["shift_date"]).dt.date

    attendance = pd.read_sql_query(text(f"""
        SELECT a.employee_id, a.work_date, a.schedule_id, a.attendance_status, a.late_minutes, a.missed_punch_flag
        FROM fact_attendance a JOIN dim_employee e ON e.employee_id = a.employee_id
        WHERE a.work_date BETWEEN :start AND :end {emp_filter}
    """), db.get_bind(), params=params)
    attendance["work_date"] = pd.to_datetime(attendance["work_date"]).dt.date

    timecard = pd.read_sql_query(text(f"""
        SELECT t.employee_id, t.work_date, t.scheduled_hours, t.regular_hours, t.overtime_hours,
               t.total_worked_hours, t.exception_flag
        FROM fact_timecard t JOIN dim_employee e ON e.employee_id = t.employee_id
        WHERE t.work_date BETWEEN :start AND :end {emp_filter}
    """), db.get_bind(), params=params)
    timecard["work_date"] = pd.to_datetime(timecard["work_date"]).dt.date

    return punches, schedule, attendance, timecard


def _severity_for(minutes_over: float, low: float, high: float) -> str:
    if minutes_over >= high:
        return "High"
    if minutes_over >= low:
        return "Medium"
    return "Low"


def detect_anomalies(db: Session, location_id: str | None = None, department_id: str | None = None, start_date: str | None = None, end_date: str | None = None, grain: str = "daily") -> dict:
    if not start_date or not end_date:
        bounds = db.execute(text("SELECT MIN(punch_date), MAX(punch_date) FROM fact_punch")).first()
        start_date = start_date or (bounds[0] if bounds else None)
        end_date = end_date or (bounds[1] if bounds else None)
    if not start_date or not end_date:
        return {"error": "No punch data ingested yet - run ingestion first.", "anomalies": [], "kpis": {}, "grain": grain, "trend": [], "alerts": [], "watchlist": []}

    # A plain dict, not a DataFrame index. emp_info() is called once per
    # anomaly and DataFrame .loc on a scalar key costs ~0.4ms; at 2,000+
    # anomalies that alone was several seconds.
    employees = {
        row["employee_id"]: {
            "employee_name": row["employee_name"],
            "location_id": row["location_id"], "location_name": row["location_name"],
            "department_id": row["department_id"], "department_name": row["department_name"],
        }
        for row in _employee_lookup(db).to_dict("records")
    }
    _UNKNOWN_EMP = {"employee_name": None, "location_id": None, "location_name": None,
                    "department_id": None, "department_name": None}
    punches, schedule, attendance, timecard = _load_frames(db, location_id, department_id, start_date, end_date)

    anomalies: list[dict] = []
    next_id = 1

    def emp_info(employee_id: str) -> dict:
        info = employees.get(employee_id)
        if info is not None:
            return info
        return {**_UNKNOWN_EMP, "employee_name": employee_id}

    def add(employee_id: str, shift_date, exception_type: str, severity: str, problem: str, expected, actual, variance: str, rule: str, root_cause: str, impact: str, action: str) -> None:
        nonlocal next_id
        info = emp_info(employee_id)
        anomalies.append({
            "id": next_id, "employee_id": employee_id, **info, "shift_date": str(shift_date),
            "exception_type": exception_type, "severity": severity, "status": "Open",
            "problem": problem, "expected_value": str(expected), "actual_value": str(actual), "variance": variance,
            "rule_triggered": rule, "root_cause": root_cause, "potential_impact": impact, "recommended_action": action,
        })
        next_id += 1

    # 1. Missed punches
    for _, row in attendance[attendance["missed_punch_flag"] == True].iterrows():  # noqa: E712
        add(row["employee_id"], row["work_date"], "Missed Punch", "High",
            "Employee has no matching punch for a scheduled shift.", "IN and OUT punch", "Missing punch", "N/A",
            "attendance.missed_punch_flag", "Employee forgot to badge in/out, or the device failed to record the event.",
            "Payroll and hours worked cannot be verified for this shift.", "Confirm hours with the employee/supervisor and manually correct the timecard.")

    # 2. Duplicate punches: same employee, same type, within DUPLICATE_PUNCH_MINUTES
    for (employee_id, punch_type), group in punches.sort_values("punch_timestamp").groupby(["employee_id", "punch_type"]):
        times = group["punch_timestamp"].tolist()
        for i in range(1, len(times)):
            gap_minutes = (times[i] - times[i - 1]).total_seconds() / 60
            if 0 <= gap_minutes <= DUPLICATE_PUNCH_MINUTES:
                add(employee_id, times[i].date(), "Duplicate Punch", "Medium",
                    f"Two {punch_type} punches recorded within {int(gap_minutes)} minutes of each other.",
                    "1 punch", "2 punches", f"{int(gap_minutes)} min apart",
                    f"duplicate {punch_type} punch within {DUPLICATE_PUNCH_MINUTES} min", "Device double-registered the badge, or the employee badged twice.",
                    "May inflate or confuse recorded hours if not deduplicated.", "Void the duplicate punch after confirming with the device log.")

    # 3/4. Early clock-in / late clock-out - join first IN and last OUT per employee-day to that day's schedule
    if not punches.empty and not schedule.empty:
        first_in = punches[punches["punch_type"] == "IN"].sort_values("punch_timestamp").groupby(["employee_id", "punch_date"]).first().reset_index()
        last_out = punches[punches["punch_type"] == "OUT"].sort_values("punch_timestamp").groupby(["employee_id", "punch_date"]).last().reset_index()
        # Plain dicts keyed by (employee, day). The previous version did a
        # MultiIndex `in` test plus a .loc lookup per row - two of the most
        # expensive pandas operations, ~5,000 times each.
        sched_start_by_day = {}
        sched_end_by_day = {}
        for shift in schedule.to_dict("records"):
            key = (shift["employee_id"], shift["shift_date"])
            sched_start_by_day[key] = shift["scheduled_start"]
            sched_end_by_day[key] = shift["scheduled_end"]

        for row in first_in.to_dict("records"):
            scheduled_start = sched_start_by_day.get((row["employee_id"], row["punch_date"]))
            if scheduled_start is None:
                continue
            early_minutes = (scheduled_start - row["punch_timestamp"]).total_seconds() / 60
            if early_minutes > EARLY_CLOCK_IN_MINUTES:
                add(row["employee_id"], row["punch_date"], "Early Clock-In", _severity_for(early_minutes, EARLY_CLOCK_IN_MINUTES, EARLY_CLOCK_IN_MINUTES * 3),
                    "Employee clocked in well before the scheduled start.", scheduled_start.strftime("%H:%M"), row["punch_timestamp"].strftime("%H:%M"),
                    f"-{int(early_minutes)} min", f"clock-in >{EARLY_CLOCK_IN_MINUTES} min early", "Employee arrived early, possibly for an unscheduled reason.",
                    "Unscheduled early hours may require approval or could be unpaid waiting time.", "Confirm whether early arrival was authorized; adjust schedule if it's a recurring pattern.")

        for row in last_out.to_dict("records"):
            scheduled_end = sched_end_by_day.get((row["employee_id"], row["punch_date"]))
            if scheduled_end is None:
                continue
            late_minutes = (row["punch_timestamp"] - scheduled_end).total_seconds() / 60
            if late_minutes > LATE_CLOCK_OUT_MINUTES:
                add(row["employee_id"], row["punch_date"], "Late Clock-Out", _severity_for(late_minutes, LATE_CLOCK_OUT_MINUTES, LATE_CLOCK_OUT_MINUTES * 3),
                    "Employee clocked out well after the scheduled end.", scheduled_end.strftime("%H:%M"), row["punch_timestamp"].strftime("%H:%M"),
                    f"+{int(late_minutes)} min", f"clock-out >{LATE_CLOCK_OUT_MINUTES} min late", "Workload ran over, or the employee forgot to clock out on time.",
                    "Overtime exposure.", "Review workload and staffing for this shift window; confirm the extra time was necessary.")

    # 5. Long breaks - employee-days with 3+ punches, look for OUT->IN gaps
    for (employee_id, punch_date), group in punches.sort_values("punch_timestamp").groupby(["employee_id", "punch_date"]):
        if len(group) < 3:
            continue
        rows = group.to_dict("records")
        for i in range(1, len(rows)):
            if rows[i - 1]["punch_type"] == "OUT" and rows[i]["punch_type"] == "IN":
                gap_minutes = (rows[i]["punch_timestamp"] - rows[i - 1]["punch_timestamp"]).total_seconds() / 60
                if gap_minutes > LONG_BREAK_MINUTES:
                    add(employee_id, punch_date, "Long Break", _severity_for(gap_minutes, LONG_BREAK_MINUTES, LONG_BREAK_MINUTES * 2),
                        "Gap between an OUT and the next IN punch exceeds the expected break window.",
                        f"<{LONG_BREAK_MINUTES} min", f"{int(gap_minutes)} min", f"+{int(gap_minutes - LONG_BREAK_MINUTES)} min over",
                        f"break gap >{LONG_BREAK_MINUTES} min", "Break ran longer than expected, or a punch is missing in between.",
                        "Unpaid/paid break policy may be affected; reduces effective coverage.", "Confirm break policy compliance with the employee.")

    # 6. Buddy-punch pattern - same device, EXACT same-minute punches for a
    # DIFFERENT pair of employees, recurring across multiple distinct days.
    # This dataset's shift-start times cluster heavily at shared, rounded
    # times (many coworkers share both a device and a nominal shift start),
    # so any single overlap is a structural coincidence, not a signal - only
    # the small number of pairs with the MOST recurring overlap are worth a
    # look, so this surfaces a capped top-N candidate list rather than
    # flagging every coincidental overlap (which would be most of the shift).
    pair_occurrences: dict[tuple[str, str], set] = {}
    for device_id, group in punches.sort_values("punch_timestamp").groupby("device_id"):
        times = group[["employee_id", "punch_timestamp", "punch_type"]].values.tolist()
        for i in range(len(times)):
            for j in range(i + 1, len(times)):
                emp_a, ts_a, type_a = times[i]
                emp_b, ts_b, type_b = times[j]
                gap_seconds = abs((ts_b - ts_a).total_seconds())
                if gap_seconds > 0:
                    break
                if emp_a != emp_b and type_a == type_b:
                    pair_key = tuple(sorted((emp_a, emp_b)))
                    pair_occurrences.setdefault(pair_key, set()).add(ts_a.date())
    top_pairs = sorted(pair_occurrences.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]
    for (emp_a, emp_b), days in top_pairs:
        add(emp_a, max(days), "Suspicious Punch Pattern — Review Required", "Medium",
            f"{emp_a} and {emp_b} recorded an identical-minute punch on the same device on {len(days)} separate days - one of the most recurring overlaps in this window.",
            "Independent punch times per employee", f"{emp_b} also punched at the exact same minute on {len(days)} days", f"{len(days)} occurrences",
            "same device, same punch type, same minute, ranked in the top 10 most-recurring pairs",
            "Potential buddy-punch pattern - a repeated device/timing pattern is a risk indicator, not a finding of fraud. Note: this dataset's shared shift-start times make one-off overlaps common and not meaningful on their own; only the most recurring pairs are surfaced here.",
            "Time theft risk if unverified.", "Manager review of device usage and shift presence for both employees.")

    # 7. Repeated manual edits (per-employee aggregate)
    manual_counts = punches[punches["manual_edit_flag"] == True].groupby("employee_id").size()  # noqa: E712
    for employee_id, count in manual_counts[manual_counts >= MANUAL_EDIT_REPEAT_THRESHOLD].items():
        add(employee_id, end_date, "Repeated Manual Edits", "Medium",
            f"{count} manually-edited punches in the selected window.", f"<{MANUAL_EDIT_REPEAT_THRESHOLD}", str(count), f"+{count - MANUAL_EDIT_REPEAT_THRESHOLD + 1}",
            f"manual_edit_flag count >= {MANUAL_EDIT_REPEAT_THRESHOLD}", "Device/process issues forcing frequent manual correction, or a supervisor override pattern.",
            "Reduces confidence in recorded time; worth auditing.", "Audit this employee's device and correction history with their supervisor.")

    # 8. Payroll exceptions (timecard-level)
    for _, row in timecard[timecard["exception_flag"] == True].iterrows():  # noqa: E712
        add(row["employee_id"], row["work_date"], "Potential Payroll Exception", "High",
            "Timecard flagged as an exception by the source system.", f"{row['scheduled_hours']}h scheduled", f"{row['total_worked_hours']}h worked",
            f"{round(row['total_worked_hours'] - row['scheduled_hours'], 2)}h", "fact_timecard.exception_flag",
            "Hours worked deviate from schedule in a way the source system flags for payroll review.",
            "Payroll may pay an incorrect amount if not reviewed.", "Route to payroll for manual review before the pay run closes.")

    # 9. Employees consistently exceeding scheduled hours
    timecard["excess"] = timecard["total_worked_hours"] > (timecard["scheduled_hours"] * EXCESS_HOURS_MARGIN)
    excess_counts = timecard[timecard["excess"]].groupby("employee_id").size()
    for employee_id, count in excess_counts[excess_counts >= EXCESS_HOURS_REPEAT_THRESHOLD].items():
        total_excess_hours = round(float(timecard[(timecard["employee_id"] == employee_id) & timecard["excess"]]["total_worked_hours"].sum() - timecard[(timecard["employee_id"] == employee_id) & timecard["excess"]]["scheduled_hours"].sum()), 1)
        add(employee_id, end_date, "Consistently Exceeding Scheduled Hours", "Medium",
            f"Worked hours exceeded scheduled hours by >{int((EXCESS_HOURS_MARGIN - 1) * 100)}% on {count} days in the window.",
            f"<{EXCESS_HOURS_REPEAT_THRESHOLD} days", f"{count} days", f"+{total_excess_hours}h total",
            f"total_worked_hours > scheduled_hours*{EXCESS_HOURS_MARGIN} on >= {EXCESS_HOURS_REPEAT_THRESHOLD} days",
            "Recurring understaffing on this role/shift, or scheduling not matching actual demand.",
            "Overtime cost exposure and possible burnout risk.", "Review scheduled hours for this employee's role against actual workload.")

    kpis = {
        "total_punches": int(len(punches)),
        "exception_rate_pct": round(float(punches["exception_flag"].mean() * 100), 1) if len(punches) else None,
        "missed_punches": int((attendance["missed_punch_flag"] == True).sum()) if len(attendance) else 0,  # noqa: E712
        "duplicate_punches": sum(1 for a in anomalies if a["exception_type"] == "Duplicate Punch"),
        "early_clock_ins": sum(1 for a in anomalies if a["exception_type"] == "Early Clock-In"),
        "late_clock_outs": sum(1 for a in anomalies if a["exception_type"] == "Late Clock-Out"),
        "long_breaks": sum(1 for a in anomalies if a["exception_type"] == "Long Break"),
        "manual_edits": int((punches["manual_edit_flag"] == True).sum()) if len(punches) else 0,  # noqa: E712
        "payroll_exceptions": sum(1 for a in anomalies if a["exception_type"] == "Potential Payroll Exception"),
        "excess_scheduled_hours_employees": sum(1 for a in anomalies if a["exception_type"] == "Consistently Exceeding Scheduled Hours"),
        "suspicious_punch_patterns": sum(1 for a in anomalies if "Suspicious" in a["exception_type"]),
    }

    return {
        "window": {"start": start_date, "end": end_date}, "kpis": kpis, "anomalies": anomalies,
        "grain": grain,
        "trend": _anomaly_trend(anomalies, punches, grain),
        "alerts": _alerts(anomalies),
        "watchlist": _watchlist(anomalies, start_date, end_date),
    }


def _anomaly_trend(anomalies: list[dict], punches: pd.DataFrame, grain: str) -> list[dict]:
    """Flagged issues over time. `hourly` is a time-of-day profile rather than a
    running series - "when in the day do punch problems happen" is the useful
    hourly question, and it is answerable because punches carry timestamps."""
    if grain == "hourly":
        if not len(punches):
            return []
        frame = punches.copy()
        frame["hour"] = pd.to_datetime(frame["punch_timestamp"]).dt.hour
        flagged = frame["exception_flag"].astype(bool) | frame["manual_edit_flag"].astype(bool)
        grouped = frame.assign(flagged=flagged).groupby("hour").agg(punches=("flagged", "size"), issues=("flagged", "sum"))
        return [
            {"label": f"{int(hour):02d}:00", "issues": int(row.issues), "punches": int(row.punches),
             "issue_rate_pct": round(float(row.issues) / row.punches * 100, 1) if row.punches else 0.0}
            for hour, row in grouped.iterrows()
        ]

    counts: dict[str, int] = {}
    for anomaly in anomalies:
        day = str(anomaly["shift_date"])
        key = day[:7] if grain == "monthly" else day
        counts[key] = counts.get(key, 0) + 1
    return [{"label": key, "issues": counts[key]} for key in sorted(counts)]


def _alerts(anomalies: list[dict]) -> list[dict]:
    """Things a manager should be told without having to go looking: the issue
    types that dominate, and the employees showing repeat problems."""
    alerts: list[dict] = []

    by_type: dict[str, int] = {}
    for anomaly in anomalies:
        by_type[anomaly["exception_type"]] = by_type.get(anomaly["exception_type"], 0) + 1
    total = sum(by_type.values())

    high = [a for a in anomalies if a.get("severity") == "High"]
    if high:
        alerts.append({
            "level": "High",
            "title": f"{len(high)} high-severity issue(s) need review",
            "detail": "These carry direct payroll or compliance exposure and should be cleared first.",
        })

    for exception_type, count in sorted(by_type.items(), key=lambda kv: -kv[1])[:1]:
        if total and count / total >= 0.3:
            alerts.append({
                "level": "Medium",
                "title": f"'{exception_type}' is {round(count / total * 100)}% of all flagged issues",
                "detail": "One issue type dominating usually points at a process or device problem, not individual behaviour.",
            })

    repeat: dict[str, dict] = {}
    for anomaly in anomalies:
        entry = repeat.setdefault(anomaly["employee_id"], {"name": anomaly.get("employee_name"), "count": 0})
        entry["count"] += 1
    worst = sorted(repeat.items(), key=lambda kv: -kv[1]["count"])[:3]
    for employee_id, entry in worst:
        if entry["count"] >= REPEAT_EMPLOYEE_ALERT_THRESHOLD:
            alerts.append({
                "level": "Medium",
                "title": f"{entry['name'] or employee_id} has {entry['count']} flagged punches",
                "detail": "Repeat flags on one person are usually a rota or training issue worth a conversation.",
            })
    return alerts


def _watchlist(anomalies: list[dict], start_date: str, end_date: str) -> list[dict]:
    """Who is likely to have a punch problem next - a disclosed trend rule, not
    a trained classifier: employees whose flag rate in the most recent third of
    the window is higher than in the earlier part are listed as rising."""
    if not anomalies:
        return []
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    span_days = max(1, (end - start).days)
    cutoff = end - pd.Timedelta(days=span_days / 3)
    # ISO dates sort lexicographically, so compare the strings and skip 2,000+
    # single-scalar pd.to_datetime() calls - that was the single slowest thing
    # in this endpoint.
    cutoff_str = cutoff.date().isoformat()

    per_employee: dict[str, dict] = {}
    for anomaly in anomalies:
        shift_date = str(anomaly["shift_date"])[:10]
        entry = per_employee.setdefault(anomaly["employee_id"], {
            "employee_id": anomaly["employee_id"], "employee_name": anomaly.get("employee_name"),
            "location_name": anomaly.get("location_name"), "department_name": anomaly.get("department_name"),
            "recent": 0, "earlier": 0,
        })
        if shift_date >= cutoff_str:
            entry["recent"] += 1
        else:
            entry["earlier"] += 1

    rising = []
    for entry in per_employee.values():
        # Compare rates, not raw counts - the recent window is a third the length.
        recent_rate = entry["recent"] / (span_days / 3)
        earlier_rate = entry["earlier"] / max(1e-9, span_days * 2 / 3)
        if entry["recent"] >= WATCHLIST_MIN_RECENT and recent_rate > earlier_rate:
            rising.append({
                **entry,
                "trend": "rising",
                "reason": f"{entry['recent']} flagged punch(es) recently vs {entry['earlier']} over the longer earlier period.",
            })
    rising.sort(key=lambda e: -e["recent"])
    return rising[:8]
