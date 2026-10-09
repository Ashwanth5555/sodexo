"""Work out what an uploaded file's columns mean.

The client uploads whatever they have - a depot roster, a till export, a
ward census - with column names nobody here has seen before. Something has
to decide which column is the time axis, which is the number to forecast,
which splits the file into series, and which (if any) records what was
already planned.

Two strategies, in order:

1. Ask an LLM. It reads column names, dtypes and a few sample values, and
   returns a mapping. A model recognises that "Shift Day" is a date and
   "Rostered Hours" is planned capacity, which no amount of substring
   matching reliably does.
2. Fall back to heuristics. If there is no API key, the call fails, or the
   answer does not survive validation, the profile-based guesses in
   storage.suggest_mapping still produce something usable. An upload must
   never fail because an external API was unavailable.

Whatever comes back is validated against the real column list before it is
offered, so a hallucinated column name becomes a fallback rather than a
confusing error later. The client still confirms the mapping either way -
this decides what the form is pre-filled with, not what is used.
"""
from __future__ import annotations

import json
import logging
import re

import pandas as pd

from app.core.config import get_settings
from app.services.datasets import series as series_builder
from app.services.datasets import storage

logger = logging.getLogger(__name__)

# Enough for the model to see what a column holds without sending the file.
SAMPLE_VALUES = 5
# The mapping call is small and on the upload path, where the client is
# watching a spinner. Better to fall back than to make them wait.
LLM_TIMEOUT_SECONDS = 25

_SYSTEM_PROMPT = """You map the columns of an uploaded data file onto a time series so it can be forecast.

You are given each column's name, dtype, null fraction, distinct count and a few sample
values. Return which column plays each role.

ROLES
- date_column: the time axis. Must be a column whose values are dates or timestamps.
  When several columns are date-like, choose the FINEST one - the one with the most
  distinct values. A file with both `business_date` (one value per day) and `date` (one
  per hour) is hourly data, and picking the daily column silently collapses 24 rows into
  one. Never pick a date column alongside a separate hour-of-day column and expect the
  hour to be used; it will not be.
- target_column: the numeric quantity to forecast. This is the thing the user cares about
  predicting - demand, hours required, volume, sales, headcount needed. Prefer a measure
  over an identifier, a code, or a running total.
- group_columns: OPTIONAL list. The BUSINESS DIMENSIONS the user would want forecast
  separately (region, department, site, depot, product line). A `source_sheet` column
  means the workbook had one sheet per series, so it is almost always the grouping. Return SEVERAL when the file
  is organised by more than one at once - a file with `region` and `department` should
  return both, giving one series per real region-and-department pairing.
  Return the SMALLEST set that captures the real dimensions. Do not add a column that is
  just a relabelling of the others: if every `site` value belongs to exactly one
  region-and-department pair, `site` splits nothing further and only makes the names
  longer. Return [] if the file is a single series. Never pick a column with one distinct
  value, or one where nearly every row differs.
  Every column you do NOT list here is available to train the models.
- capacity_column: OPTIONAL. A numeric column recording what was already PLANNED, scheduled,
  rostered, budgeted or available - as opposed to what was needed or what happened. This is
  compared against the forecast to show over- and under-coverage. Null if no such column
  exists. Do NOT pick a second demand measure here: it must mean "planned/available", not
  "another thing that was consumed".
- driver_columns: numeric columns that plausibly INFLUENCE the target and would be known in
  advance or measured alongside it (volumes, counts, flags, weather, prices). Exclude the
  target itself, identifiers, and anything derived from the target.
  CRITICAL: exclude EVERY column that records what was planned, scheduled, rostered,
  budgeted or staffed - not just the one you chose as capacity_column. A plan is made in
  response to expected demand, so a model given it can predict demand from the plan built
  for that demand, which looks accurate and is worthless. If a file has both
  `hours_rostered` and `headcount_scheduled`, pick one as capacity and leave the other out
  of driver_columns entirely.
- frequency: one of H, D, W, MS - the natural spacing of the observations.
- aggregation: sum or mean - how to combine several rows landing in the same period. Use sum
  for counts, volumes and hours; mean for rates, levels, temperatures and percentages.

RULES
1. Use ONLY column names exactly as given. Never invent, correct or reformat a name.
2. If you are not confident about an optional role, return null for it. A wrong guess costs
   the user more than an empty field.
3. reasoning: one short sentence on why the target and capacity choices are right.

Return strict JSON only:
{"date_column": str, "target_column": str, "group_columns": [str],
 "capacity_column": str|null, "driver_columns": [str], "frequency": "H"|"D"|"W"|"MS",
 "aggregation": "sum"|"mean", "reasoning": str}"""


def _column_digest(frame: pd.DataFrame, profile: list[dict]) -> list[dict]:
    """What the model sees: structure and a few values, never the whole file."""
    digest = []
    for entry in profile:
        name = entry["name"]
        samples = entry.get("samples") or []
        if len(samples) < SAMPLE_VALUES and name in frame.columns:
            samples = [str(v) for v in frame[name].dropna().head(SAMPLE_VALUES).tolist()]
        digest.append({
            "name": name,
            "dtype": entry.get("dtype"),
            "is_numeric": entry.get("is_numeric"),
            "looks_like_dates": entry.get("is_date_like"),
            "distinct": entry.get("distinct"),
            "null_pct": entry.get("null_pct"),
            "samples": samples[:SAMPLE_VALUES],
        })
    return digest


def _ask_llm(frame: pd.DataFrame, profile: list[dict]) -> dict | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None

    payload = {
        "row_count": len(frame),
        "columns": _column_digest(frame, profile),
    }
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=LLM_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=settings.openai_model,
            # Deterministic: the same file should map the same way every
            # time, or two clients comparing notes see different defaults.
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
        )
        return json.loads(response.choices[0].message.content)
    except Exception as exc:
        # Any failure here is recoverable - the heuristics still work.
        logger.warning("LLM column mapping unavailable, falling back to heuristics: %s", exc)
        return None


# A column the file's author named "target" outranks anything we can infer.
_TARGET_NAME = re.compile(r"(?:^|[^a-z])target(?:[^a-z]|$)", re.I)
# Names that read as something measured rather than something coded.
_MEASURE_WORDS = ("amount", "value", "qty", "quantity", "count", "sales", "revenue", "demand",
                  "volume", "hours", "manpower", "headcount", "units", "total", "served")


def _partitions_the_timeline(column: str, frame, date_column: str) -> bool:
    """Does this column cut the timeline into stretches rather than split the business?

    A real dimension - region, department, site - co-exists with its siblings:
    every date carries a row for each of them. A calendar label - Season,
    Quarter, Shift Week, Month Name - takes its turn and comes back round, so
    each of its values owns a scattered handful of dates and none of them
    overlap.

    Grouping on one of those is quietly destructive: it cuts one continuous
    series into four seasonal ones, each with three quarters of its calendar
    missing, and the gaps are then interpolated as though the days had simply
    not been recorded. A client file with a Season column produced sixteen
    series where four were meant.

    Such a column is still useful - it goes through as a category driver and
    is one-hot encoded - which is where seasonality belongs.
    """
    dates = pd.to_datetime(frame[date_column], errors="coerce")
    values = frame[column].astype(str)
    usable = dates.notna() & frame[column].notna()
    if not usable.any():
        return False
    days = dates[usable].dt.normalize()
    if values[usable].groupby(days).nunique().mean() > 1.05:
        return False  # The values co-exist on the same dates: a real dimension.

    # One value per date, so the question is whether the values take turns.
    # A file that simply reports one site and then the next gives each value
    # a single unbroken run; a calendar label hands over and comes back, so
    # there are more runs than there are values. Measured this way a single
    # year of Seasons is caught - Winter opens the year and closes it - which
    # a coverage ratio alone misses, because Spring really does own every day
    # between March and May.
    ordered = values[usable].iloc[days.argsort().to_numpy()]
    runs = int((ordered != ordered.shift()).sum())
    distinct = int(ordered.nunique())
    if runs > distinct:
        return True

    # And, over a longer file, a value whose own span is full of holes.
    coverage = []
    for _, block in days.groupby(values[usable]):
        span = (block.max() - block.min()).days + 1
        coverage.append(block.nunique() / max(span, 1))
    return bool(coverage) and sum(coverage) / len(coverage) < 0.8


def _covers_every_group(column: str, frame: pd.DataFrame, group_columns: list[str]) -> bool:
    """Does this column hold a number somewhere in every series?"""
    values = pd.to_numeric(frame[column], errors="coerce")
    if not group_columns:
        return bool(values.notna().any())
    return bool(values.notna().groupby([frame[c] for c in group_columns]).any().all())


def _repair_target(
    target: str,
    frame: pd.DataFrame,
    group_columns: list[str],
    numeric: set[str],
    profile: list[dict],
) -> str:
    """Hold the target to what the file can actually support.

    A file covering several departments carries each one's own measures, so a
    column can be a perfectly good measure and still be blank for most of the
    file. Chosen as the target it yields series with nothing in them, and the
    run dies at training on an empty frame - the failure lands far from the
    decision that caused it, and reads as a broken upload.

    Two rules, both decided here rather than in the prompt, because a mapping
    that has to be right every time cannot depend on what a model returns:
    an explicit name wins, and whatever wins has to cover every series.
    """
    named = [e["name"] for e in profile if e["name"] in numeric and _TARGET_NAME.search(e["name"])]
    if len(named) == 1 and named[0] != target:
        logger.info("Using '%s' as the target: the column name says so.", named[0])
        return named[0]

    if _covers_every_group(target, frame, group_columns):
        return target

    # Prefer a replacement that reads as a measure; fall back to the one with
    # the most distinct values, which beats a flag or a code.
    candidates = [
        e for e in profile
        if e["name"] in numeric and e["name"] != target and _covers_every_group(e["name"], frame, group_columns)
    ]
    if not candidates:
        return target
    candidates.sort(
        key=lambda e: (any(w in e["name"].lower() for w in _MEASURE_WORDS), e.get("distinct") or 0),
        reverse=True,
    )
    logger.info(
        "Replacing target '%s' with '%s': '%s' is empty for some of %s.",
        target, candidates[0]["name"], target, group_columns or "the file",
    )
    return candidates[0]["name"]


def _validate(proposal: dict, frame: pd.DataFrame, profile: list[dict]) -> dict | None:
    """Keep only what the file can actually support.

    A model naming a column that does not exist, or calling a text column
    numeric, must not reach the form. Required roles failing validation
    discards the whole proposal; optional roles are simply dropped.
    """
    names = {entry["name"] for entry in profile}
    numeric = {entry["name"] for entry in profile if entry.get("is_numeric")}
    by_name = {entry["name"]: entry for entry in profile}

    date_column = proposal.get("date_column")
    target_column = proposal.get("target_column")
    if date_column not in names or target_column not in numeric or date_column == target_column:
        logger.warning(
            "Discarding LLM mapping: date=%r target=%r not usable against the real columns.",
            date_column, target_column,
        )
        return None

    # The date column has to actually parse, whatever the model believed.
    parsed = pd.to_datetime(frame[date_column], errors="coerce")
    if parsed.notna().mean() < 0.8:
        logger.warning("Discarding LLM mapping: '%s' does not parse as dates.", date_column)
        return None

    def _optional(role: str, must_be_numeric: bool) -> str | None:
        value = proposal.get(role)
        if not value or value not in names:
            return None
        if must_be_numeric and value not in numeric:
            return None
        if value in (date_column, target_column):
            return None
        return value

    group_columns = []
    for candidate in (proposal.get("group_columns") or []):
        if candidate not in names or candidate in (date_column, target_column):
            continue
        distinct = by_name[candidate].get("distinct") or 0
        # One group is not a grouping, and one group per row is an id.
        if not (2 <= distinct <= storage.MAX_GROUP_SERIES):
            continue
        # Skip a level that splits nothing further. A `site` column whose
        # every value sits inside one region-and-department pair produces
        # the same series with longer names, and costs the user a column
        # they could have trained on instead.
        if group_columns:
            before = frame[group_columns].drop_duplicates().shape[0]
            after = frame[group_columns + [candidate]].drop_duplicates().shape[0]
            if after <= before:
                logger.info("Dropping group column '%s': it adds no further split.", candidate)
                continue
        if date_column and _partitions_the_timeline(candidate, frame, date_column):
            logger.info(
                "Dropping group column '%s': its values take turns over the calendar rather than "
                "co-existing, so grouping on it would cut one series into stretches. It stays as a "
                "driver instead.", candidate,
            )
            continue
        group_columns.append(candidate)

    planned_words = ("roster", "scheduled", "planned", "budget", "allocated", "staffed",
                     "capacity", "available", "fte", "headcount")

    capacity_column = _optional("capacity_column", must_be_numeric=True)
    if capacity_column in group_columns:
        capacity_column = None
    # Capacity has to mean "what was planned". A model asked for one will
    # offer the nearest big number when the file has no roster in it at all -
    # here, a ward's bed census - and Staffing Impact then charts demand
    # against occupied beds and calls the difference over-coverage. No
    # capacity column is right when the file records none; the client can
    # still set one by hand if they name theirs something we do not know.
    if capacity_column and not any(w in capacity_column.lower() for w in planned_words):
        logger.info("Dropping capacity column '%s': it does not read as a planned measure.", capacity_column)
        capacity_column = None

    reserved = {date_column, target_column, capacity_column, *group_columns}
    # Belt and braces on the leak the prompt warns about. A model that
    # ignores the instruction, or a file with a planned measure under a name
    # it did not recognise, must not slip a roster into the inputs.
    drivers = []
    for candidate in (proposal.get("driver_columns") or []):
        if candidate in reserved:
            continue
        if candidate not in numeric and not _is_category(candidate, frame):
            continue
        if any(word in candidate.lower() for word in planned_words):
            logger.info("Dropping '%s' from drivers: it reads as a planned measure.", candidate)
            continue
        drivers.append(candidate)

    # Text columns naming a handful of repeating states - a season, a shift
    # pattern, a weather band - are added whether or not the model listed
    # them. They are expanded into indicator columns at training time, and
    # before that they were simply dropped: a client could see `season` in
    # their file and reasonably assume the models had read it.
    for entry in profile:
        name = entry["name"]
        if name in reserved or name in drivers or name in numeric:
            continue
        if _is_category(name, frame) and not any(w in name.lower() for w in planned_words):
            drivers.append(name)

    # Frequency is checked against the actual spacing of the dates, not
    # just against the list of allowed values. A model looking at a daily
    # file returned "H", and hourly resampling of daily data manufactures
    # 23 interpolated rows for every real one - a mistake that produces
    # excellent-looking accuracy on almost entirely invented data.
    frequency = proposal.get("frequency")
    observed = storage.infer_frequency(frame, date_column)
    if frequency not in storage.FREQUENCY_VALUES:
        frequency = observed
    elif frequency != observed:
        logger.info(
            "Overriding proposed frequency %r with %r, which is what the date spacing shows.",
            frequency, observed,
        )
        frequency = observed
    aggregation = proposal.get("aggregation")
    if aggregation not in ("sum", "mean"):
        aggregation = "sum"

    return {
        "date_column": date_column,
        "target_column": target_column,
        "group_columns": group_columns,
        "capacity_column": capacity_column,
        "driver_columns": drivers,
        "frequency": frequency,
        "aggregation": aggregation,
        "reasoning": str(proposal.get("reasoning") or "")[:400],
        "source": "llm",
    }


def _heuristic(frame: pd.DataFrame, profile: list[dict]) -> dict:
    """The no-LLM path: column profiling and name matching."""
    suggestion = storage.suggest_mapping(profile, len(frame))
    date_column = suggestion["date_column"]
    target_column = suggestion["target_column"]

    numeric = [
        entry["name"] for entry in profile
        if entry.get("is_numeric") and not entry.get("is_date_like")
        and entry["name"] not in (date_column, target_column)
    ]
    # Same capacity words the companion mapper used, kept because they are
    # the vocabulary these exports actually use.
    capacity_words = ("scheduled", "roster", "planned", "capacity", "budget", "available",
                      "staffed", "headcount", "fte", "allocated")
    capacity_column = next((c for c in numeric if any(w in c.lower() for w in capacity_words)), None)

    drivers = [c for c in numeric if c != capacity_column]

    frequency = storage.infer_frequency(frame, date_column) if date_column else "D"
    # The heuristic proposes at most one grouping column: without an LLM
    # there is no reliable way to tell which of several low-cardinality
    # labels are meant to be combined, and guessing wrong multiplies the
    # series count.
    candidates = suggestion.get("group_candidates") or []
    return {
        "date_column": date_column,
        "target_column": target_column,
        "group_columns": candidates[:1],
        "capacity_column": capacity_column,
        "driver_columns": drivers,
        "frequency": frequency,
        "aggregation": "sum",
        "reasoning": "Detected from column names, types and value patterns.",
        "source": "heuristic",
        "group_candidates": suggestion.get("group_candidates", []),
    }


def _force_sheet_grouping(proposal: dict, frame: pd.DataFrame) -> dict:
    """A multi-sheet workbook is grouped by its sheets, not merged.

    Separate sheets mean the client already separated those series by hand.
    Merging them would sum a cafe's transactions into a kitchen's meal
    counts under one target, which is not a forecast of anything.

    Enforced in code rather than left to the prompt: the model saw the
    instruction and still returned an empty grouping for a four-sheet
    workbook, and the cost of getting this wrong is silent nonsense rather
    than a visible error.
    """
    sheet_columns = [c for c in frame.columns if c in frame.attrs.get("sheet_dimension_columns", [])]
    if storage.SHEET_COLUMN in frame.columns:
        sheet_columns = [storage.SHEET_COLUMN]
    if not sheet_columns:
        return proposal

    groups = list(proposal.get("group_columns") or [])
    missing = [c for c in sheet_columns if c not in groups]
    if missing:
        proposal["group_columns"] = missing + groups
        note = (
            "Grouped by the dimensions in the sheet names, because the workbook keeps each "
            "series on its own sheet."
        )
        reasoning = proposal.get("reasoning") or ""
        proposal["reasoning"] = f"{note} {reasoning}".strip()
    # Structural columns, never model inputs.
    proposal["driver_columns"] = [c for c in proposal.get("driver_columns") or [] if c not in sheet_columns]
    return proposal


def _is_category(column: str, frame: pd.DataFrame) -> bool:
    """A text column the models can actually use once encoded."""
    if column not in frame.columns:
        return False
    return series_builder.is_encodable_category(frame[column], len(frame))


def _apply_target_guards(proposal: dict, frame: pd.DataFrame, profile: list[dict]) -> dict:
    """Last word on the target, on both the LLM and the heuristic path.

    Runs after the grouping is settled, because whether a target works depends
    on which series the file is about to be cut into.
    """
    numeric = {e["name"] for e in profile if e.get("is_numeric")}
    target = proposal.get("target_column")
    if target not in numeric:
        return proposal
    groups = proposal.get("group_columns") or []
    repaired = _repair_target(target, frame, groups, numeric, profile)
    if repaired == target:
        return proposal
    proposal["target_column"] = repaired
    # The new target must not survive in any other role - as a driver it
    # would predict itself, which scores perfectly and forecasts nothing.
    proposal["group_columns"] = [c for c in groups if c != repaired]
    proposal["driver_columns"] = [c for c in (proposal.get("driver_columns") or []) if c != repaired]
    if proposal.get("capacity_column") == repaired:
        proposal["capacity_column"] = None
    return proposal


def _grouping_of_last_resort(proposal: dict, candidates: list[str], frame, profile: list[dict]) -> dict:
    """Take the obvious split when the model proposed none at all.

    A file of two budget lines reported every month came back ungrouped, so
    both were summed into one series and the client saw a single total where
    they keep two. The missed split is the invisible mistake - one series
    looks like a deliberate answer - while an unwanted one is right there on
    the mapping card with a chip to switch it off.

    Only ever one column, only when nothing was proposed, and only a column
    whose values co-exist date by date, so this cannot reintroduce the
    calendar-label grouping the guard above removes.
    """
    if proposal.get("group_columns"):
        return proposal
    date_column = proposal.get("date_column")
    reserved = {date_column, proposal.get("target_column"), proposal.get("capacity_column")}
    for name in candidates:
        if name in reserved or name not in frame.columns:
            continue
        if date_column and _partitions_the_timeline(name, frame, date_column):
            continue
        logger.info("No grouping proposed; taking '%s', which repeats on every date.", name)
        proposal["group_columns"] = [name]
        proposal["driver_columns"] = [
            c for c in (proposal.get("driver_columns") or []) if c != name
        ]
        break
    return proposal


def propose(frame: pd.DataFrame, profile: list[dict]) -> dict:
    """The mapping to pre-fill the form with. Never raises."""
    heuristic = _force_sheet_grouping(_heuristic(frame, profile), frame)

    raw = _ask_llm(frame, profile)
    validated = _validate(raw, frame, profile) if raw is not None else None
    if validated is None:
        return _apply_target_guards(heuristic, frame, profile)

    validated = _force_sheet_grouping(validated, frame)
    # Group candidates come from profiling either way - the client may want
    # a different split than the model chose, and the dropdown needs options.
    candidates = heuristic.get("group_candidates", [])
    validated = _grouping_of_last_resort(validated, candidates, frame, profile)
    if storage.SHEET_COLUMN in frame.columns and storage.SHEET_COLUMN not in candidates:
        candidates = [storage.SHEET_COLUMN, *candidates]
    validated["group_candidates"] = candidates
    return _apply_target_guards(validated, frame, profile)
