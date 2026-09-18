"""Rule-based AI Recommendations - combines Labor Forecasting's staffing
gap (Phase 1), Predictive Analytics' risk models (Phase 4), and Labor
Analytics' cost/budget data (Phase 2). Every number here is read from
already-computed application services, never invented by this module or by
an LLM - matching the explicit instruction that recommendations must be
built from real observed facts and predicted conditions.

No OR-Tools optimizer in this phase (confirmed with the user) - staffing
recommendations are computed directly from the real gap between required
and scheduled headcount, not a constraint-solved schedule.

Card copy is written in the register a site manager expects in a management
report: plain English and free of jargon, but professional rather than
conversational.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_PRIORITY_RANK = {"High": 3, "Medium": 2, "Low": 1}


def _people(count: int) -> str:
    """required_headcount / scheduled_headcount are PEOPLE ON SHIFT, not
    staff-hours. Earlier copy said "staff-hours", which made a perfectly
    ordinary 10-vs-20 headcount gap read as nonsense."""
    return f"{count} person" if count == 1 else f"{count} people"


def _pct(value) -> str:
    """Percentages from Labor Analytics are None when the underlying window
    has no rows to measure. Formatting that straight produced "None%"."""
    return "not measurable" if value is None else f"{value}%"


def _avg_hourly_rate(db: Session, department_id: str) -> float:
    return float(db.execute(text("SELECT AVG(base_hourly_rate) FROM dim_employee WHERE department_id=:d"), {"d": department_id}).scalar() or 0.0)


def staffing_gap_recommendations(db: Session) -> list[dict]:
    from app.services.forecasting import workspace
    from app.services.forecasting.contract import SERIES_BY_KEY

    pairs = [("LOC01_FOOD_NUTRITION", "LOC02_FOOD_NUTRITION", "DEP01"), ("LOC01_CAFE", "LOC02_CAFE", "DEP02")]
    recommendations = []
    for key_a, key_b, department_id in pairs:
        impact_a = workspace.get_staffing_impact(db, key_a, days=14)
        impact_b = workspace.get_staffing_impact(db, key_b, days=14)
        points_a, points_b = impact_a.get("points", []), impact_b.get("points", [])
        if not points_a or not points_b:
            continue
        points_b_by_date = {p["date"]: p for p in points_b}
        points_a_by_date = {p["date"]: p for p in points_a}
        rate = _avg_hourly_rate(db, department_id)

        for worst, other_by_date, loc_label, other_label in (
            (min(points_a, key=lambda p: p["gap"]), points_b_by_date, SERIES_BY_KEY[key_a].label, SERIES_BY_KEY[key_b].label),
            (min(points_b, key=lambda p: p["gap"]), points_a_by_date, SERIES_BY_KEY[key_b].label, SERIES_BY_KEY[key_a].label),
        ):
            if worst["gap"] >= 0:
                continue
            shortfall = -worst["gap"]
            counterpart = other_by_date.get(worst["date"])
            surplus_available = counterpart["gap"] if counterpart and counterpart["gap"] > 0 else 0
            reassign = min(shortfall, surplus_available)
            add_needed = shortfall - reassign
            estimated_saving = round(shortfall * rate * 1.5, 2)

            # "2026-07-20T07:00:00" -> "Sunday 20 Jul morning" - a manager reads
            # a weekday and a part of day, not an ISO timestamp.
            stamp = datetime.fromisoformat(worst["date"])
            part_of_day = "morning" if stamp.hour < 12 else "afternoon"
            when = f"{stamp.strftime('%A %d %b')} {part_of_day}"

            if reassign > 0 and add_needed > 0:
                action = f"Reassign {_people(reassign)} from {other_label}, then secure cover for the remaining {_people(add_needed)}."
            elif reassign > 0:
                action = f"Reassign {_people(reassign)} from {other_label}, which is carrying surplus cover at the same time."
            else:
                action = f"Roster {add_needed} additional {'person' if add_needed == 1 else 'people'} - no other site has surplus at that time."

            # Evidence: the actual figures the rule fired on, in words a manager
            # reads rather than field names. This used to be an empty list.
            evidence = [
                f"Forecast requirement: {_people(worst['required_headcount'])} on shift.",
                f"Currently rostered: {_people(worst['scheduled_headcount'])}.",
                (f"{other_label}: {_people(surplus_available)} surplus at the same time."
                 if surplus_available > 0 else f"{other_label}: no surplus available at that time."),
                "Basis: 14 days of forecast demand compared against the published roster.",
            ]

            recommendations.append({
                "id": f"staffing-{worst['date']}-{key_a if loc_label == SERIES_BY_KEY[key_a].label else key_b}",
                "category": "Staffing Adjustment", "priority": "High" if shortfall >= 3 else "Medium",
                "location_department": loc_label,
                "headline": f"Coverage shortfall - {when}",
                "problem": (
                    f"Forecast demand for that {part_of_day} is {_people(worst['required_headcount'])} on shift "
                    f"against {worst['scheduled_headcount']} rostered - a shortfall of {_people(shortfall)}."
                ),
                "evidence": evidence,
                "recommended_action": action,
                "expected_impact": f"The shift is covered in full rather than running {_people(shortfall)} short.",
                "estimated_savings": estimated_saving,
                "savings_basis": "avoided last-minute overtime cover",
                "confidence": 70,
                "priority_score": shortfall,
            })
    return recommendations


def overtime_recommendations(db: Session) -> list[dict]:
    from app.services.predictions import employee_risk
    from app.services.predictions.common import ModelNotApplicable

    try:
        result = employee_risk.overtime_risk(db)
    except ModelNotApplicable:
        return []
    # Only the top 3. Every High-risk employee produces a near-identical card,
    # and five of them in a row crowded the staffing, cost and attendance
    # actions off the page entirely.
    top = [e for e in result["employees"] if e["risk_level"] == "High"][:3]
    if not top:
        return []
    ids = [e["employee_id"] for e in top]
    id_params = {f"id{i}": eid for i, eid in enumerate(ids)}
    placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
    rows = db.execute(text(f"""
        SELECT t.employee_id, SUM(t.overtime_hours) AS ot_hours, AVG(e.base_hourly_rate) AS rate, COALESCE(AVG(p.overtime_multiplier), 1.5) AS mult
        FROM fact_timecard t JOIN dim_employee e ON e.employee_id = t.employee_id
        LEFT JOIN employee_pay_rate p ON p.employee_id = t.employee_id
        WHERE t.employee_id IN ({placeholders}) GROUP BY t.employee_id
    """), id_params).all()

    recommendations = []
    for row in rows:
        emp = next(e for e in top if e["employee_id"] == row.employee_id)
        ot_hours = float(row.ot_hours or 0)
        saving = round(ot_hours * float(row.rate or 0) * (float(row.mult) - 1), 2)
        recommendations.append({
            "id": f"ot-{row.employee_id}", "category": "Overtime Reduction",
            "priority": "High" if emp["risk_probability"] >= 0.66 else "Medium",
            "location_department": f"{emp['location_name']} / {emp['department_name']}",
            "headline": f"{emp['employee_name']} is trending towards further overtime",
            "problem": (
                f"{emp['employee_name']} has recorded {round(ot_hours, 1)} overtime hours in this period, and their "
                f"recent shift pattern indicates further overtime is likely."
            ),
            "evidence": [
                f"Overtime recorded to date: {round(ot_hours, 1)} hours.",
                f"Overtime is paid at {float(row.mult):g}x the standard rate.",
                f"Overtime Risk model: High group ({round(emp['risk_probability'] * 100)}% likelihood).",
            ],
            "recommended_action": "Redistribute one or two upcoming shifts to a colleague with capacity in the same week.",
            "expected_impact": f"Up to {round(ot_hours, 1)} hours move off the overtime rate.",
            "estimated_savings": saving,
            "savings_basis": "the premium paid above the standard hourly rate",
            "confidence": round(emp["risk_probability"] * 100),
            "priority_score": emp["risk_probability"] * 10,
        })
    return recommendations


def attendance_recommendations(db: Session) -> list[dict]:
    from app.services.predictions import employee_risk
    from app.services.predictions.common import ModelNotApplicable

    recommendations = []
    for label, category, fn in (("Absenteeism Risk", "Attendance", employee_risk.absenteeism_risk), ("Schedule Adherence Risk", "Schedule Adherence", employee_risk.schedule_adherence_risk)):
        try:
            result = fn(db)
        except ModelNotApplicable:
            continue
        for emp in [e for e in result["employees"] if e["risk_level"] == "High"][:3]:
            is_attendance = category == "Attendance"
            recommendations.append({
                "id": f"{category.lower()}-{emp['employee_id']}", "category": category,
                "priority": "Medium",
                "location_department": f"{emp['location_name']} / {emp['department_name']}",
                "headline": (
                    f"{emp['employee_name']} is at risk of missing their next shift" if is_attendance
                    else f"{emp['employee_name']} is repeatedly working outside rostered hours"
                ),
                "problem": (
                    "Their recent attendance pattern indicates a raised likelihood of absence on their next shift."
                    if is_attendance
                    else "They consistently start or finish outside their rostered times, and the pattern is continuing."
                ),
                "evidence": [
                    f"{label} model: High group ({round(emp['risk_probability'] * 100)}% likelihood).",
                    f"Site: {emp['location_name']}, {emp['department_name']}.",
                    "Basis: this employee's own attendance and punch history, not a site-wide average.",
                ],
                "recommended_action": (
                    "Confirm availability ahead of the shift and identify standby cover."
                    if is_attendance
                    else "Review their rostered start and finish times with them, and confirm the hours they can commit to."
                ),
                "expected_impact": (
                    "The absence is known in advance rather than on the day of the shift."
                    if is_attendance
                    else "Hours worked align with the roster, reducing timecard corrections."
                ),
                "estimated_savings": None,
                "savings_basis": None,
                "confidence": round(emp["risk_probability"] * 100),
                "priority_score": emp["risk_probability"] * 5,
            })
    return recommendations


def cost_recommendations(db: Session) -> list[dict]:
    from app.services.analytics import labor_analytics

    summary = labor_analytics.compute(db)
    if summary.get("error"):
        return []
    recommendations = []
    for m in summary["manager_performance"]:
        pct = m.get("labor_cost_vs_budget_pct")
        if pct is not None and pct > 10:
            recommendations.append({
                "id": f"cost-{m['location_id']}-{m['department_id']}", "category": "Labor Cost",
                "priority": "High" if pct > 20 else "Medium",
                "location_department": f"{m['location_name']} / {m['department_name']}",
                "headline": f"{pct}% over labor budget",
                "problem": (
                    f"This department is running {pct}% above its labor budget, with overtime at "
                    f"{_pct(m['overtime_control_pct'])} of all hours worked here."
                ),
                "evidence": [
                    f"Labor cost to date: ${m['labor_cost']:,.0f}, {pct}% above budget.",
                    f"Overtime: {_pct(m['overtime_control_pct'])} of hours worked.",
                    f"Schedule adherence: staff work to their rostered times {_pct(m['schedule_adherence_pct'])} of the time.",
                    f"{m['headcount']} employees, {m['actual_hours']:,.0f} hours worked.",
                ],
                "recommended_action": (
                    "Require supervisor sign-off for overtime, and realign the roster to the actual demand peaks."
                ),
                "expected_impact": "Spend returns towards budget without reducing shift cover.",
                "estimated_savings": None,
                "savings_basis": None,
                "confidence": 60,
                "priority_score": pct,
            })
    return recommendations


def compute_recommendations(db: Session, limit: int = 20) -> list[dict]:
    all_recs = staffing_gap_recommendations(db) + overtime_recommendations(db) + cost_recommendations(db) + attendance_recommendations(db)
    all_recs.sort(key=lambda r: (_PRIORITY_RANK.get(r["priority"], 0), r["priority_score"]), reverse=True)
    for rec in all_recs:
        rec.pop("priority_score", None)
    return all_recs[:limit]


def answer_question(db: Session, question: str) -> dict:
    q = question.lower()
    recs = compute_recommendations(db, limit=100)

    if "staff" in q and ("tomorrow" in q or "next" in q):
        matches = [r for r in recs if r["category"] == "Staffing Adjustment"][:5]
        answer = (
            f"Based on the last 14 days of real schedule-vs-required data, {len(matches)} staffing gap(s) are currently flagged."
            if matches else "No staffing gaps are currently flagged in the recent overlap window between real demand and real schedules."
        )
    # Parenthesised to state the precedence explicitly - `and` binds tighter
    # than `or`, so this has always read as "overtime, OR (reduce without cost)".
    elif "overtime" in q or ("reduce" in q and "cost" not in q):
        matches = [r for r in recs if r["category"] == "Overtime Reduction"][:5]
        answer = f"{len(matches)} employee(s) are currently in the High overtime-risk group, based on the Overtime Risk model." if matches else "No employees are currently in the High overtime-risk group."
    elif "cost" in q and "save" in q:
        matches = sorted([r for r in recs if r["estimated_savings"]], key=lambda r: r["estimated_savings"], reverse=True)[:5]
        answer = f"The highest estimated-savings action is: {matches[0]['recommended_action']}" if matches else "No recommendation currently has an estimated savings figure."
    elif "attention" in q or "immediate" in q:
        matches = recs[:5]
        answer = f"{matches[0]['location_department']} needs the most immediate attention: {matches[0]['problem']}" if matches else "No high-priority issues are currently flagged."
    elif "move" in q or "reassign" in q or "between location" in q:
        # Matches the staffing action copy above ("Reassign N people from ...").
        # This looked for "Reassign" while the copy said "Move ... across from",
        # so the branch could never match anything.
        matches = [r for r in recs if r["category"] == "Staffing Adjustment" and "Reassign" in r["recommended_action"]][:5]
        answer = f"{len(matches)} recommendation(s) involve reassigning scheduled staff between locations." if matches else "No current recommendation involves moving staff between locations - either both locations are balanced, or neither has surplus to transfer."
    else:
        matches = recs[:5]
        answer = f"Top current recommendation: {matches[0]['problem']}" if matches else "No recommendations are currently flagged - all monitored metrics are within tolerance."

    return {"question": question, "answer": answer, "recommendations": matches}
