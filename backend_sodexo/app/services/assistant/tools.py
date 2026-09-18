"""Structured-fact retrieval for the AI Assistant - each tool calls into an
already-built application service and returns a small, curated dict, never
the raw database. The LLM (or template fallback) only ever sees these
facts, never raw rows - matching the instruction to use tool/service
retrieval rather than putting all data into the prompt.

The dicts are deliberately a little richer than the headline number: a
question like "who is most likely to leave?" or "which site is worst on
cost?" can only be answered accurately if the named rows behind the KPI are
in the facts, not just the count.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelPrediction, ModelRun, TrainingJob


def _latest_job(db: Session) -> TrainingJob | None:
    return db.execute(select(TrainingJob).order_by(TrainingJob.started_at.desc())).scalars().first()


def _daily_forecast_for_run(db: Session, run: ModelRun) -> list[dict]:
    """Each of the real future days individually, not averaged - this is the
    actual model output, valid out to the 7-day horizon the model was
    trained and validated at."""
    predictions = db.execute(
        select(ModelPrediction).where(ModelPrediction.model_run_id == run.id, ModelPrediction.is_future.is_(True))
    ).scalars().all()
    by_date: dict[str, list[float]] = {}
    for p in predictions:
        by_date.setdefault(p.business_date.isoformat(), []).append(p.predicted)
    return [{"date": d, "forecast": round(sum(v) / len(v), 2)} for d, v in sorted(by_date.items())]


def _project_from_history(db: Session, run: ModelRun, slots: int, pct_change: float | None) -> float | None:
    """A historical-average projection for a horizon the model was never
    validated at (monthly/quarterly) - the average of the most recent
    `slots` real ACTUAL half-day-block values, nudged by the model's own
    validated 7-day trend direction.

    Deliberately sourced from ModelPrediction (the same table
    `_trend_for_run` reads) rather than workspace.get_eda()'s daily_trend -
    the two are NOT on the same scale (eda averages raw hourly rows across a
    full calendar day including closed hours; ModelPrediction stores the
    half-day-block figure the models actually forecast), and mixing them
    produced a ~7x-too-low number. Same source, same scale as the weekly
    figure, just averaged over a longer real window.
    """
    predictions = db.execute(
        select(ModelPrediction)
        .where(ModelPrediction.model_run_id == run.id, ModelPrediction.is_future.is_(False), ModelPrediction.actual.is_not(None))
        .order_by(ModelPrediction.business_date.desc(), ModelPrediction.hour_of_day.desc())
        .limit(slots)
    ).scalars().all()
    if not predictions:
        return None
    avg = sum(p.actual for p in predictions) / len(predictions)
    return round(avg * (1 + (pct_change or 0) / 100), 2)


_HORIZON_WORDS = {
    "quarterly": {"quarter", "quarterly", "quarters"},
    "monthly": {"month", "months", "monthly"},
    "daily": {"day", "days", "daily", "tomorrow"},
    "weekly": {"week", "weeks", "weekly"},
}


def detect_forecast_horizon(question: str) -> str:
    """Which of daily/weekly/monthly/quarterly the question is asking for -
    defaults to weekly, the model's own native, fully-validated horizon."""
    words = set(_words(question))
    for horizon, terms in _HORIZON_WORDS.items():
        if words & terms:
            return horizon
    return "weekly"


def _trend_for_run(db: Session, run: ModelRun) -> dict | None:
    """Recent-actual vs. future-forecast average, computed from the same
    persisted ModelPrediction rows the Forecast Explorer chart uses - a real
    number for trend/graph-shaped questions, not a description."""
    predictions = db.execute(select(ModelPrediction).where(ModelPrediction.model_run_id == run.id)).scalars().all()
    recent_actual = [p.actual for p in predictions if not p.is_future and p.actual is not None][-24:]
    future = [p.predicted for p in predictions if p.is_future]
    if not recent_actual or not future:
        return None
    avg_recent = sum(recent_actual) / len(recent_actual)
    avg_future = sum(future) / len(future)
    if not avg_recent:
        return None
    pct_change = round((avg_future - avg_recent) / avg_recent * 100, 1)
    direction = "flat" if abs(pct_change) < 5 else ("rising" if pct_change > 0 else "falling")
    return {"avg_recent_actual": round(avg_recent, 2), "avg_forecast_next_7d": round(avg_future, 2), "pct_change": pct_change, "direction": direction}


# Department and location are matched separately so "the cafe at Community
# Hospital" narrows to exactly one series instead of matching all three
# series that mention either word.
_DEPARTMENT_ALIASES = {
    "CAFE": ("cafe", "cafes", "café", "retail", "coffee", "kiosk"),
    "FOOD_NUTRITION": ("food", "nutrition", "kitchen", "catering", "food & nutrition", "food and nutrition"),
}
_LOCATION_ALIASES = {
    "LOC01": ("community hospital", "community", "loc01", "loc 01", "location 1", "site 1"),
    "LOC02": ("regional medical", "regional", "loc02", "loc 02", "location 2", "site 2"),
}


def _alias_hits(question: str, aliases: dict[str, tuple[str, ...]]) -> set[str]:
    q = question.lower()
    words = set(_words(q))
    hit = set()
    for code, terms in aliases.items():
        for term in terms:
            if (term in q) if " " in term else (term in words):
                hit.add(code)
                break
    return hit


# dim_department: DEP01 = Food & Nutrition Services, DEP02 = Retail & Cafe Services.
_DEPARTMENT_IDS = {"FOOD_NUTRITION": "DEP01", "CAFE": "DEP02"}


def _scope_employees(question: str, rows: list[dict]) -> list[dict]:
    """Narrow employee-level rows to the site/department the question names.

    Without this, a "who is highest risk at the cafe?" question was answered
    from the global top-N, so the right employee was only ever included by
    luck. Falls back to the unfiltered rows when the filter empties the list,
    so a scoped question still gets an answer rather than silence.
    """
    locations = _alias_hits(question, _LOCATION_ALIASES)
    departments = {_DEPARTMENT_IDS[d] for d in _alias_hits(question, _DEPARTMENT_ALIASES) if d in _DEPARTMENT_IDS}
    if not locations and not departments:
        return rows
    scoped = [
        r for r in rows
        if (not locations or r.get("location_id") in locations)
        and (not departments or r.get("department_id") in departments)
    ]
    return scoped or rows


_SCOPE_NAMES = {
    "LOC01": "Community Hospital", "LOC02": "Regional Medical Center",
    "CAFE": "Retail & Cafe Services", "FOOD_NUTRITION": "Food & Nutrition Services",
}


def _scope_label(question: str) -> str:
    """What the question narrowed to, in words. The site-level payloads carry
    every department so a ranking question can be answered, which means the
    answer needs telling which row the user actually asked about - otherwise a
    scoped question gets answered with the company-wide total."""
    codes = sorted(_alias_hits(question, _LOCATION_ALIASES)) + sorted(_alias_hits(question, _DEPARTMENT_ALIASES))
    if not codes:
        return "all sites and departments"
    return ", ".join(_SCOPE_NAMES[c] for c in codes)


def _mentioned_series(question: str, series_list) -> list:
    """If the question names a specific location and/or department (e.g. "the
    cafe", "community hospital", "the cafe at Regional Medical"), scope facts
    to just those series instead of dumping all 4 - keeps answers on-topic and
    short. Falls back to every series when nothing specific is named."""
    departments = _alias_hits(question, _DEPARTMENT_ALIASES)
    locations = _alias_hits(question, _LOCATION_ALIASES)
    if not departments and not locations:
        return list(series_list)
    matched = [
        s for s in series_list
        if (not departments or any(d in s.key for d in departments))
        and (not locations or s.location_id in locations)
    ]
    return matched or list(series_list)


def forecast_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    job = _latest_job(db)
    if not job:
        return {"status": "No training run yet - Labor Forecasting has not been trained."}

    from app.services.forecasting import workspace
    from app.services.forecasting.contract import SERIES

    scoped_series = _mentioned_series(question, SERIES)
    scoped_keys = {s.key for s in scoped_series}
    champions = db.execute(
        select(ModelRun).where(ModelRun.job_id == job.job_id, ModelRun.is_champion.is_(True), ModelRun.series_key.in_(scoped_keys))
    ).scalars().all()

    staffing = []
    for series in scoped_series:
        # A chart needs more than the latest point to draw a line.
        impact = workspace.get_staffing_impact(db, series.key, days=14 if visual else 1)
        if impact.get("points"):
            staffing.append({"series": series.label, **impact["points"][-1]})

    # Hour-of-day/day-of-week/seasonal pattern, folded in from the Data
    # Analysis tab so "why does demand peak at 7am?"-style questions are
    # answerable without a dedicated topic for it.
    demand_pattern = []
    eda_by_key: dict[str, dict] = {}
    chart = None
    for series in scoped_series:
        try:
            eda = workspace.get_eda(db, series.key)
        except Exception:
            continue
        eda_by_key[series.key] = eda
        demand_pattern.append({"series": series.label, "holiday_lift_pct": eda.get("holiday_lift_pct")})
        if visual and chart is None and eda.get("daily_trend"):
            chart = apply_chart_type({
                "type": "line", "title": f"{series.label} demand trend", "x_key": "date",
                "series": [{"key": "actual", "label": "Actual"}, {"key": "trend_7d", "label": "7-day trend"}],
                "data": eda["daily_trend"],
            }, chart_type)

    horizon = detect_forecast_horizon(question)
    label_by_key = {s.key: s.label for s in SERIES}

    def _horizon_forecast(c: ModelRun) -> dict:
        trend = _trend_for_run(db, c)
        if horizon == "daily":
            return {"horizon": "daily", "basis": "validated model forecast",
                    "days": _daily_forecast_for_run(db, c)}
        if horizon == "weekly":
            return {"horizon": "weekly", "basis": "validated model forecast",
                    "avg_forecast": trend["avg_forecast_next_7d"] if trend else None}
        # monthly/quarterly: the model is only validated to 7 days ahead: this
        # is a historical-average projection, not a trained/validated
        # forecast, and must be presented as such.
        from app.services.forecasting.contract import SLOTS_PER_DAY

        window_days = 30 if horizon == "monthly" else 90
        pct_change = trend["pct_change"] if trend else None
        projected = _project_from_history(db, c, window_days * SLOTS_PER_DAY, pct_change)
        return {
            "horizon": horizon, "basis": "historical seasonal projection - NOT a validated forecast",
            "projected_avg": projected,
            "caveat": f"The forecasting models are only trained and validated 7 days ahead. This {horizon} figure is "
                      f"a projection from the last {window_days} days of real actual demand, adjusted by the "
                      f"model's own validated 7-day trend direction - it is not itself a validated prediction.",
        }

    facts = {
        # Tells the model whether it is looking at one named site or all of
        # them, so it does not describe a scoped answer as company-wide.
        "scope": [s.label for s in scoped_series] if len(scoped_series) < len(SERIES) else "all sites and departments",
        "requested_horizon": horizon,
        "champions": [
            {
                "series": label_by_key.get(c.series_key, c.series_key), "model": c.model_label,
                "mape": c.mape, "accuracy": c.accuracy, "trend": _trend_for_run(db, c),
                "forecast_at_requested_horizon": _horizon_forecast(c),
            }
            for c in champions
        ],
        "latest_staffing_gap": staffing,
        "demand_pattern": demand_pattern,
    }
    if chart:
        facts["chart"] = chart
    return facts


def labor_cost_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    from app.services.analytics import labor_analytics

    summary = labor_analytics.compute(db)
    if summary.get("error"):
        return {"status": summary["error"]}
    # The full per-department rows, ranked worst-to-best on budget variance.
    # Without these, a "which department is worst on cost?" question had only
    # the company-wide KPI total to work from and could not be answered.
    departments = sorted(
        (
            {
                "location": m["location_name"], "department": m["department_name"],
                "labor_cost": m["labor_cost"], "vs_budget_pct": m["labor_cost_vs_budget_pct"],
                "overtime_pct_of_hours": m["overtime_control_pct"],
                "schedule_adherence_pct": m["schedule_adherence_pct"],
                "headcount": m["headcount"], "hours_worked": m["actual_hours"],
                "scorecard_rank": m["rank"],
            }
            for m in summary["manager_performance"]
        ),
        key=lambda d: d["vs_budget_pct"] if d["vs_budget_pct"] is not None else -999,
        reverse=True,
    )
    facts = {
        "scope": _scope_label(question),
        "window": summary.get("window"),
        "kpis": summary["kpis"],
        "departments_ranked_by_budget_variance": departments,
        "over_budget_departments": [d for d in departments if (d["vs_budget_pct"] or 0) > 10],
    }
    if visual and summary["labor_cost"].get("trend"):
        facts["chart"] = apply_chart_type({
            "type": "line", "title": "Labor cost trend", "x_key": "date",
            "series": [{"key": "cost", "label": "Cost"}], "data": summary["labor_cost"]["trend"],
        }, chart_type)
    return facts


def punch_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    from app.services.punch_analytics import rules

    result = rules.detect_anomalies(db)
    if result.get("error"):
        return {"status": result["error"]}
    anomalies = result.get("anomalies") or []
    # Which exception types actually drive the exception rate, plus the named
    # repeat offenders - a "who keeps missing punches?" question needs rows,
    # not just the KPI count.
    by_type: dict[str, int] = {}
    by_employee: dict[str, dict] = {}
    for a in anomalies:
        by_type[a["exception_type"]] = by_type.get(a["exception_type"], 0) + 1
        entry = by_employee.setdefault(a["employee_id"], {
            "employee": a["employee_name"], "location": a["location_name"], "department": a["department_name"],
            "location_id": a["location_id"], "department_id": a["department_id"], "exceptions": 0,
        })
        entry["exceptions"] += 1
    flagged = _scope_employees(question, sorted(by_employee.values(), key=lambda e: -e["exceptions"]))
    exceptions_by_type = dict(sorted(by_type.items(), key=lambda kv: -kv[1]))
    facts = {
        "scope": _scope_label(question),
        "window": result.get("window"),
        "kpis": result["kpis"],
        "total_exceptions": len(anomalies),
        "exceptions_by_type": exceptions_by_type,
        # The ids exist only to drive the scoping above; the names carry the
        # same information for a reader.
        "most_flagged_employees": [{k: v for k, v in e.items() if not k.endswith("_id")} for e in flagged[:5]],
    }
    if visual and exceptions_by_type:
        facts["chart"] = apply_chart_type({
            "type": "bar", "title": "Punch issues by type", "x_key": "type",
            "series": [{"key": "count", "label": "Issues"}],
            "data": [{"type": k, "count": v} for k, v in exceptions_by_type.items()],
        }, chart_type)
    return facts


def risk_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    from app.services.predictions import engine as predictions_engine

    result = predictions_engine.compute_all(db)
    summary = []
    chart = None
    for m in result["employee_models"]:
        if m.get("status") == "completed":
            high = [e for e in m["employees"] if e["risk_level"] == "High"]
            scoped = _scope_employees(question, m["employees"])
            highest = [
                {
                    "employee": e["employee_name"], "location": e["location_name"],
                    "department": e["department_name"], "risk_pct": round(e["risk_probability"] * 100),
                    "risk_level": e["risk_level"],
                }
                for e in scoped[:5]
            ]
            summary.append({
                "model": m["model_label"],
                "high_risk_employee_count": len(high),
                "roc_auc": m["metrics"]["roc_auc"],
                # Named employees, so "who is most at risk?" is answerable from
                # the facts instead of only "how many". Already sorted by
                # descending risk by the prediction service.
                "highest_risk_employees": highest,
                "top_drivers": m.get("top_drivers"),
            })
            if visual and chart is None and highest:
                chart = apply_chart_type({
                    "type": "bar", "title": f"{m['model_label']} - highest risk employees", "x_key": "employee",
                    "series": [{"key": "risk_pct", "label": "Risk %"}],
                    "data": [{"employee": e["employee"], "risk_pct": e["risk_pct"]} for e in highest],
                }, chart_type)
        else:
            summary.append({"model": m["model_id"], "status": "not_applicable", "reason": m.get("reason")})
    facts = {"risk_models": summary, "turnover_risk": result["turnover_risk"]["reason"]}
    if chart:
        facts["chart"] = chart
    return facts


def recommendation_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    from app.services.recommendations import engine as recommendations_engine

    recs = recommendations_engine.compute_recommendations(db, limit=8)
    facts = {
        "total_open_actions": len(recs),
        "high_priority_count": sum(1 for r in recs if r["priority"] == "High"),
        # `id` is a routing key for the UI and means nothing to a reader, so it
        # is dropped rather than spent as prompt text.
        "top_recommendations": [{k: v for k, v in r.items() if k != "id"} for r in recs],
    }
    if visual and recs:
        by_priority: dict[str, int] = {}
        for r in recs:
            by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
        facts["chart"] = apply_chart_type({
            "type": "donut", "title": "Recommendations by priority",
            "series": [{"key": "value", "label": "Count"}],
            "data": [{"name": k, "value": v} for k, v in by_priority.items()],
        }, chart_type)
    return facts


def model_performance_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    forecast = forecast_facts(db, question, visual=False)
    from app.services.predictions import engine as predictions_engine

    predictive = predictions_engine.compute_all(db)
    classifier_metrics = [{"model": m["model_label"], "metrics": m.get("metrics"), "top_drivers": m.get("top_drivers")} for m in predictive["employee_models"] if m.get("status") == "completed"]
    champions = forecast.get("champions") or []
    facts = {"forecast_champions": champions, "predictive_models": classifier_metrics}
    if visual and champions:
        facts["chart"] = apply_chart_type({
            "type": "bar", "title": "Champion MAPE by series", "x_key": "series",
            "series": [{"key": "mape", "label": "MAPE"}],
            "data": [{"series": c["series"], "mape": c["mape"]} for c in champions if c.get("mape") is not None],
        }, chart_type)
    return facts


def data_quality_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    from app.services.data_quality import summary as data_quality_summary

    result = data_quality_summary.get_summary(db)
    worst = sorted(result["sources"], key=lambda r: r["records_rejected"], reverse=True)[:5]
    facts = {
        "overall_status": result["summary"]["overall_quality_status"],
        "totals": result["summary"],
        "worst_sources": [
            {"source": s["dataset_label"], "received": s["records_received"], "loaded": s["records_loaded"],
             "rejected": s["records_rejected"], "quality_status": s["quality_status"], "freshness_hours": s["freshness_hours"]}
            for s in worst
        ],
    }
    if visual and worst:
        facts["chart"] = apply_chart_type({
            "type": "bar", "title": "Records rejected by source", "x_key": "source",
            "series": [{"key": "rejected", "label": "Rejected"}],
            "data": [{"source": s["source"], "rejected": s["rejected"]} for s in facts["worst_sources"]],
        }, chart_type)
    return facts


def drift_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    from app.services.forecasting import workspace
    from app.services.forecasting.contract import SERIES

    results = []
    chart = None
    for series in _mentioned_series(question, SERIES):
        try:
            d = workspace.get_drift(db, series.key, None)
        except LookupError:
            continue
        results.append({
            "series": series.label, "status": d["status"],
            "pct_change": d["feature_distribution"]["pct_change"],
            "signal_strength": d["demand_signal_strength"],
        })
        if visual and chart is None and d.get("demand_signal_trend"):
            chart = apply_chart_type({
                "type": "line", "title": f"{series.label} demand signal drift", "x_key": "date",
                "series": [{"key": "value", "label": d["demand_signal_name"]}],
                "data": d["demand_signal_trend"],
            }, chart_type)
    if not results:
        return {"status": "No completed model to check drift for yet - train the forecasting models first."}
    facts = {"scope": _scope_label(question), "series_drift": results}
    if chart:
        facts["chart"] = chart
    return facts


TOOLS = {
    "forecast": forecast_facts,
    "labor_cost": labor_cost_facts,
    "punch": punch_facts,
    "risk": risk_facts,
    "recommendations": recommendation_facts,
    "model_performance": model_performance_facts,
    "data_quality": data_quality_facts,
    "drift": drift_facts,
}

# Shown to the LLM router so it can pick tools by meaning rather than by
# keyword overlap. Keep in step with TOOLS.
TOOL_DESCRIPTIONS = {
    "forecast": "labor demand forecast, forecast trend and direction, required vs scheduled headcount, staffing gaps, under/overstaffed shifts, demand patterns by hour or day",
    "labor_cost": "labor cost, spend against budget, per-department budget variance, overtime hours and cost, hours worked, headcount",
    "punch": "clock-in/clock-out punches, missed punches, late arrivals, buddy punching, timecard exceptions, attendance anomalies",
    "risk": "per-employee predictive risk scores and who is highest risk: overtime risk, absenteeism risk, schedule adherence risk, payroll anomaly risk, turnover",
    "recommendations": "the ranked list of recommended actions to take now, what to prioritise, what needs immediate attention, estimated savings",
    "model_performance": "how accurate or trustworthy the models are: champion model per series, backtest accuracy, MAPE, ROC-AUC, feature drivers",
    "data_quality": "data ingestion health, which source systems loaded cleanly, records received/loaded/rejected, freshness of the data",
    "drift": "whether demand has shifted enough that a forecast model can no longer be trusted, how strongly the demand signal tracks staffing",
}


# Word sets, not substrings. The previous version used
# `q.startswith(keyword)`, so "highest cost department" started with "hi" and
# was answered as a greeting, and anything starting with "help" was too.
_GREETING_WORDS = {
    "hi", "hii", "hiya", "hey", "heya", "hello", "helo", "yo", "howdy", "sup",
    "good", "morning", "afternoon", "evening", "there", "thanks", "thank", "you",
    "ok", "okay", "cool", "nice", "please",
}
_CAPABILITY_PHRASES = (
    "what can you do", "who are you", "what do you do", "how do you work",
    "what can i ask", "what should i ask", "what are you",
)

# Each topic scores by how many of its terms the question hits; the best
# topics win. One shared keyword no longer drags in a whole extra topic, and a
# question that hits nothing is declined rather than answered with whatever
# matched first.
_TOPIC_TERMS: dict[str, tuple[str, ...]] = {
    "labor_cost": (
        "cost", "costs", "budget", "budgets", "spend", "spending", "expense", "expenses",
        "payroll", "overtime", "over time", "ot hours", "utilisation", "utilization",
        "cost per hour", "under budget", "over budget", "money", "dollars",
    ),
    "forecast": (
        "forecast", "forecasts", "forecasting", "demand", "staffing gap", "staff gap",
        "required", "understaffed", "overstaffed", "short of staff", "shortfall",
        "trend", "trending", "graph", "chart", "pattern", "rising", "falling",
        "increase", "increasing", "decrease", "decreasing",
        "how many staff", "headcount", "roster", "rota", "schedule", "scheduled",
        "staff", "staffing", "cover", "shift", "shifts",
        # Deliberately NOT "tomorrow" / "next week" on their own - "what's the
        # weather tomorrow" was matching forecast on the date word alone.
    ),
    "punch": (
        "punch", "punches", "punching", "attendance", "late", "lateness", "missed",
        "anomaly", "anomalies", "buddy", "clock in", "clock out", "clocking",
        "exception", "exceptions", "absent", "absence", "absenteeism", "timecard",
    ),
    "risk": ("risk", "risks", "risky", "at risk", "turnover", "attrition", "likely to", "predict", "prediction"),
    "recommendations": (
        "recommend", "recommends", "recommended", "recommendation", "recommendations",
        "action", "actions", "should i", "what should", "how should", "how do i",
        "reduce", "cut", "improve", "fix", "advice", "advise", "suggest", "suggestion",
        "how can", "priority", "priorities", "prioritise", "prioritize", "next step",
        "needs attention", "immediate attention",
    ),
    "model_performance": (
        "model", "models", "accuracy", "accurate", "mape", "champion", "backtest",
        "how reliable", "can i trust", "trustworthy", "model performance",
    ),
    "data_quality": (
        "data quality", "ingestion", "ingest", "sync", "source system", "sources",
        "records received", "records loaded", "records rejected", "rejected", "freshness",
        "stale", "up to date", "data health",
    ),
    "drift": (
        "drift", "drifted", "drifting", "still trust", "still accurate", "out of date",
        "signal strength", "correlation", "correlates", "still valid", "retrain", "retraining",
    ),
}
MAX_TOPICS = 3

# A whole-picture question deserves the whole picture, not the two topics that
# happened to share a word with it.
_OVERVIEW_PHRASES = (
    "overview", "summary", "summarise", "summarize", "everything", "overall",
    "how are we doing", "state of play", "full picture", "brief me", "walk me through",
)
_OVERVIEW_TOPICS = ["recommendations", "forecast", "labor_cost"]


def _words(text: str) -> list[str]:
    return re.findall("[a-z0-9']+", text.lower())


def _term_hits(question: str, terms: tuple[str, ...]) -> int:
    """Whole-word for single terms, plain containment for phrases.

    A multi-word phrase counts double: matching "over budget" is far stronger
    evidence of intent than matching the bare word "budget", and without the
    weighting a topic with many single words always outranked a precise
    phrase match.
    """
    q = question.lower()
    words = set(_words(q))
    hits = 0
    for term in terms:
        if " " in term:
            if term in q:
                hits += 2
        elif term in words:
            hits += 1
    return hits


def select_tools(question: str) -> list[str]:
    """No fallback to a default topic - if nothing matches, the question is
    genuinely out of scope for this app and service.py declines rather than
    answering with unrelated facts."""
    q = " ".join(_words(question))
    if any(phrase in q for phrase in _OVERVIEW_PHRASES):
        return list(_OVERVIEW_TOPICS)
    scored = [(topic, _term_hits(question, terms)) for topic, terms in _TOPIC_TERMS.items()]
    ranked = sorted((t for t in scored if t[1] > 0), key=lambda t: -t[1])
    if not ranked:
        return []
    # Only keep the topics that are genuinely competitive with the best match,
    # so a single incidental keyword no longer pulls in a third topic.
    best = ranked[0][1]
    return [topic for topic, hits in ranked[:MAX_TOPICS] if hits * 2 >= best]


def is_greeting(question: str) -> bool:
    """True only for a message that is *entirely* pleasantries, or a genuine
    "what can you do" - never for a real question that merely opens with one."""
    words = _words(question)
    if not words:
        return False
    if all(word in _GREETING_WORDS for word in words):
        return True
    q = " ".join(words)
    return any(phrase in q for phrase in _CAPABILITY_PHRASES) and not select_tools(question)


FOLLOW_UP_MAX_WORDS = 6

# Words that point back at something already said. A question containing one
# of these cannot be scoped on its own words alone.
_REFERENTIAL_WORDS = {"there", "that", "it", "its", "them", "they", "their", "those", "same"}


def is_follow_up(question: str) -> bool:
    """A short message that carries no subject of its own ("why?", "and the
    food service?") only makes sense against the previous turn, so its topic
    has to be inherited rather than parsed out of these few words.

    Deliberately keyed on "has no topic of its own" rather than on the opening
    word: "Which department is over budget?" and "What is the demand trend?"
    both open with a question word but stand perfectly well alone, and
    treating them as follow-ups dragged the previous site into a question that
    was asking about all of them.
    """
    words = _words(question)
    if not words or len(words) > FOLLOW_UP_MAX_WORDS:
        return False
    return not select_tools(question)


_EMPTY_SCOPE = {"location_id": None, "location_name": None, "department_id": None, "department_name": None}


def detect_scope(question: str) -> dict:
    """What site/department the question itself names, explicitly.

    `None` for a dimension the question doesn't name, or names ambiguously
    (both locations, both departments at once) - ambiguous means "not
    stated", never an arbitrary pick. Uses the alias-internal codes
    (LOC01/LOC02/CAFE/FOOD_NUTRITION), the same code space _SCOPE_NAMES and
    _alias_hits already speak - not dim_department's DEP01/DEP02.
    """
    locs = _alias_hits(question, _LOCATION_ALIASES)
    deps = _alias_hits(question, _DEPARTMENT_ALIASES)
    loc = next(iter(locs)) if len(locs) == 1 else None
    dep = next(iter(deps)) if len(deps) == 1 else None
    return {
        "location_id": loc, "location_name": _SCOPE_NAMES.get(loc),
        "department_id": dep, "department_name": _SCOPE_NAMES.get(dep),
    }


def merge_scope(question: str, current_scope: dict | None, history: list[dict] | None = None) -> dict:
    """The scope to carry into this turn's answer, and to hand back to the
    frontend to round-trip on the next one.

    A site/department named in the current question always wins for that
    dimension. Otherwise, the previous turn's value for that dimension
    carries forward only when this question reads as a follow-up ("why?",
    "what about there?") - a self-contained new question ("which department
    is over budget?") resets to company-wide instead of dragging a stale
    scope from several turns ago, which is the failure mode the old
    text-concatenation approach had past one hop.
    """
    explicit = detect_scope(question)
    current_scope = current_scope or {}
    carry = is_follow_up(question) or bool(set(_words(question)) & _REFERENTIAL_WORDS)
    merged = {}
    for dim in ("location", "department"):
        id_key, name_key = f"{dim}_id", f"{dim}_name"
        if explicit[id_key]:
            merged[id_key], merged[name_key] = explicit[id_key], explicit[name_key]
        elif carry and current_scope.get(id_key):
            merged[id_key], merged[name_key] = current_scope[id_key], current_scope.get(name_key)
        else:
            merged[id_key], merged[name_key] = None, None
    return merged


def scoping_text_from_scope(question: str, scope: dict | None) -> str:
    """The text the fact tools read for site/department scoping - the
    persisted scope's names, prepended ahead of the question, so the
    existing _alias_hits-based helpers inside each tool pick them up
    unchanged. An explicit mention in `question` itself still wins, because
    merge_scope already gave it priority when building `scope`."""
    if not scope:
        return question
    prefix = " ".join(filter(None, [scope.get("location_name"), scope.get("department_name")]))
    return f"{prefix} {question}".strip() if prefix else question


_VISUAL_WORDS = {"chart", "graph", "plot", "visual", "picture"}
_VISUAL_PHRASES = ("trend line", "show me", "over time")


_CHART_TYPE_PHRASES: dict[str, tuple[str, ...]] = {
    "bar": ("bar graph", "bar chart", "bar plot", "column chart", "columns"),
    "line": ("line graph", "line chart", "line plot", "trend line"),
    "donut": ("donut", "doughnut", "pie chart", "pie graph"),
}

# "piechart", "bargraph", "linechart" etc. are typed as one word as often as
# two - split them apart before matching so the phrase lists above don't need
# a duplicate compound-word entry for every combination.
_COMPOUND_CHART_WORD = re.compile(r"\b(pie|bar|line|donut|doughnut|column)(chart|graph|plot)s?\b")


def _normalize_chart_words(question: str) -> str:
    return _COMPOUND_CHART_WORD.sub(r"\1 \2", question.lower())


def wants_chart_type(question: str) -> str | None:
    """Which chart type the user explicitly asked for, if any - "give me a
    bar graph" should draw a bar chart even for a topic whose own default is
    a line. `None` means let each tool use its own default shape."""
    q = " ".join(_words(_normalize_chart_words(question)))
    for chart_type, phrases in _CHART_TYPE_PHRASES.items():
        if any(phrase in q for phrase in phrases):
            return chart_type
    return None


def apply_chart_type(chart: dict | None, chart_type: str | None) -> dict | None:
    """Honour an explicitly requested chart type, reshaping data where the
    two shapes differ - line/bar share one {x_key, series[].key} shape and
    only need the label swapped, but donut needs {name, value} pairs, so
    switching to or from it means actually reshaping the rows, not just
    relabelling them."""
    if not chart or not chart_type or chart_type == chart["type"]:
        return chart
    if chart_type in ("line", "bar") and chart["type"] in ("line", "bar"):
        return {**chart, "type": chart_type}
    if chart_type == "donut":
        key, label = chart["series"][0]["key"], chart["series"][0]["label"]
        x_key = chart.get("x_key", "name")
        return {
            "type": "donut", "title": chart["title"],
            "series": [{"key": "value", "label": label}],
            "data": [{"name": row.get(x_key), "value": row.get(key)} for row in chart["data"]],
        }
    # donut -> line/bar: the donut's own {name, value} rows already fit the
    # {x_key, series[].key} shape line/bar expect - just name the axis.
    return {
        "type": chart_type, "title": chart["title"], "x_key": "name",
        "series": chart["series"], "data": chart["data"],
    }


def wants_visual(question: str) -> bool:
    """Whether the question is asking to see a chart, not just be told a
    number. Computed once per request and applied uniformly to whichever
    tool(s) end up running - deterministic regardless of whether the
    agentic or keyword path picked them."""
    normalized = _normalize_chart_words(question)
    words = set(_words(normalized))
    if words & _VISUAL_WORDS:
        return True
    q = " ".join(_words(normalized))
    return any(phrase in q for phrase in _VISUAL_PHRASES)


# Forecasting is fixed at 7 days ahead - it cannot be retrained per-question
# to a different horizon. "month"/"quarter"/"year" always mean something
# longer; an explicit "N days"/"N weeks" only counts if it resolves to a
# different number of days than 7.
_UNHANDLED_HORIZON_WORDS = {"year", "yearly", "annual", "annually"}


def mentions_other_horizon(question: str) -> bool:
    """Whether the question names a horizon truly outside what forecast_facts
    can answer at all - daily/weekly/monthly/quarterly are now genuinely
    handled (see detect_forecast_horizon), each labelled with its own real
    basis, so only a longer span than that (yearly+) still needs the blanket
    "can't do this" disclosure."""
    return bool(set(_words(question)) & _UNHANDLED_HORIZON_WORDS)


_HORIZON_NOTE = (
    "This system's forecasts don't extend that far - the validated model reaches 7 days ahead, "
    "and monthly/quarterly figures beyond that are historical projections at best. "
    "Here is the real 7-day forecast:\n\n"
)


def apply_horizon_note(question: str, result: dict) -> dict:
    """Guarantee the disclosure appears for a horizon nothing in forecast_facts
    covers (yearly+), rather than trust the LLM's instruction-following alone."""
    if "forecast" not in (result.get("tools_used") or []) or not mentions_other_horizon(question):
        return result
    answer = result.get("answer") or ""
    if "7 day" in answer[:160].lower() or "7-day" in answer[:160].lower():
        return result
    return {**result, "answer": _HORIZON_NOTE + answer}
