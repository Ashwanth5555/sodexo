import logging

from sqlalchemy.orm import Session

from app.services.assistant import llm, tools

logger = logging.getLogger(__name__)

# Both replies use the same bullet structure the real answers do, so an
# out-of-scope question or a plain "hi" still looks like part of the product.
_SCOPE_MESSAGE = """That one is outside what I can see - I only read the data in this application.

**I can help with**
- Demand forecasts, and where demand is heading
- Capacity gaps - where a period is short of what was planned, or has too much
- Model accuracy, and which model won for each series
- Drift - whether a trained model can still be trusted
- Data quality of the file you uploaded
- The ranked list of recommended actions

**Try**
- "What is the demand trend?"
- "Where are the biggest capacity gaps?\""""

_GREETING_MESSAGE = """Hello. I am the forecasting assistant for this application, and every number I give you is read from the data you uploaded - never invented.

**What I can tell you about**
- **Demand forecasts** - where demand is heading, and how reliable that forecast is
- **Capacity gaps** - which periods fall short of what was planned, and by how much
- **Model performance** - which of the models won for each series, and how accurate it is
- **Drift** - whether the data has moved away from what the models were trained on
- **Data quality** - how much of your file was usable, and what was not
- **What to do next** - the current recommendations, ranked by priority

**A good place to start**
- "What is the demand trend?"
- "Which series needs attention first?"
- "Show me a chart of the forecast\""""


def ask(db: Session, question: str, history: list[dict] | None = None, current_scope: dict | None = None) -> dict:
    if tools.is_greeting(question):
        return {
            "question": question, "answer": _GREETING_MESSAGE, "tools_used": [],
            "facts": {}, "chart": None, "scope": current_scope or tools._EMPTY_SCOPE, "llm_used": False,
        }

    scope = tools.merge_scope(question, current_scope, history)
    visual = tools.wants_visual(question)
    chart_type = tools.wants_chart_type(question) if visual else None

    if llm.is_configured():
        try:
            from app.services.assistant import agent

            return tools.apply_horizon_note(question, agent.run(db, question, history, scope, visual, chart_type))
        except Exception as exc:
            logger.warning("Agentic assistant path failed, falling back to keyword routing: %s", exc)
    return tools.apply_horizon_note(question, fallback_ask(db, question, history, scope, visual, chart_type))


def fallback_ask(db: Session, question: str, history: list[dict] | None, scope: dict, visual: bool, chart_type: str | None = None, routed_by: str = "keyword") -> dict:
    """The one and only fallback answer path - used both when no OpenAI key
    is configured at all, and when the agentic path above raises for any
    reason, so there is never a second, divergent copy of this logic to
    drift out of sync."""
    # A follow-up like "why?" carries no keywords of its own, so the previous
    # turns decide the topic (scope itself already comes from `scope`, merged
    # by the caller - this only affects *which tools* select_tools picks).
    routing_question = question
    if history and tools.is_follow_up(question):
        previous = " ".join(t.get("content", "") for t in history if t.get("role") == "user")
        routing_question = f"{previous} {question}".strip()

    tool_names = tools.select_tools(routing_question)
    if not tool_names:
        return {
            "question": question, "answer": _SCOPE_MESSAGE, "tools_used": [],
            "facts": {}, "chart": None, "scope": scope, "llm_used": False,
        }

    scoping_text = tools.scoping_text_from_scope(question, scope)
    facts = {name: tools.TOOLS[name](db, scoping_text, visual=visual, chart_type=chart_type) for name in tool_names}
    chart = next((f["chart"] for f in facts.values() if visual and f.get("chart")), None)
    stripped_facts = {name: {k: v for k, v in f.items() if k != "chart"} for name, f in facts.items()}
    answer = llm.generate_answer(question, stripped_facts, history)
    return {
        "question": question,
        "answer": answer,
        "tools_used": tool_names,
        "routed_by": routed_by,
        "facts": facts,
        "chart": chart,
        "scope": scope,
        "llm_used": llm.is_configured(),
    }
