"""Real OpenAI function-calling: the model picks which of the application's
own data tools to call (by name only - every tool takes no arguments, so
there is nothing for the model to get wrong about *what* to pass, only
*which* tools are relevant), the tools run against the real database, and a
second call turns the results into the grounded, formatted answer.

This is the primary path when OPENAI_API_KEY is configured. `service.ask()`
catches any exception from `run()` (including "the model called no tools at
all", raised explicitly below) and falls back to the deterministic
keyword+template path in that case, so a routing or API outage degrades the
answer rather than breaking the request.
"""
import json
import logging

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.assistant import llm, tools

logger = logging.getLogger(__name__)

# One round is enough for almost every question ("what's the cost trend?").
# A second round covers a follow-on need surfaced by the first round's
# results. Capped low because every round is a full model round-trip.
MAX_ROUNDS = 2
# Across all rounds combined - a hard ceiling so a pathological reply can't
# spend an unbounded number of DB queries in one request.
MAX_TOOL_CALLS = 4


def _tool_schema() -> list[dict]:
    """Zero-argument function per tool - the model chooses *which* tools are
    relevant, never *what* to pass them. Trusting an LLM-authored paraphrase
    as the scoping text would bypass the deterministic _alias_hits matching
    tools.py relies on, so scoping is handled entirely outside this call via
    the persisted `scope` instead."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
        for name, desc in tools.TOOL_DESCRIPTIONS.items()
    ]


def _assistant_msg(message) -> dict:
    """Hand-built rather than message.model_dump() - the SDK's pydantic dump
    carries extra fields (refusal, audio, ...) that vary by SDK version, and
    resubmitting an unfamiliar shape on the next call is an avoidable
    failure mode."""
    return {
        "role": "assistant",
        "content": message.content,
        "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in message.tool_calls
        ],
    }


def _tool_msg(tool_call_id: str, facts: dict) -> dict:
    # The model never needs axis/data arrays to write prose about a number,
    # and it costs real tokens - strip the chart before it goes in the prompt.
    payload = {k: v for k, v in facts.items() if k != "chart"}
    return {"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(payload, default=str, ensure_ascii=False)}


def run(db: Session, question: str, history: list[dict] | None, scope: dict, wants_visual: bool, chart_type: str | None = None) -> dict:
    settings = get_settings()
    scoping_text = tools.scoping_text_from_scope(question, scope)
    messages = [
        {"role": "system", "content": llm._SYSTEM_PROMPT},
        *llm.history_messages(history),
        {"role": "user", "content": question},
    ]
    schema = _tool_schema()
    executed: list[tuple[str, dict]] = []  # (tool_name, facts), call order preserved
    seen: set[str] = set()

    for _round in range(MAX_ROUNDS):
        response = llm.client().chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            tools=schema,
            tool_choice="auto",
            temperature=0,
            max_tokens=60,  # arguments are always "{}" - nothing here to budget for
        )
        message = response.choices[0].message
        if not message.tool_calls:
            # A tool-call-free reply is never trusted as the final answer -
            # it means either laziness or an ungrounded decline. Either way,
            # this falls through to the keyword+template fallback in
            # service.py rather than returning unverified text.
            break

        messages.append(_assistant_msg(message))
        remaining = MAX_TOOL_CALLS - len(executed)
        for i, call in enumerate(message.tool_calls):
            name = call.function.name
            # Every tool_call_id needs exactly one role:"tool" reply, always -
            # including ones we choose not to actually run - or the next
            # create() call 400s on the mismatched conversation shape.
            if name in seen:
                messages.append(_tool_msg(call.id, {"note": "already retrieved this turn"}))
                continue
            if i >= remaining or name not in tools.TOOLS:
                messages.append(_tool_msg(call.id, {"skipped": "tool budget exceeded"}))
                continue
            try:
                facts = tools.TOOLS[name](db, scoping_text, visual=wants_visual, chart_type=chart_type)
            except Exception as exc:
                logger.warning("Assistant tool %s failed: %s", name, exc)
                facts = {"status": "This data source is temporarily unavailable."}
            executed.append((name, facts))
            seen.add(name)
            messages.append(_tool_msg(call.id, facts))
        if len(executed) >= MAX_TOOL_CALLS:
            break

    if not executed:
        raise RuntimeError("assistant agent selected no tools")

    final = llm.client().chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0,
        max_tokens=400,
    )
    content = final.choices[0].message.content
    # Same post-processing generate_answer() applies to its single-shot
    # answer - a "Next step" that only says Continue/Monitor/Keep/Maintain/
    # Ensure is worse than no next step, and answers are capped to the same
    # short-paragraph length either path produces.
    answer = llm._cap(llm._drop_filler_next_step(content)) if content else ""
    facts = dict(executed)
    # First tool call (in the order the model made them) whose facts carry a
    # chart - one chart per reply keeps the chat readable.
    chart = next((f["chart"] for _, f in executed if wants_visual and f.get("chart")), None)
    if not answer:
        stripped = {name: {k: v for k, v in f.items() if k != "chart"} for name, f in facts.items()}
        answer = llm.generate_answer(question, stripped, history)
    return {
        "question": question,
        "answer": answer,
        "tools_used": [name for name, _ in executed],
        "routed_by": "model_agentic",
        "facts": facts,
        "chart": chart,
        "scope": scope,
        "llm_used": True,
    }
