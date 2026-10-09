"""Rule-based AI Recommendations.

Every recommendation is computed from the client's own uploaded data - the
forecast against the planned-capacity column - and never invented here or by
an LLM.

The four canonical rule families this module used to hold (staffing gap over
a fixed site pair, overtime, attendance, schedule adherence) are gone with
the Sodexo schema. Three of them read per-employee overtime hours, punch
records and pay rates, which no uploaded forecasting file carries; the
fourth is replaced by the dataset gap rule, which finds spare capacity
across whatever series the client's own grouping produced.

No optimiser: gaps are computed directly from required versus planned, not
from a constraint-solved schedule.

Card copy is written in the register a site manager expects in a management
report: plain English and free of jargon, but professional rather than
conversational.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.services.recommendations import datasets as dataset_rules

logger = logging.getLogger(__name__)

_PRIORITY_RANK = {"High": 3, "Medium": 2, "Low": 1}


def compute_recommendations(db: Session, limit: int = 20) -> list[dict]:
    all_recs = dataset_rules.uploaded_recommendations(db)
    all_recs.sort(key=lambda r: (_PRIORITY_RANK.get(r["priority"], 0), r["priority_score"]), reverse=True)
    for rec in all_recs:
        rec.pop("priority_score", None)
    return all_recs[:limit]


def empty_reason(db: Session) -> str:
    """Shown in place of the cards when compute_recommendations returns none."""
    return dataset_rules.empty_reason(db)


def answer_question(db: Session, question: str) -> dict:
    """The Q&A box on the recommendations page.

    Only one rule family survives (capacity shortfalls), so this no longer
    branches across overtime, cost and attendance vocabularies - those
    branches matched categories that are never produced now. One of them was
    already dead before the rewrite: it looked for "Reassign" while the card
    copy said "Move ... across from".
    """
    q = question.lower()
    recs = compute_recommendations(db, limit=100)

    if not recs:
        return {
            "question": question,
            "answer": (
                "No recommendations are flagged. These compare forecast demand against a planned-capacity "
                "column, so they appear once a dataset is trained and has one mapped."
            ),
            "recommendations": [],
        }

    transfers = [r for r in recs if "Move " in r["recommended_action"]]
    if "move" in q or "reassign" in q or "transfer" in q or "spare" in q:
        matches = transfers[:5]
        answer = (
            f"{len(transfers)} recommendation(s) can be covered by moving spare capacity from another series."
            if transfers else
            "No current shortfall can be covered from another series - none has spare capacity at the same time."
        )
    elif "attention" in q or "immediate" in q or "priority" in q or "first" in q:
        matches = recs[:5]
        answer = f"{matches[0]['location_department']} needs attention first: {matches[0]['problem']}"
    else:
        matches = recs[:5]
        answer = f"Top current recommendation: {matches[0]['problem']}"

    return {"question": question, "answer": answer, "recommendations": matches}
