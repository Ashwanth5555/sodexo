"""Parsing, profiling and on-disk storage for user-uploaded datasets.

A dataset here is any CSV/Excel file the user wants to forecast - we do not
know its schema in advance, so instead of forcing it into a canonical table we
keep the parsed frame on disk as CSV and record a column profile the UI uses to
let the user say which column is the date, which is the value to forecast, and
which (optionally) splits the file into separate series.
"""
from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

import pandas as pd

from app.core.config import PROJECT_ROOT

UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"

CSV_EXTENSIONS = {".csv", ".txt", ".tsv"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
ALLOWED_EXTENSIONS = CSV_EXTENSIONS | EXCEL_EXTENSIONS

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Guard rails: enough for real series, small enough that a 13-model portfolio
# still finishes, and that one upload cannot exhaust the process.
MAX_ROWS = 500_000
MAX_GROUP_SERIES = 40

# Pandas offset aliases we support, in ascending period length.
FREQUENCIES = [
    {"value": "H", "label": "Hourly"},
    {"value": "D", "label": "Daily"},
    {"value": "W", "label": "Weekly"},
    {"value": "MS", "label": "Monthly"},
]
FREQUENCY_VALUES = {f["value"] for f in FREQUENCIES}

AGGREGATIONS = [
    {"value": "sum", "label": "Sum"},
    {"value": "mean", "label": "Average"},
]


class DatasetError(Exception):
    """A problem with the file or the mapping the user chose."""


def _read_frame(filename: str, raw: bytes) -> pd.DataFrame:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise DatasetError(f"Unsupported file type '{suffix or filename}'. Upload a .csv or .xlsx file.")
    try:
        if suffix in EXCEL_EXTENSIONS:
            return pd.read_excel(io.BytesIO(raw))
        # sep=None + the python engine sniffs comma/semicolon/tab automatically.
        return pd.read_csv(io.BytesIO(raw), sep=None, engine="python")
    except Exception as exc:
        raise DatasetError(f"Could not read the file: {exc}") from exc


def _dedupe_columns(columns: list[str]) -> list[str]:
    """Make column names unique and non-empty so later lookups are unambiguous."""
    seen: dict[str, int] = {}
    out = []
    for index, raw in enumerate(columns):
        name = str(raw).strip() or f"column_{index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def _looks_like_dates(series: pd.Series) -> bool:
    sample = series.dropna().head(80)
    if sample.empty:
        return False
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    # Pure numbers (ids, counts) parse as dates under some formats - exclude
    # them unless they look like 20240131-style stamps.
    if pd.api.types.is_numeric_dtype(series):
        return False
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        parsed = pd.to_datetime(sample, errors="coerce")
    return parsed.notna().mean() >= 0.8


def profile_columns(frame: pd.DataFrame) -> list[dict]:
    """Per-column facts the mapping UI needs to suggest sensible defaults."""
    total = len(frame)
    profile = []
    for name in frame.columns:
        column = frame[name]
        non_null = int(column.notna().sum())
        is_numeric = bool(pd.api.types.is_numeric_dtype(column))
        samples = [str(v) for v in column.dropna().head(3).tolist()]
        profile.append({
            "name": name,
            "dtype": str(column.dtype),
            "is_numeric": is_numeric,
            "is_date_like": bool(_looks_like_dates(column)),
            "null_pct": round((1 - (non_null / total)) * 100, 1) if total else 0.0,
            "distinct": int(column.nunique(dropna=True)),
            "samples": samples,
        })
    return profile


def suggest_mapping(profile: list[dict], row_count: int) -> dict:
    """Best-guess date/target/group columns, so the form opens pre-filled."""
    date_candidates = [c for c in profile if c["is_date_like"]]
    numeric = [c for c in profile if c["is_numeric"] and not c["is_date_like"]]

    date_column = date_candidates[0]["name"] if date_candidates else None

    # Prefer a numeric column whose name sounds like a measure; otherwise the
    # numeric column with the most distinct values (ids tend to be excluded
    # below, and a near-constant column makes a poor forecasting target).
    measure_words = ("amount", "value", "qty", "quantity", "count", "sales", "revenue",
                     "demand", "volume", "hours", "manpower", "total", "units", "price")
    named = [c for c in numeric if any(w in c["name"].lower() for w in measure_words)]
    id_like = [c for c in numeric if c["distinct"] >= max(1, int(row_count * 0.9))]
    ranked = named or [c for c in numeric if c not in id_like] or numeric
    target_column = ranked[0]["name"] if ranked else None

    # A grouping column splits the file into one series per value - useful when
    # it has a handful of repeated values, useless when nearly every row differs.
    group_candidates = [
        c for c in profile
        if not c["is_numeric"] and not c["is_date_like"] and 2 <= c["distinct"] <= MAX_GROUP_SERIES
    ]
    group_candidates.sort(key=lambda c: c["distinct"])

    return {
        "date_column": date_column,
        "target_column": target_column,
        "group_column": None,
        "group_candidates": [c["name"] for c in group_candidates],
    }


def infer_frequency(frame: pd.DataFrame, date_column: str) -> str:
    """Infer the sampling frequency from the gaps between observed dates."""
    dates = pd.to_datetime(frame[date_column], errors="coerce").dropna().sort_values().unique()
    if len(dates) < 3:
        return "D"
    gaps = pd.Series(pd.to_datetime(dates)).diff().dropna()
    if gaps.empty:
        return "D"
    median_hours = gaps.dt.total_seconds().median() / 3600
    if median_hours <= 2:
        return "H"
    if median_hours <= 48:
        return "D"
    if median_hours <= 24 * 10:
        return "W"
    return "MS"


def save_upload(filename: str, raw: bytes) -> tuple[str, pd.DataFrame, list[dict]]:
    """Parse, store and profile an upload. Returns (dataset_id, frame, profile)."""
    if not raw:
        raise DatasetError("The file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise DatasetError(f"File is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.")

    frame = _read_frame(filename, raw)
    frame.columns = _dedupe_columns(list(frame.columns))
    frame = frame.dropna(how="all")
    if frame.empty:
        raise DatasetError("The file has no data rows.")
    if len(frame) > MAX_ROWS:
        raise DatasetError(f"The file has {len(frame):,} rows; the limit is {MAX_ROWS:,}.")
    if len(frame.columns) < 2:
        raise DatasetError("The file needs at least two columns - a date and a value to forecast.")

    dataset_id = f"DS-{uuid.uuid4().hex[:12]}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(UPLOAD_DIR / f"{dataset_id}.csv", index=False)
    return dataset_id, frame, profile_columns(frame)


def load_frame(stored_filename: str) -> pd.DataFrame:
    path = UPLOAD_DIR / stored_filename
    if not path.exists():
        raise DatasetError(f"The stored file for this dataset is missing ({stored_filename}).")
    return pd.read_csv(path)


def delete_stored_file(stored_filename: str) -> None:
    path = UPLOAD_DIR / stored_filename
    if path.exists():
        path.unlink()


def safe_key_fragment(value: str, limit: int = 18) -> str:
    """A series-key-safe fragment of a group value (keys are a String(60))."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").upper()
    return (cleaned[:limit] or "ALL")
