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
from app.services.datasets import cleaning

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


# The column a multi-sheet workbook's sheet names land in. Named with a
# leading marker so it cannot silently collide with a column the client
# already has - a file with its own "sheet" column would otherwise have it
# overwritten.
SHEET_COLUMN = "source_sheet"


def _read_sheet(book: pd.ExcelFile, name: str, report: cleaning.Report) -> pd.DataFrame | None:
    """One sheet, with its real header found first.

    Read twice on purpose: once with no header to locate the row holding
    the column names, then again from that row. A report with a title and a
    run date above the table otherwise makes the title the name of column
    one and the header row becomes the first row of data.
    """
    probe = book.parse(name, header=None, nrows=cleaning.MAX_HEADER_SCAN + 4)
    header_row = cleaning.find_header_row(probe)
    sheet = book.parse(name, header=header_row)
    if header_row:
        report.add(f"Header taken from row {header_row + 1}; the rows above it were a title or preamble.")
    if not cleaning.is_data_sheet(sheet):
        return None
    return sheet


def _read_excel(raw: bytes) -> pd.DataFrame:
    """Read a workbook, combining every sheet into one frame.

    Operational exports very often put one series per sheet - one per site,
    per department, per product line. Reading only the first sheet, which is
    what pandas does by default, silently discards the rest: a four-sheet
    workbook would come in as a quarter of the data with no warning at all.

    So every sheet is read and concatenated, with the sheet name kept in
    SHEET_COLUMN. That makes the sheet name available as a grouping column,
    which is what it almost always means.

    Sheets need not share a schema. A catering sheet carrying `meals_served`
    and a retail sheet carrying `transaction_count` union into one frame with
    blanks where each does not apply, and the per-series build drops the
    columns that are empty for that series.
    """
    book = pd.ExcelFile(io.BytesIO(raw))
    report = cleaning.Report()

    kept: list[tuple[str, pd.DataFrame]] = []
    skipped: list[str] = []
    for name in book.sheet_names:
        sheet = _read_sheet(book, name, report)
        if sheet is None:
            skipped.append(str(name))
            continue
        kept.append((str(name), sheet))

    if not kept:
        raise DatasetError(
            "No sheet in this workbook holds a table. Each sheet needs a header row and "
            "at least a few rows of data beneath it."
        )
    if skipped:
        # Notes, cover pages and legends. Naming them matters because the
        # sheet name becomes the grouping, so a silently-kept README would
        # appear as a series the client never asked to forecast.
        report.add(f"Ignored {len(skipped)} sheet(s) holding no table: {', '.join(skipped[:4])}.")

    dimension_names: list[str] = []
    if len(kept) == 1:
        frame = cleaning.clean(kept[0][1], report)
    else:
        cleaned = {name: cleaning.clean(sheet, report) for name, sheet in kept}
        sheet_names = list(cleaned)

        # If the sheets already carry the columns that tell them apart, use
        # those. Splitting the names as well would hand the client two columns
        # for every real dimension.
        dimension_names = cleaning.sheet_label_columns(cleaned)
        if dimension_names:
            report.add(
                f"Each sheet is one {' + '.join(dimension_names)}, "
                f"read from the columns already in the file."
            )
            frames = list(cleaned.values())
        else:
            # Sheet names usually encode the dimensions they cut across, and
            # splitting them turns one opaque label into the real columns the
            # client thinks in - two sites by two departments rather than four
            # sheet names.
            dimensions = cleaning.split_sheet_names(sheet_names)
            dimension_names = cleaning.name_sheet_dimensions(dimensions, sheet_names) if dimensions else []
            if dimensions:
                report.add(
                    f"Split the sheet names into {', '.join(dimension_names)} - "
                    f"{' x '.join(str(len(set(d['values'].values()))) for d in dimensions)} combinations."
                )

            frames = []
            for name, sheet in cleaned.items():
                if dimensions:
                    for column, dimension in zip(dimension_names, dimensions):
                        sheet[column] = dimension["values"][name]
                else:
                    sheet[SHEET_COLUMN] = name
                frames.append(sheet)
        # sort=False keeps the first sheet's column order, so the mapping
        # form lists columns in the order the client wrote them.
        frame = pd.concat(frames, ignore_index=True, sort=False)

    frame.attrs["cleaning_notes"] = report.notes
    frame.attrs["sheet_dimension_columns"] = dimension_names
    return frame


def _read_frame(filename: str, raw: bytes) -> pd.DataFrame:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise DatasetError(f"Unsupported file type '{suffix or filename}'. Upload a .csv or .xlsx file.")
    try:
        if suffix in EXCEL_EXTENSIONS:
            return _read_excel(raw)
        # sep=None + the python engine sniffs comma/semicolon/tab automatically.
        report = cleaning.Report()
        probe = pd.read_csv(io.BytesIO(raw), sep=None, engine="python", header=None,
                            nrows=cleaning.MAX_HEADER_SCAN + 4)
        header_row = cleaning.find_header_row(probe)
        csv = pd.read_csv(io.BytesIO(raw), sep=None, engine="python", header=header_row)
        if header_row:
            report.add(f"Header taken from row {header_row + 1}; the rows above it were a title or preamble.")
        csv = cleaning.clean(csv, report)
        csv.attrs["cleaning_notes"] = report.notes
        return csv
    except DatasetError:
        raise
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


def _structural_keys(frame: pd.DataFrame) -> list[pd.Series]:
    """Columns whose value decides which *other* columns a row even has.

    A file covering two departments carries each department's own measures:
    beds and meals for catering, sales and transactions for retail. Stacked
    into one table - by us across sheets, or by the client in one sheet - every
    row is blank in the other department's columns. Judged as missing data that
    file reads a fifth empty and every upload looks broken, so a column's blank
    rate is measured only where the column applies at all.

    Only low-cardinality text columns qualify, because a key has to name a part
    of the business ("Retail Cafe"), not a row.
    """
    keys = [c for c in frame.attrs.get("sheet_dimension_columns", []) if c in frame.columns]
    if not keys and SHEET_COLUMN in frame.columns:
        keys = [SHEET_COLUMN]
    if not keys:
        limit = max(2, min(50, len(frame) // 100))
        keys = [
            name
            for name in frame.columns
            if not pd.api.types.is_numeric_dtype(frame[name])
            and not pd.api.types.is_datetime64_any_dtype(frame[name])
            and 1 < frame[name].nunique(dropna=False) <= limit
        ]
    return [frame[name] for name in keys[:4]]


def profile_columns(frame: pd.DataFrame) -> list[dict]:
    """Per-column facts the mapping UI needs to suggest sensible defaults."""
    keys = _structural_keys(frame)
    key_names = {key.name for key in keys}
    profile = []
    for name in frame.columns:
        column = frame[name]
        blank = column.isna()
        scope = column
        if name not in key_names and blank.any() and not blank.all():
            for key in keys:
                # Structural only when the column is wholly absent for some
                # values of the key and wholly present for the rest. A column
                # with scattered gaps inside a group really is missing data.
                per_group = blank.groupby(key, dropna=False).mean()
                if set(per_group.unique()) <= {0.0, 1.0}:
                    scope = column[key.isin(per_group[per_group == 0.0].index)]
                    break
        counted = len(scope)
        samples = [str(v) for v in column.dropna().head(3).tolist()]
        profile.append({
            "name": name,
            "dtype": str(column.dtype),
            "is_numeric": bool(pd.api.types.is_numeric_dtype(column)),
            "is_date_like": bool(_looks_like_dates(column)),
            "null_pct": round((1 - (int(scope.notna().sum()) / counted)) * 100, 1) if counted else 0.0,
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
    # The cleaned frame is what gets stored, so every later read sees the
    # same rows the mapping was proposed against.
    frame.to_csv(UPLOAD_DIR / f"{dataset_id}.csv", index=False)
    return dataset_id, frame, profile_columns(frame)


def load_preview(stored_filename: str, rows: int = 8) -> list[dict]:
    """The first rows of a stored file, for showing what it contains.

    Reads only those rows rather than the whole file: this is on the path of
    every page load that shows the spreadsheet preview, and the files run to
    hundreds of thousands of rows.
    """
    path = UPLOAD_DIR / stored_filename
    if not path.exists():
        return []
    head = pd.read_csv(path, nrows=rows)
    return head.astype(object).where(head.notna(), None).to_dict("records")


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
