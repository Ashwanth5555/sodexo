"""Loads the Sodexo Healthcare synthetic workbook once at process startup and
serves it from memory. This is the ONLY place that touches the .xlsx file -
every router reads through the functions below.

Represents: "Representative synthetic Sodexo Healthcare account data exposed
through simulated source-system connectors." No real Sodexo, hospital,
patient, or employee data is involved.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import pandas as pd

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_DEFAULT_DATA_DIR)))
WORKBOOK_PATH = DATA_DIR / os.environ.get("SODEXO_WORKBOOK_FILENAME", "sodex.xlsx")

_lock = threading.Lock()
_cache: dict[str, pd.DataFrame] = {}

SHEETS = [
    "locations",
    "departments",
    "jobs",
    "job_skills",
    "staffing_standards",
    "employees",
    "hourly_census",
    "cafe_hourly_sales",
    "schedules",
    "punches",
    "attendance",
    "timecards",
    "pay_rates",
    "labor_budgets",
    "worker_skills",
    "holidays",
]


def load_all() -> None:
    with _lock:
        if _cache:
            return
        xl = pd.ExcelFile(WORKBOOK_PATH)
        for name in SHEETS:
            _cache[name] = xl.parse(name)


def get_sheet(name: str) -> pd.DataFrame:
    if not _cache:
        load_all()
    return _cache[name].copy()


def _apply_date_filter(df: pd.DataFrame, col: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    if col not in df.columns:
        return df
    if start_date:
        df = df[df[col] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df[col] <= pd.Timestamp(end_date)]
    return df


def query(
    sheet: str,
    *,
    date_col: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    filters: Optional[dict] = None,
    page: int = 1,
    page_size: int = 500,
) -> tuple[list[dict], int]:
    """Generic filtered/paginated read of a cached sheet. Returns (rows, total_count)."""
    df = get_sheet(sheet)

    if date_col:
        df = _apply_date_filter(df, date_col, start_date, end_date)

    if filters:
        for key, value in filters.items():
            if value is None:
                continue
            if key not in df.columns:
                continue
            df = df[df[key] == value]

    total = len(df)
    start = (page - 1) * page_size
    end = start + page_size
    page_df = df.iloc[start:end]

    records = page_df.to_dict(orient="records")
    for r in records:
        for k, v in list(r.items()):
            if isinstance(v, pd.Timestamp):
                r[k] = v.strftime("%Y-%m-%d") if v.hour == 0 and v.minute == 0 else v.isoformat()
            elif pd.isna(v):
                r[k] = None
    return records, total
