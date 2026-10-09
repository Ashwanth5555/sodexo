"""Recommendations for uploaded datasets.

The canonical engine has four rule families: staffing gap, overtime,
attendance and schedule adherence. Only the first survives contact with an
arbitrary file - the other three read per-employee overtime hours, punch
records and pay rates, which no forecasting CSV carries, and inventing them
would be worse than not offering them.

So this produces gap recommendations and nothing else, from the same
forecast-versus-planned comparison Staffing Impact uses - both read the
target and capacity columns of the one uploaded file. Where the canonical
rule can look across a fixed pair of sites for spare cover, this looks across
every other series of the same dataset - the client's own grouping, whatever
it happens to mean.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UploadedDataset
from app.services.datasets import series as series_builder
from app.services.datasets import workspace as dataset_workspace

logger = logging.getLogger(__name__)

# How many gap recommendations one dataset may contribute. Without a cap, a
# 40-group upload with a chronic shortfall buries every other card.
MAX_PER_DATASET = 6


def _num(value: float) -> str:
    """A bare number, without a trailing unit.

    An uploaded target column is a phrase the client wrote ("Labour Hours
    Required"), not a unit. Appending it to every figure produced copy like
    "111.9 Labour Hours Required against 75.7 Labour Hours Required planned",
    so the metric is named once per card and the figures stay bare.
    """
    rounded = round(value, 1)
    whole = int(rounded)
    return str(whole) if abs(rounded - whole) < 0.05 else str(rounded)


def _when(iso: str, periods_per_day: int) -> str:
    """A timestamp phrased the way someone reading a roster would say it."""
    stamp = datetime.fromisoformat(iso)
    if periods_per_day <= 1:
        return stamp.strftime("%A %d %b")
    part = "morning" if stamp.hour < 12 else "afternoon"
    return f"{stamp.strftime('%A %d %b')} {part}"


def _dataset_recommendations(db: Session, dataset: UploadedDataset) -> list[dict]:
    # No planned-capacity column means no gap to report. Not an error: most
    # files record only what happened, not what was scheduled.
    # Same check as Staffing Impact, and for the same reason: a roster column
    # that is really a weekday number produced "short by 181" on every series,
    # which is a confident recommendation built on nothing.
    if not series_builder.is_plausible_capacity(dataset.capacity_column):
        return []

    try:
        specs = series_builder.list_series(dataset)
    except Exception:
        logger.warning("Could not list series for dataset %s", dataset.dataset_id, exc_info=True)
        return []

    # Gather every series' gap points once, so a shortfall in one can be
    # matched against a surplus in another at the same timestamp.
    impacts: dict[str, dict] = {}
    for spec in specs:
        try:
            impact = dataset_workspace.get_staffing_impact(db, spec.key, days=14)
        except Exception:
            # A single unusable series - no overlap, missing group value -
            # must not suppress recommendations for the rest of the file.
            logger.info("No staffing impact for %s", spec.key, exc_info=True)
            continue
        # Only periods that carry BOTH halves of the comparison. Staffing
        # Impact now returns forecast periods the roster does not reach yet,
        # whose gap is None - and a recommendation cannot be made about a
        # shortfall nobody has planned against. Left in, they also made
        # min(..., key=gap) compare None against a float and raise.
        usable = [p for p in impact.get("points", []) if p.get("gap") is not None]
        if usable:
            impacts[spec.key] = {"label": spec.label, "points": usable}

    if not impacts:
        return []

    periods_per_day = series_builder.grain_for(dataset.frequency or "D")["per_day"]
    metric = dataset.target_column or "the forecast value"
    by_key_date = {
        key: {p["date"]: p for p in payload["points"]}
        for key, payload in impacts.items()
    }

    recommendations = []
    for key, payload in impacts.items():
        worst = min(payload["points"], key=lambda p: p["gap"])
        if worst["gap"] >= 0:
            continue  # This series is covered across the whole window.
        shortfall = -worst["gap"]

        # Look for spare capacity in any sibling series at the same instant.
        donor_label, donor_surplus = None, 0.0
        for other_key, other_payload in impacts.items():
            if other_key == key:
                continue
            counterpart = by_key_date[other_key].get(worst["date"])
            if counterpart and counterpart["gap"] > donor_surplus:
                donor_surplus = counterpart["gap"]
                donor_label = other_payload["label"]

        reassign = min(shortfall, donor_surplus)
        add_needed = shortfall - reassign
        when = _when(worst["date"], periods_per_day)

        if reassign > 0 and add_needed > 0.05:
            action = (
                f"Move {_num(reassign)} across from {donor_label}, "
                f"then find cover for the remaining {_num(add_needed)}."
            )
        elif reassign > 0:
            action = f"Move {_num(reassign)} across from {donor_label}, which has spare at the same time."
        else:
            action = f"Add {_num(shortfall)} of capacity - no other series in this dataset has spare then."

        evidence = [
            f"Actual requirement: {_num(worst['required_headcount'])} {metric}.",
            f"Planned ({dataset.capacity_column}): {_num(worst['scheduled_headcount'])}.",
            (f"{donor_label}: {_num(donor_surplus)} spare at the same time."
             if donor_surplus > 0 else "No other series in this dataset has spare capacity then."),
            f"Basis: the last 14 days of recorded '{dataset.target_column}' against "
            f"'{dataset.capacity_column}' - a review of what happened, not a prediction.",
        ]

        recommendations.append({
            "id": f"ds-gap-{key}-{worst['date']}",
            "category": "Staffing Adjustment",
            # The canonical rule's threshold of 3 is a headcount. An uploaded
            # target could be hours, trays or euros, so severity is relative
            # to the requirement rather than an absolute count.
            "priority": "High" if shortfall >= 0.25 * max(worst["required_headcount"], 1e-9) else "Medium",
            "location_department": payload["label"],
            "headline": f"Capacity shortfall - {when}",
            "problem": (
                f"{metric} for {when} was {_num(worst['required_headcount'])} "
                f"against {_num(worst['scheduled_headcount'])} planned - short by {_num(shortfall)}."
            ),
            "evidence": evidence,
            "recommended_action": action,
            "expected_impact": f"The period is covered in full rather than running {_num(shortfall)} short.",
            # No pay rates in an uploaded file, so no cost estimate. The
            # canonical version's figure was already the wrong sign (it is a
            # cost, not a saving); not guessing one here is the honest choice.
            "estimated_savings": None,
            "savings_basis": None,
            "confidence": None,
            "priority_score": shortfall / max(worst["required_headcount"], 1e-9),
            "source": "uploaded",
        })

    recommendations.sort(key=lambda r: r["priority_score"], reverse=True)
    return recommendations[:MAX_PER_DATASET]


def empty_reason(db: Session) -> str:
    """Why there is nothing to recommend, in the reader's own terms.

    An empty page used to read "all monitored metrics are within tolerance",
    which says the opposite of the truth in the common case: nothing was
    within tolerance, nothing could be measured at all. The distinction
    matters - one means relax, the other means a column is missing.
    """
    datasets = db.execute(
        select(UploadedDataset).where(UploadedDataset.status == "configured")
    ).scalars().all()
    if not datasets:
        return "Nothing has been uploaded yet. Add a file on Data Quality and train it first."

    planned = [d for d in datasets if series_builder.is_plausible_capacity(d.capacity_column)]
    if not planned:
        names = ", ".join(sorted({d.original_filename for d in datasets}))
        return (
            f"Every recommendation on this page compares what was needed against what was planned. "
            f"{names} records what was needed, but no rostered, scheduled or budgeted figure to set "
            f"against it, so there is no gap to act on. Add one column of planned figures per period "
            f"and upload again. Staffing Impact shows the requirement ahead in the meantime."
        )
    return (
        "Every period in the last 14 days was covered - the planned figure met or exceeded the "
        "requirement throughout, so there is nothing to move or add."
    )


def uploaded_recommendations(db: Session) -> list[dict]:
    """Gap recommendations across every dataset with a planned-capacity column."""
    datasets = db.execute(
        select(UploadedDataset).where(UploadedDataset.status == "configured")
    ).scalars().all()

    out: list[dict] = []
    for dataset in datasets:
        try:
            out.extend(_dataset_recommendations(db, dataset))
        except Exception:
            logger.warning("Recommendations failed for dataset %s", dataset.dataset_id, exc_info=True)
    return out
