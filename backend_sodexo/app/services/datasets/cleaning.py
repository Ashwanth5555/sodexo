"""Turn a real-world spreadsheet into something forecastable.

A file exported from a finance or operations system is rarely a clean
rectangle. It carries a title and a run date above the header, a totals row
at the foot, currency symbols in the numbers, a "Notes" sheet beside the
data, and very often it is pivoted - one column per month rather than one
row per month.

Every one of those broke the upload before this module existed, and broke it
quietly: the header row became data, the first text cell became the "date"
column, and the client got a confusing mapping form instead of an error they
could act on.

Nothing here guesses silently. Each transformation records what it did, and
that record is shown on the Data Quality page, so a client can always see
that the header was taken from row 4 and a totals row was dropped.
"""
from __future__ import annotations

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

# How far down to look for the real header. Beyond this a file is not a
# report with a preamble, it is something we should not be guessing about.
MAX_HEADER_SCAN = 12
# A sheet with fewer rows than this is a note, a cover page or a legend -
# not a series anybody wants forecast.
MIN_DATA_ROWS = 5
# Reshaping needs enough date-like headers to be sure. Three columns that
# happen to parse as dates is coincidence; eight is a pivot table.
MIN_WIDE_COLUMNS = 6

_CURRENCY = re.compile(r"[^\d\-+.,eE]")
_TOTAL_ROW = re.compile(r"^\s*(grand\s+)?(total|totals|sum|subtotal|average|avg)\s*:?\s*$", re.I)


class Report:
    """What cleaning did, in words a client can check."""

    def __init__(self) -> None:
        self.notes: list[str] = []

    def add(self, note: str) -> None:
        self.notes.append(note)

    def __bool__(self) -> bool:
        return bool(self.notes)


def _looks_like_header(row: pd.Series, below: pd.DataFrame) -> bool:
    """A header row names things; the rows under it hold values.

    Judged on the row itself being mostly non-empty text, and on the block
    beneath it being wider than a stray title cell.
    """
    values = [str(v).strip() for v in row.tolist() if str(v).strip() and str(v) != "nan"]
    if len(values) < 2:
        return False
    # A title line is one long sentence in one cell; a header is several
    # short labels across several cells.
    filled_ratio = len(values) / max(1, len(row))
    if filled_ratio < 0.5:
        return False
    if below.empty:
        return False
    # Headers are rarely numeric.
    numeric_headers = sum(1 for v in values if _is_number(v))
    return numeric_headers <= len(values) / 2


def _parses_as_dates(text: pd.Series) -> bool:
    """Whether a text column is really dates."""
    sample = text.head(80)
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        parsed = pd.to_datetime(sample, errors="coerce")
    return bool(parsed.notna().mean() >= 0.8)


def _is_number(value: str) -> bool:
    try:
        float(_CURRENCY.sub("", value).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


def find_header_row(raw: pd.DataFrame) -> int:
    """Index of the row holding the column names, 0 if it is already first.

    Reads the sheet with no header at all and looks for the first row that
    reads like labels with data underneath. A report with a title and a
    generated-on line puts the real header on row 3 or 4, and taking row 0
    makes the title the name of every column.
    """
    limit = min(MAX_HEADER_SCAN, len(raw))
    for index in range(limit):
        if _looks_like_header(raw.iloc[index], raw.iloc[index + 1: index + 4]):
            return index
    return 0


def drop_total_rows(frame: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Remove a trailing Total/Subtotal/Average row.

    It is not an observation, and left in place it becomes a period with an
    unreadable date that inflates the rejected-row count, or worse a real
    period carrying the sum of every other one.
    """
    if frame.empty:
        return frame
    text_columns = [c for c in frame.columns if frame[c].dtype == object]
    if not text_columns:
        return frame
    mask = pd.Series(False, index=frame.index)
    for column in text_columns:
        mask |= frame[column].astype(str).str.match(_TOTAL_ROW, na=False)
    if mask.any():
        report.add(f"Removed {int(mask.sum())} total or subtotal row(s).")
        return frame.loc[~mask].reset_index(drop=True)
    return frame


def coerce_numeric_text(frame: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Turn "£1,234.50" and "1 234,5" into numbers.

    Only applied where nearly every value in the column converts. A column
    of genuine text must not be mangled into mostly-blank numbers just
    because a few entries happen to look numeric.
    """
    converted = []
    for column in frame.columns:
        series = frame[column]
        if series.dtype != object:
            continue
        text = series.dropna().astype(str)
        if text.empty:
            continue
        # Never touch a column that reads as dates. The symbol strip removes
        # separators indiscriminately, so "01/01/2024" would become the
        # number 1012024 and the file would lose its time axis entirely -
        # which is exactly what happened before this check existed.
        if _parses_as_dates(text):
            continue
        # Already-numeric text is handled fine downstream; this is for the
        # values that are not.
        if pd.to_numeric(text, errors="coerce").notna().mean() > 0.95:
            continue
        stripped = text.str.replace(_CURRENCY, "", regex=True).str.replace(",", "", regex=False)
        parsed = pd.to_numeric(stripped, errors="coerce")
        if parsed.notna().mean() > 0.95:
            frame[column] = pd.to_numeric(
                series.astype(str).str.replace(_CURRENCY, "", regex=True).str.replace(",", "", regex=False),
                errors="coerce",
            )
            converted.append(str(column))
    if converted:
        report.add(f"Read {', '.join(converted[:4])} as numbers by removing symbols and separators.")
    return frame


# The formats spreadsheet headers actually use for a period. pandas parses
# none of "Jan-24", "Jan 24" or "2024-01" on its own - to_datetime returns
# NaT for all three - so a pivot went completely undetected until these
# were tried explicitly.
_PERIOD_FORMATS = (
    "%b-%y", "%b %y", "%b-%Y", "%b %Y", "%B %Y", "%B-%Y",
    "%Y-%m", "%m/%Y", "%m-%Y", "%Y/%m",
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
)
_QUARTER = re.compile(r"^\s*Q([1-4])[\s\-/]*(\d{2,4})\s*$", re.I)


def parse_period_label(label: str):
    """A column header read as the period it names, or None."""
    text = str(label).strip()
    if not text or text.lower().startswith("unnamed"):
        return None
    # A bare number is a quantity, an id or a year by coincidence.
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return None

    quarter = _QUARTER.match(text)
    if quarter:
        q, year = int(quarter.group(1)), int(quarter.group(2))
        year += 2000 if year < 100 else 0
        return pd.Timestamp(year=year, month=(q - 1) * 3 + 1, day=1)

    for fmt in _PERIOD_FORMATS:
        try:
            return pd.Timestamp(pd.to_datetime(text, format=fmt))
        except (ValueError, TypeError):
            continue
    # Last resort: let pandas guess, for anything the list above missed.
    try:
        parsed = pd.to_datetime(text, errors="coerce")
        return None if pd.isna(parsed) else pd.Timestamp(parsed)
    except (ValueError, TypeError):
        return None


def _date_like_headers(columns) -> list:
    """Which column headers are themselves periods - the sign of a pivot."""
    return [c for c in columns if parse_period_label(c) is not None]


def unpivot_if_wide(frame: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Reshape one-column-per-period into one row per period.

    A pivoted export has its dates in the header: Jan-24, Feb-24, Mar-24 as
    columns with a region down the side. That is the normal shape for a
    finance report and unusable for forecasting, which needs a time axis in
    a column.

    Left alone when only a couple of headers parse as dates, since that is
    more likely a coincidence than a pivot.
    """
    date_columns = _date_like_headers(frame.columns)
    if len(date_columns) < MIN_WIDE_COLUMNS:
        return frame

    id_columns = [c for c in frame.columns if c not in date_columns]
    if not id_columns:
        # Every column is a date: there is nothing to identify a row by, so
        # the melt would produce a single unlabelled series. Still better
        # than leaving it wide.
        id_columns = []

    long = frame.melt(
        id_vars=id_columns,
        value_vars=date_columns,
        var_name="period",
        value_name="value",
    )
    # Map through the same parser that identified them, so "Jan-24" becomes
    # a real timestamp rather than NaT.
    long["period"] = long["period"].map(parse_period_label)
    long = long.dropna(subset=["period"])
    # A blank cell in a pivot means "nothing that month", not a gap to fill.
    long = long.dropna(subset=["value"])

    report.add(
        f"Reshaped {len(date_columns)} date columns ({date_columns[0]} to {date_columns[-1]}) "
        f"into one row per period, giving {len(long):,} rows."
    )
    return long.reset_index(drop=True)


def is_data_sheet(frame: pd.DataFrame) -> bool:
    """Whether a sheet holds a table rather than notes or a cover page.

    A README with one column of sentences is neither, and letting it
    through makes it a forecastable "series" of its own once sheets become
    the grouping.

    The row threshold is waived for a pivot. A wide export has one row per
    region and one column per month, so three rows and eighteen columns is
    a perfectly good table - judging it on row count alone rejected exactly
    the shape this module exists to handle.
    """
    if frame.empty:
        return False
    usable = frame.dropna(how="all").dropna(axis=1, how="all")
    if usable.shape[1] < 2:
        return False
    if usable.shape[0] >= MIN_DATA_ROWS:
        return True
    return len(_date_like_headers(usable.columns)) >= MIN_WIDE_COLUMNS


def clean(frame: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Everything that does not need the raw sheet: applied in order."""
    frame = frame.dropna(how="all").dropna(axis=1, how="all")
    frame = drop_total_rows(frame, report)
    frame = unpivot_if_wide(frame, report)
    frame = coerce_numeric_text(frame, report)
    return frame.reset_index(drop=True)


# --- sheet names that encode more than one dimension ------------------------
#
# A workbook often names its sheets by the dimensions they cut across:
# RAW_LOC01_FOOD_NUTRITION, RAW_LOC02_CAFE. Treated as one opaque label that
# is a single grouping column with four values, which forecasts correctly but
# hides the structure - the client thinks in "two sites by two departments",
# and the recommendations can only look for spare capacity across whatever
# dimensions actually exist as columns.
#
# Splitting them restores that structure, so region and department become
# real columns the client can group by, filter on and reason about.

_SPLIT_DELIMITERS = ("_", "-", "|", "/")


def _strip_common_tokens(parts: list[list[str]]) -> list[list[str]]:
    """Drop leading and trailing tokens that every sheet shares.

    "RAW_" on all four sheets is a naming convention, not a dimension: it
    has one value, so it splits nothing and only lengthens the labels.
    """
    while parts and all(len(p) > 1 for p in parts) and len({p[0] for p in parts}) == 1:
        parts = [p[1:] for p in parts]
    while parts and all(len(p) > 1 for p in parts) and len({p[-1] for p in parts}) == 1:
        parts = [p[:-1] for p in parts]
    return parts


def sheet_label_columns(frames: dict[str, pd.DataFrame]) -> list[str]:
    """Columns the sheets already carry that say which sheet a row came from.

    A workbook whose sheets each hold one site or one department often states
    that in a column as well as in the sheet name. Deriving dimensions from the
    names on top of those would give two columns per fact - region *and* a
    location split out of "RAW_LOC01_CAFE" - so the client would see four
    grouping columns holding two real dimensions.

    A label column is text, constant within every sheet, and takes a different
    combination in each: constant alone is not enough, since a flag that reads
    0 everywhere is constant too but identifies nothing.
    """
    if len(frames) < 2:
        return []
    shared = set.intersection(*(set(f.columns) for f in frames.values()))
    first = next(iter(frames.values()))
    labels = [
        name
        for name in first.columns
        if name in shared
        and not pd.api.types.is_numeric_dtype(first[name])
        and not pd.api.types.is_datetime64_any_dtype(first[name])
        and all(len(f) and f[name].nunique(dropna=False) == 1 for f in frames.values())
    ]
    if not labels:
        return []
    keys = {tuple(str(f[name].iloc[0]) for name in labels) for f in frames.values()}
    return labels if len(keys) == len(frames) else []


def split_sheet_names(names: list[str], max_dimensions: int = 3) -> list[dict] | None:
    """Decompose sheet names into the dimensions they encode.

    Returns one entry per dimension - {"values": {sheet: value}} - or None
    when the names do not decompose cleanly.

    Requires every dimension to vary and the combination to identify each
    sheet uniquely. Anything less and the split is a guess, and a wrong
    guess here silently changes what gets forecast.
    """
    if len(names) < 2:
        return None

    for delimiter in _SPLIT_DELIMITERS:
        if not all(delimiter in name for name in names):
            continue
        tokens = _strip_common_tokens([name.split(delimiter) for name in names])
        if not tokens or min(len(t) for t in tokens) < 2:
            continue

        # Token counts differ when one dimension's values contain the
        # delimiter ("FOOD_NUTRITION" vs "CAFE"). Splitting from the left a
        # fixed number of times and joining the remainder keeps the columns
        # aligned: the last dimension simply absorbs the extra tokens.
        for dimensions in range(2, max_dimensions + 1):
            if min(len(t) for t in tokens) < dimensions:
                continue
            columns = []
            for index in range(dimensions):
                if index == dimensions - 1:
                    values = [delimiter.join(t[index:]) for t in tokens]
                else:
                    values = [t[index] for t in tokens]
                columns.append(values)

            # Every dimension must vary, or it is not a dimension.
            if any(len(set(v)) < 2 for v in columns):
                continue
            # And together they must identify each sheet, or the split has
            # lost information and two sheets would collapse into one.
            combos = list(zip(*columns))
            if len(set(combos)) != len(names):
                continue
            return [{"values": dict(zip(names, values))} for values in columns]
    return None


def name_sheet_dimensions(columns: list[dict], sheet_names: list[str]) -> list[str]:
    """Ask the model what each decomposed dimension should be called.

    The values are codes - LOC01, FOOD_NUTRITION - and a column called
    "dimension_1" tells the client nothing. Falls back to positional names
    if there is no key or the call fails; the split is still correct either
    way, only the labels are less readable.
    """
    fallback = [f"dimension_{i + 1}" for i in range(len(columns))]
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.openai_api_key:
        return fallback

    samples = [sorted({str(v) for v in c["values"].values()})[:6] for c in columns]
    try:
        import json

        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=15)
        response = client.chat.completions.create(
            model=settings.openai_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sheet names in a workbook were split into dimensions. Give each a short, "
                        "lowercase, snake_case column name describing what its values are - for "
                        "example location, department, region, product_line, site. Return strict "
                        'JSON: {"names": [str, ...]} with exactly one name per dimension, in order.'
                    ),
                },
                {"role": "user", "content": json.dumps({"sheet_names": sheet_names[:12], "dimensions": samples})},
            ],
        )
        proposed = json.loads(response.choices[0].message.content).get("names") or []
        cleaned = []
        for index in range(len(columns)):
            name = str(proposed[index]).strip().lower() if index < len(proposed) else ""
            name = re.sub(r"[^a-z0-9_]+", "_", name).strip("_")
            cleaned.append(name or fallback[index])
        # Duplicate names would collapse into one column on assignment.
        if len(set(cleaned)) != len(cleaned):
            return fallback
        return cleaned
    except Exception as exc:
        logger.warning("Could not name sheet dimensions, using positional names: %s", exc)
        return fallback
