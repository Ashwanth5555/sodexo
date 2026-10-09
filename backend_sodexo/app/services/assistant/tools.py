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
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelPrediction, ModelRun, TrainingJob

logger = logging.getLogger(__name__)


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


def _periods_per_day(db: Session, series_key: str) -> int:
    """How many rows one day of this series occupies.

    The canonical grain was two half-day blocks a day, and that constant was
    being used to size history windows for uploaded data too - so "the last 30
    days" asked for 60 rows, which is 60 days of a daily file and two and a
    half hours of an hourly one. Read it from the dataset instead.
    """
    try:
        from app.services.datasets.series import grain_for
        from app.services.datasets.workspace import resolve

        dataset, _spec = resolve(db, series_key)
        return max(1, grain_for(dataset.frequency or "D")["per_day"])
    except Exception:
        return 1


def _project_from_history(db: Session, run: ModelRun, slots: int, pct_change: float | None) -> float | None:
    """A historical-average projection for a horizon the model was never
    validated at (monthly/quarterly) - the average of the most recent
    `slots` real ACTUAL values at the series' own grain, nudged by the model's own
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


# Scoping is derived from the data, not declared here.
#
# This used to be two hardcoded alias tables - LOC01 meaning "community
# hospital", CAFE meaning "cafe"/"retail"/"kiosk" - which only worked for the
# Sodexo dataset. With every series coming from a client upload, the words
# that identify a series are the words the CLIENT put in their group column,
# so the vocabulary has to be built from the dataset at question time.


def _series_aliases(series_list) -> dict[str, tuple[str, ...]]:
    """series_key -> the words that would make someone mean that series.

    Built from each series' own label. A label like "Depot labour plan /
    North Depot" yields "north", "depot", "labour", "plan" - so "how is
    north depot doing" scopes to it. Short tokens are dropped: matching on
    "of" or "and" would scope every question to whichever series happened to
    contain them.
    """
    aliases: dict[str, tuple[str, ...]] = {}
    for series in series_list:
        label = (series.label or "").lower()
        tokens = {t for t in _words(label) if len(t) > 3}
        # The full label as one phrase, so "north depot" beats either word
        # alone when both appear.
        phrases = {p.strip() for p in label.split("/") if len(p.strip()) > 3}
        aliases[series.key] = tuple(tokens | phrases)
    return aliases


def _discriminating_aliases(aliases: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Drop words shared by every series.

    Splitting one file by group gives labels with a common prefix - "Depot
    labour plan / North Depot" and "Depot labour plan / South Depot" share
    three words out of four. Matching on those would scope to everything,
    which is the same as not scoping at all, so only the words that actually
    tell two series apart are kept.
    """
    if len(aliases) < 2:
        return aliases
    counts: dict[str, int] = {}
    for terms in aliases.values():
        for term in set(terms):
            counts[term] = counts.get(term, 0) + 1
    total = len(aliases)
    return {
        key: tuple(t for t in terms if counts.get(t, 0) < total) or terms
        for key, terms in aliases.items()
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


def _scope_label(question: str, series_list=None) -> str:
    """What the question narrowed to, in words.

    Names the matched series, because with uploaded data there is no fixed
    vocabulary of sites and departments to name instead.
    """
    if not series_list:
        return "everything loaded"
    matched = _mentioned_series(question, series_list)
    if len(matched) >= len(series_list):
        return "everything loaded"
    return ", ".join(s.label for s in matched)


class _SeriesRef:
    """A series the assistant can talk about.

    location_id and `uploaded` are vestiges of the period when built-in and
    uploaded series coexisted and tools had to branch on which was which.
    They are kept because the scoping helpers still read them, and because a
    second source would reintroduce the distinction.
    """

    __slots__ = ("key", "label", "location_id", "uploaded")

    def __init__(self, key: str, label: str, location_id: str | None, uploaded: bool):
        self.key = key
        self.label = label
        self.location_id = location_id
        self.uploaded = uploaded


def series_unit(db: Session, ref) -> str:
    """What one unit of this series' target actually is, in words.

    The built-in series forecast required_manpower per half-day block, which
    the answers have always called "required staff/hour". An uploaded series
    forecasts whatever column the client picked, at whatever frequency they
    chose - calling that "staff/hour" states a unit the number does not have.
    """
    if not getattr(ref, "uploaded", False):
        return "required staff/hour"
    try:
        from app.services.datasets.workspace import resolve

        dataset, _spec = resolve(db, ref.key)
        target = dataset.target_column or "units"
        period = {"H": "hour", "D": "day", "W": "week", "MS": "month"}.get(dataset.frequency or "D", "period")
        return f"{target} per {period}"
    except Exception:
        return "units"


def all_series(db: Session) -> list[_SeriesRef]:
    """Every trained uploaded series - there are no built-in ones.

    Trained only: a dataset that has been mapped but never trained has no
    results to answer from, and including it would put "no completed model"
    noise into an answer instead of a fact.
    """
    from app.services.datasets.training import all_uploaded_series

    try:
        return [
            _SeriesRef(s.key, s.label, None, uploaded=True)
            for s in all_uploaded_series(db, trained_only=True)
        ]
    except Exception:
        # A dataset whose file went missing must not stop the assistant
        # answering at all - it can still decline cleanly with no series.
        logger.warning("Could not list series for the assistant", exc_info=True)
        return []


def _mentioned_series(question: str, series_list) -> list:
    """Scope a question to the series it names.

    The vocabulary comes from the series' own labels, so it adapts to
    whatever the client called their groups. Falls back to every series when
    nothing specific is named - a general question should see everything.
    """
    if not series_list:
        return []
    aliases = _discriminating_aliases(_series_aliases(series_list))
    hits = _alias_hits(question, aliases)
    if not hits:
        return list(series_list)
    matched = [s for s in series_list if s.key in hits]
    return matched or list(series_list)


def forecast_facts(db: Session, question: str = "", visual: bool = False, chart_type: str | None = None) -> dict:
    job = _latest_job(db)
    if not job:
        return {"status": "No training run yet - Labor Forecasting has not been trained."}

    from app.services.forecasting import workspace

    series_list = all_series(db)
    scoped_series = _mentioned_series(question, series_list)
    scoped_keys = {s.key for s in scoped_series}
    # Uploaded series are trained by their own job, so filtering champions to
    # the latest job id would drop every one of them. Match on the champion
    # flag per series instead, which is set once selection completes for
    # whichever job trained it.
    champions = db.execute(
        select(ModelRun).where(ModelRun.is_champion.is_(True), ModelRun.series_key.in_(scoped_keys))
    ).scalars().all()

    staffing = []
    for series in scoped_series:
        try:
            impact = workspace.get_staffing_impact(db, series.key, days=14)
        except LookupError:
            # An uploaded series with no companion file has no planned curve
            # to compare against - that is expected, not an error.
            continue
        points = impact.get("points") or []
        if not points:
            continue
        # The whole outlook, not one arbitrary period. This used to hand over
        # points[-1] alone, so a horizon with three short weeks in it was
        # summarised by whichever week happened to fall last - and the
        # assistant answered "no site is short" while the recommendations
        # page listed the shortfalls. One page contradicting another is worse
        # than either being wrong on its own.
        outlook = impact.get("outlook") or {}
        ahead = [p for p in points if p.get("is_forecast") and p.get("gap") is not None]
        worst = min(ahead, key=lambda p: p["gap"]) if ahead else None
        latest = points[-1]
        staffing.append({
            "series": series.label,
            "measured_in": impact.get("required_label"),
            "periods_ahead": outlook.get("periods"),
            "periods_short_ahead": outlook.get("short_periods"),
            "cover_to_find": outlook.get("total_shortfall"),
            "worst_period": None if worst is None else worst["date"],
            "worst_gap": None if worst is None else worst["gap"],
            # Named for their scope, not left to a reading rule. A field
            # called "status" beside a horizon summary gets answered from,
            # however clearly the prompt says otherwise - so the name itself
            # now says it describes one period.
            "latest_period": latest["date"],
            "required_in_latest_period": latest["required_headcount"],
            "planned_in_latest_period": latest["scheduled_headcount"],
            "gap_in_latest_period": latest["gap"],
            "status_in_latest_period": latest["status"],
        })

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
    label_by_key = {s.key: s.label for s in series_list}
    ref_by_key = {s.key: s for s in series_list}

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
        window_days = 30 if horizon == "monthly" else 90
        pct_change = trend["pct_change"] if trend else None
        projected = _project_from_history(
            db, c, window_days * _periods_per_day(db, c.series_key), pct_change
        )
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
        "scope": [s.label for s in scoped_series] if len(scoped_series) < len(series_list) else "everything loaded",
        "requested_horizon": horizon,
        "champions": [
            {
                "series": label_by_key.get(c.series_key, c.series_key), "model": c.model_label,
                "mape": c.mape, "accuracy": c.accuracy, "trend": _trend_for_run(db, c),
                "forecast_at_requested_horizon": _horizon_forecast(c),
                # Carried per series so the answer states this series' own
                # unit rather than the built-in schema's.
                "unit": series_unit(db, ref_by_key.get(c.series_key)) if ref_by_key.get(c.series_key) else "units",
            }
            for c in champions
        ],
        "staffing_outlook": staffing,
        "demand_pattern": demand_pattern,
    }
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
    champions = forecast.get("champions") or []
    # The employee-level classifiers (overtime, absenteeism, adherence) went
    # with the canonical schema - they were trained on per-worker punch and
    # timecard rows, which an uploaded forecasting file does not contain.
    facts = {"forecast_champions": champions}
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

    results = []
    chart = None
    for series in _mentioned_series(question, all_series(db)):
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


# Only the tools that can be answered from an uploaded file. The labor
# cost, punch and per-employee risk tools are gone with the Sodexo schema:
# they read pay rates, punch records and employee rows, which a forecasting
# file does not carry, and answering them from nothing would mean inventing
# figures.
TOOLS = {
    "forecast": forecast_facts,
    "recommendations": recommendation_facts,
    "model_performance": model_performance_facts,
    "data_quality": data_quality_facts,
    "drift": drift_facts,
}

# Shown to the LLM router so it can pick tools by meaning rather than by
# keyword overlap. Keep in step with TOOLS.
TOOL_DESCRIPTIONS = {
    "forecast": "labor demand forecast, forecast trend and direction, required vs scheduled headcount, staffing gaps, under/overstaffed shifts, demand patterns by hour or day",
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
_OVERVIEW_TOPICS = ["recommendations", "forecast", "model_performance"]


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


_EMPTY_SCOPE = {"series_keys": []}


def merge_scope(question: str, current_scope: dict | None, history: list[dict] | None = None) -> dict:
    """Carry the previous turn's series scope into a follow-up.

    Scope is now a list of series keys rather than a site/department pair,
    because with uploaded data there is no fixed dimension to scope on. A
    question that names a series wins; otherwise a follow-up ("why?", "what
    about there?") inherits the previous turn's series, and a self-contained
    question resets to everything.
    """
    current_scope = current_scope or {}
    carry = is_follow_up(question) or bool(set(_words(question)) & _REFERENTIAL_WORDS)
    if carry and current_scope.get("series_keys"):
        return {"series_keys": list(current_scope["series_keys"])}
    return {"series_keys": []}


def scope_from_series(matched, series_list) -> dict:
    """The scope to persist after answering, for the next turn to inherit."""
    if not matched or len(matched) >= len(series_list):
        return {"series_keys": []}
    return {"series_keys": [s.key for s in matched]}


def scoping_text_from_scope(question: str, scope: dict | None) -> str:
    """Prefix the question with the carried series' names.

    The fact tools scope by matching words against series labels, so the
    cheapest way to apply an inherited scope is to put those labels in front
    of the question and let the existing matching pick them up.
    """
    keys = (scope or {}).get("series_keys") or []
    if not keys:
        return question
    return f"{' '.join(keys)} {question}"


_CHART_TYPE_PHRASES: dict[str, tuple[str, ...]] = {
    "bar": ("bar graph", "bar chart", "bar plot", "column chart", "columns"),
    "line": ("line graph", "line chart", "line plot", "trend line"),
    "donut": ("donut", "doughnut", "pie chart", "pie graph"),
}

_VISUAL_WORDS = {"chart", "graph", "plot", "visual", "picture"}
_VISUAL_PHRASES = ("trend line", "show me", "over time")
# "barchart" and "bar chart" mean the same thing; split the compound so the
# word-set match below sees "chart" either way.
_COMPOUND_CHART_WORD = re.compile(r"(pie|bar|line|donut|doughnut|column)(chart|graph|plot)s?")


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
