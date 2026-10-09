"""What the user needs to know before uploading, and a template to start from.

Both are generated from the application's own constants rather than written
out by hand. A limit stated in the UI that no longer matches the limit the
code enforces is worse than no limit at all - the user trusts it, hits the
real one, and the error contradicts the page they just read. So the row cap,
file-size cap, accepted extensions, frequencies and minimum history all come
from storage.py and series.py directly.

The template is a real, trainable file rather than a header row: a client
who uploads it unchanged should see the whole application work, which both
demonstrates the shape and proves their install is sound before they invest
effort in preparing their own export.
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from app.services.datasets import series as series_builder
from app.services.datasets import storage

# Two weeks short of a year, across three groups: enough for every model in
# the portfolio to be eligible (Exponential Smoothing needs two full seasonal
# cycles inside the smallest CV fold) without making the download large.
TEMPLATE_DAYS = 365
TEMPLATE_START = date(2025, 1, 1)

# Two regions x two departments = four series. Deliberately a small
# combination rather than a flat list: it is the shape most operational
# exports actually have, and it demonstrates that grouping by more than one
# column produces one series per real pairing.
TEMPLATE_REGIONS = ("North Region", "South Region")
TEMPLATE_DEPARTMENTS = ("Food & Nutrition", "Retail & Cafe")

TEMPLATE_COLUMNS = [
    ("business_date", "required", "The time axis. One row per period, per combination."),
    ("region", "group", "A dimension to split by. Combined with department below."),
    ("department", "group", "The second dimension. 2 regions x 2 departments = 4 separate forecasts."),
    ("hours_required", "required", "The value to forecast. The only column that must be numeric and present."),
    ("hours_rostered", "capacity", "What was already planned. Unlocks Staffing Impact and gap recommendations."),
    ("headcount_scheduled", "capacity", "An alternative planned measure - pick whichever you actually roster by."),
    ("occupied_beds", "driver", "A demand signal. Whatever drives your volume."),
    ("admissions", "driver", "Another demand signal."),
    ("meals_served", "driver", "Throughput measured alongside the target."),
    ("transactions", "driver", "Till or order count, if you have one."),
    ("is_holiday", "driver", "Flag. Use 0/1, not Y/N."),
    ("is_promotion", "driver", "Flag for a campaign or event running that period."),
]

# What each role means, shown in the UI so the roles are self-explanatory.
ROLE_LABELS = {
    "required": ("Required", "Every file needs these two."),
    "group": ("Splits into series", "The dimensions you want separate forecasts for. Each combination becomes one."),
    "capacity": ("Planned", "Compared against the forecast to show over/under-coverage. Never a model input."),
    "driver": ("Trains the model", "Fed to the models as an input to help explain the target."),
}


def requirements() -> dict:
    """Everything the upload panel needs to describe what is accepted."""
    grains = {
        value: series_builder.grain_for(value)
        for value in (f["value"] for f in storage.FREQUENCIES)
    }
    return {
        "accepted_extensions": sorted(storage.ALLOWED_EXTENSIONS),
        "max_file_mb": storage.MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_rows": storage.MAX_ROWS,
        "max_series": storage.MAX_GROUP_SERIES,
        "min_rows_per_series": series_builder.MIN_ROWS_PER_SERIES,
        "frequencies": [
            {
                "value": f["value"],
                "label": f["label"],
                "default_horizon": grains[f["value"]]["default_horizon"],
            }
            for f in storage.FREQUENCIES
        ],
        "aggregations": storage.AGGREGATIONS,
        "columns": [
            {
                "name": name,
                "role": role,
                "role_label": ROLE_LABELS[role][0],
                "purpose": purpose,
                "required": role == "required",
            }
            for name, role, purpose in TEMPLATE_COLUMNS
        ],
        "roles": [
            {"role": role, "label": label, "explanation": explanation}
            for role, (label, explanation) in ROLE_LABELS.items()
        ],
        "example_series_count": len(TEMPLATE_REGIONS) * len(TEMPLATE_DEPARTMENTS),
        "example_combination": (
            f"{len(TEMPLATE_REGIONS)} regions x {len(TEMPLATE_DEPARTMENTS)} departments "
            f"= {len(TEMPLATE_REGIONS) * len(TEMPLATE_DEPARTMENTS)} separate forecasts"
        ),
        "notes": [
            "Column names do not matter - they are read and mapped for you, and you confirm before training.",
            "Group by the dimensions you want separate forecasts for - every other column is used to train the models.",
            "Grouping by more than one column gives one forecast per combination that actually occurs in your data.",
            "Several rows in the same period are combined, by sum or average, whichever you choose.",
            "Gaps in the dates are fine; the series is put on a regular grid before training.",
            "One timestamp column, not a separate date and hour - otherwise hourly rows collapse into daily totals.",
        ],
    }


def _seasonal_value(day_index: int, weekday: int, base: float) -> float:
    """A plausible demand curve: weekly rhythm plus a slow annual swing."""
    import math

    weekly = 1.15 if weekday < 5 else 0.72
    annual = 1 + 0.18 * math.sin(2 * math.pi * day_index / 365)
    # A fixed wobble rather than a random one, so the file is identical on
    # every download - two people comparing templates should see the same
    # numbers, and a re-download should not retrain to a different result.
    wobble = 1 + 0.05 * math.sin(day_index * 1.7 + weekday)
    return base * weekly * annual * wobble


def template_rows() -> list[dict]:
    """A year of daily rows for every region x department pairing."""
    holidays = {date(2025, 1, 1), date(2025, 4, 18), date(2025, 5, 26), date(2025, 12, 25), date(2025, 12, 26)}
    rows = []
    for region_index, region in enumerate(TEMPLATE_REGIONS):
        for dept_index, department in enumerate(TEMPLATE_DEPARTMENTS):
            # Each combination gets its own level and its own driver mix, so
            # the four series are genuinely different to forecast rather
            # than four copies of one curve.
            base = 34 + region_index * 18 + dept_index * 9
            is_catering = department == TEMPLATE_DEPARTMENTS[0]
            for day_index in range(TEMPLATE_DAYS):
                day = TEMPLATE_START + timedelta(days=day_index)
                holiday = day in holidays
                required = _seasonal_value(day_index, day.weekday(), base)
                if holiday:
                    required *= 0.82
                promotion = (not is_catering) and (day_index % 23 == 0)
                if promotion:
                    required *= 1.14
                # Planned drifts from required on a weekly cycle, so the gap
                # analysis has a real pattern to find rather than noise.
                rostered = required * (1 + 0.13 * ((day_index % 7) - 3) / 3)
                beds = required * 2.9 * (1.0 if is_catering else 0.35)
                rows.append({
                    "business_date": day.isoformat(),
                    "region": region,
                    "department": department,
                            "hours_required": round(required, 1),
                    "hours_rostered": round(max(rostered, 1.0), 1),
                    "headcount_scheduled": max(1, round(rostered / 7.6)),
                    "occupied_beds": round(beds),
                    "admissions": max(0, round(beds / 11)),
                    "meals_served": round(required * 4.3) if is_catering else 0,
                    "transactions": 0 if is_catering else round(required * 9.1),
                    "is_holiday": 1 if holiday else 0,
                    "is_promotion": 1 if promotion else 0,
                })
    return rows


def template_csv() -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=[name for name, _, _ in TEMPLATE_COLUMNS])
    writer.writeheader()
    writer.writerows(template_rows())
    return buffer.getvalue()
