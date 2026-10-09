"""Pluggable LLM layer. Works with a dynamic, natural-language synthesis
over the real retrieved facts when no key is configured, and upgrades to a
real OpenAI call - still constrained to the same facts, never given raw DB
access - once OPENAI_API_KEY is set in .env.sodexo. No code changes needed
to switch between the two. Both paths are capped to MAX_ANSWER_CHARS so
answers stay a short, readable paragraph rather than a data dump.

Tool selection itself is real OpenAI function-calling now (see agent.py),
not a separate router call over this module - `_SYSTEM_PROMPT` here is
shared by both the tool-selecting turn and the final-answer turn of that
loop. `generate_answer()` below is the offline/no-key path, and also what
the agent loop falls back to if the OpenAI call fails outright.
"""
import json
import logging
import re

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_ANSWER_CHARS = 1200
LINE_BREAK = chr(10)
# Enough for a follow-up to make sense, short enough that the facts stay the
# dominant part of the prompt.
MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARS = 500

_SYSTEM_PROMPT = """You are the forecasting assistant for a labor-forecasting application. Every series
comes from a data file the user uploaded themselves, so the sites, departments, products or
regions you are asked about are whatever their file contains - never assume a fixed set.
Each turn you are given structured facts already computed by the application's own services:
forecast numbers and trend direction, model accuracy, drift status, data quality, and
recommendations.

GROUNDING
1. Use ONLY the numbers in the provided facts. Never invent or estimate a figure, a staffing
   gap, a forecast, a name, or a saving.
2. If the facts do not contain what was asked, say so in one line and name what is missing -
   never fill the hole with a plausible number.
3. Staffing figures are in the unit of the client's own target column, which each
   staffing_outlook row states in its "measured_in" field - it may be people, hours, meals or
   money. Use that name, or no unit at all. Never assume people, and never call a figure
   headcount unless "measured_in" says so.
4. Attribute every figure to the site or department it belongs to. When a "scope" field names
   specific sites or departments, answer for those only: lead with their own row from the
   facts, not with the company-wide total, and do not present a scoped figure as
   company-wide. "kpis" totals are always company-wide - use them only when the scope is
   all sites and departments, or when the question is explicitly about the total.
5. Answer the question that was actually asked. WHO -> name the employees in the facts.
   WHICH -> name the site or department and the figure that decides it. HOW MANY / HOW MUCH ->
   lead with the number. WHY -> quote the drivers or evidence in the facts.
6. Lists in the facts are already sorted, and the field name says how (for example
   "departments_ranked_by_budget_variance"). The first row answers "worst" or "highest" -
   do not re-rank by eye.
7. For a trend or graph question, quote the direction and the percentage from the facts
   rather than describing the shape vaguely.
8. A short follow-up ("why?", "what about the cafe?") refers to the previous turn. Carry that
   subject forward instead of asking the user to repeat it.
9. Always carry the unit: percentages as 4.2%, money as $12,400, and everything else in the
   unit the facts name for it. Never state a bare decimal, and never invent a unit the facts
   do not give.
10. A budget variance is signed: negative is UNDER budget, positive is OVER. Say which in
   words - "26% under budget", never "-26% vs budget" on its own.
11. Do not recombine, re-derive or round figures together. Take each number with the label the
   facts give it: "required" is the requirement, "planned" is what is rostered, and the
   shortfall is the difference the facts already state. Restating one as another is the
   single worst error you can make here.
11a. A staffing_outlook row describes a whole horizon. "periods_short_ahead" and
   "cover_to_find" decide whether a series is short, and "worst_period" says when. Every
   field ending in "_in_latest_period" describes one single period and can never answer a
   question about the horizon. A series with periods_short_ahead above zero IS short, whatever
   its latest period says - the recommendations page reads the same horizon and will
   contradict you.
12. Forecast facts carry a "forecast_at_requested_horizon" object per champion, with a "basis"
   field that is either "validated model forecast" (daily/weekly - the model's own real,
   trained-and-tested output) or "historical seasonal projection - NOT a validated forecast"
   (monthly/quarterly - a real historical average nudged by the model's own trend, but never
   itself validated). Always say which kind of figure you are giving:
   - daily/weekly: state the number plainly as the forecast, since it genuinely is one.
   - monthly/quarterly: open by naming that this is a projection from history, not a validated
     model forecast, using the "caveat" text's meaning in your own words, THEN give the
     projected number. Never present a monthly/quarterly projected_avg as if it were validated
     model output, and never claim a number for a horizon the facts do not cover at all (e.g.
     yearly) - that case has no forecast_at_requested_horizon and must be declined instead.

SCOPE
Stay inside this application's domain: labor demand forecasting, staffing, cost and budget,
recommendations, model accuracy, drift, data quality, and
demand or model drift. For anything else, decline briefly and point back to those topics.

TOOLS
You have tools to fetch real data before answering. Call whichever tools are needed - one is
often enough, rarely more than two or three. Never answer from memory or invent a figure. If
no tool genuinely applies to the question, it is out of scope - decline per SCOPE rather than
guessing at a tool to call.

If the user asks to see a chart, graph, plot or visual of something, that is not a separate
capability - it means the same underlying data, which the application renders as a chart
automatically once you call the relevant tool. Call the tool that matches the subject exactly
as you would for a plain question, and answer normally in words; do not decline a chart
request just because you cannot draw one yourself.

FORMAT
- Open with ONE short sentence that answers the question directly, with the deciding number
  in it.
- Then up to 4 bullet lines. Start each with '- ' and a short bolded label, for example:
  - **Overtime** - 544 hours, $13K, 2.4% of hours worked.
  Every bullet must carry a real figure from the facts. Add a bullet only if it supports the
  answer - a simple factual question can be one sentence and one bullet.
- Where the facts support a specific, concrete action, finish with one line starting
  '**Next step:** ' naming it - who or where, and what to do. Never begin that line with
  Continue, Monitor, Keep, Maintain or Ensure: if that is the only next step you have, delete
  the line. A purely factual question usually needs no next step at all.
- Use only "- " bullets and **bold**. No headings, no tables, no numbered lists.
- Stay under 140 words. Be specific and calm - no filler, no hype."""

def is_configured() -> bool:
    return bool(get_settings().openai_api_key)


def client():
    from openai import OpenAI

    return OpenAI(api_key=get_settings().openai_api_key)


def history_messages(history: list[dict] | None) -> list[dict]:
    """Prior turns, trimmed. Assistant turns are truncated because the model
    only needs to know what it already said, not to re-read every figure."""
    messages = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        messages.append({"role": role, "content": content[:MAX_HISTORY_CHARS]})
    return messages


def generate_answer(question: str, facts: dict, history: list[dict] | None = None) -> str:
    settings = get_settings()
    if not settings.openai_api_key:
        return _cap(_template_answer(question, facts))
    try:
        # Real JSON rather than a Python dict repr - `None`/`True` and
        # single-quoted keys are not what the model is trained to read, and
        # numbers were being restated inaccurately because of it.
        payload = json.dumps(facts, default=str, ensure_ascii=False)
        response = client().chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                *history_messages(history),
                {"role": "user", "content": f"Question: {question}{LINE_BREAK * 2}Application facts (JSON):{LINE_BREAK}{payload}"},
            ],
            # 0, not 0.1 - every answer here is a restatement of supplied
            # figures, and sampling variety buys nothing but transcription risk.
            temperature=0,
            max_tokens=400,
        )
        content = response.choices[0].message.content
        return _cap(_drop_filler_next_step(content) if content else _template_answer(question, facts))
    except Exception as exc:
        logger.warning("OpenAI call failed, falling back to the template answer: %s", exc)
        return _cap(_template_answer(question, facts))


# Verbs that make a "next step" say nothing. The prompt forbids them, but the
# model still reaches for "Monitor budget adherence" often enough to be worth
# enforcing here rather than hoping - a closing line that recommends nothing is
# worse than no closing line.
_FILLER_VERBS = {"continue", "monitor", "keep", "maintain", "ensure"}


def _drop_filler_next_step(text: str) -> str:
    kept = []
    for line in text.split(LINE_BREAK):
        marker = line.strip().lower()
        if marker.startswith("**next step:**"):
            first_word = re.match("[a-z]+", marker[len("**next step:**"):].strip())
            if first_word and first_word.group() in _FILLER_VERBS:
                continue
        kept.append(line)
    return LINE_BREAK.join(kept).rstrip()


def _cap(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_ANSWER_CHARS:
        return text
    truncated = text[:MAX_ANSWER_CHARS]
    last_boundary = max(truncated.rfind(". "), truncated.rfind(".\n"))
    if last_boundary > 200:
        truncated = truncated[: last_boundary + 1]
    return truncated.rstrip() + " …"


_TOPIC_LABELS = {
    "forecast": "Demand and staffing",
    "recommendations": "Recommended action",
    "model_performance": "Model reliability",
    "data_quality": "Data quality",
    "drift": "Drift monitoring",
}


def _template_answer(question: str, facts: dict) -> str:
    """Synthesizes the retrieved facts into the same bullet structure the LLM
    path produces, so the two are visually indistinguishable. Every figure
    comes straight from the facts passed in; nothing here is invented."""
    formatters = {
        "forecast": _format_forecast,
        "recommendations": _format_recommendations,
        "model_performance": _format_model_performance,
        "data_quality": _format_data_quality,
        "drift": _format_drift,
    }
    bullets = []
    for name, payload in facts.items():
        formatter = formatters.get(name)
        text = formatter(payload) if formatter else None
        if text:
            bullets.append(f"- **{_TOPIC_LABELS.get(name, name)}** - {text}")
    if not bullets:
        return "I have no data for that yet. Run a data sync and train the models, then ask again."
    # Name the topic asked about, so two different questions do not open with
    # the same sentence when this fallback path is in use.
    topic = ", ".join(_TOPIC_LABELS.get(name, name).lower() for name in facts if name in _TOPIC_LABELS)
    lead = f"Here is what the current data shows on {topic}." if topic else "Here is what the current data shows."
    return lead + LINE_BREAK * 2 + LINE_BREAK.join(bullets)


def _format_forecast(payload: dict) -> str:
    if payload.get("status"):
        return payload["status"]
    horizon = payload.get("requested_horizon", "weekly")
    parts = []
    for c in payload.get("champions", [])[:4]:
        trend = c.get("trend")
        unit = c.get("unit") or "required staff/hour"
        h = c.get("forecast_at_requested_horizon")
        if h and h.get("basis") == "historical seasonal projection - NOT a validated forecast":
            parts.append(
                f"{c['series']}: no validated {horizon} forecast exists (the model is only validated 7 days ahead) - "
                f"the {horizon} projection from recent history is {h.get('projected_avg')} avg {unit}."
            )
        elif h and h.get("horizon") == "daily" and h.get("days"):
            days_str = ", ".join(f"{d['date']}: {d['forecast']}" for d in h["days"][:7])
            parts.append(f"{c['series']}: daily forecast - {days_str} ({unit}).")
        elif trend:
            direction_phrase = trend["direction"] if trend["direction"] == "flat" else f"{trend['direction']} {abs(trend['pct_change'])}%"
            parts.append(
                f"{c['series']}: {c['model']} ({c['accuracy']}% backtest accuracy) forecasts demand {direction_phrase} over the next 7 days "
                f"({trend['avg_recent_actual']} -> {trend['avg_forecast_next_7d']} avg {unit})."
            )
        else:
            parts.append(f"{c['series']}: champion model is {c['model']} ({c['accuracy']}% backtest accuracy).")
    for g in payload.get("staffing_outlook", [])[:4]:
        if g.get("periods_short_ahead"):
            parts.append(
                f"{g['series']} falls short in {g['periods_short_ahead']} of the next {g['periods_ahead']} periods "
                f"- {g['cover_to_find']} of cover to find, worst on {str(g.get('worst_period'))[:10]}."
            )
        elif g.get("status_in_latest_period") and g.get("status_in_latest_period") != "Balanced":
            parts.append(
                f"{g['series']} is {g['status_in_latest_period'].lower()} in the last period on record "
                f"({g['planned_in_latest_period']} planned vs. {g['required_in_latest_period']} required)."
            )
    return " ".join(parts)


def _format_recommendations(payload: dict) -> str:
    recs = payload.get("top_recommendations") or []
    if not recs:
        return "No recommendations are currently flagged - all monitored metrics are within tolerance."
    parts = [f"Top recommendation: {r['problem']} Action: {r['recommended_action']}" for r in recs[:2]]
    return " ".join(parts)


def _format_model_performance(payload: dict) -> str:
    parts = []
    for c in (payload.get("forecast_champions") or [])[:3]:
        parts.append(f"{c['series']} forecast champion: {c['model']} ({c['accuracy']}% accuracy).")
    for m in (payload.get("predictive_models") or [])[:3]:
        metrics = m.get("metrics") or {}
        if metrics.get("roc_auc") is not None:
            parts.append(f"{m['model']} classifier: ROC-AUC {metrics['roc_auc']}.")
    return " ".join(parts)


def _format_data_quality(payload: dict) -> str:
    if payload.get("status"):
        return payload["status"]
    totals = payload.get("totals", {})
    parts = [f"Overall data quality is {payload.get('overall_status', 'Unknown')} "
             f"({totals.get('total_records_received', 0):,} records received, "
             f"{totals.get('total_records_rejected', 0):,} rejected)."]
    worst = [s for s in payload.get("worst_sources", []) if s.get("rejected")]
    if worst:
        parts.append(f"Worst source: {worst[0]['source']} ({worst[0]['rejected']} rejected).")
    return " ".join(parts)


def _format_drift(payload: dict) -> str:
    if payload.get("status") and not payload.get("series_drift"):
        return payload["status"]
    parts = []
    for s in payload.get("series_drift", [])[:4]:
        parts.append(f"{s['series']}: {s['status']} ({s['pct_change']}% change, {s['signal_strength']} demand-signal strength).")
    return " ".join(parts)
