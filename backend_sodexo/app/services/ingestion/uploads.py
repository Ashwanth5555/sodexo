"""Manual file upload as an ingestion source.

Same destination and the same rules as the API pull: a row uploaded here is
validated by the identical validator, quarantined into data_quality_issue if it
fails, upserted by the identical loader, and recorded as an ingestion_batch. The
only difference is where the records come from - a CSV/Excel file instead of the
mock enterprise API - so Data Quality reports uploads alongside every other
source rather than treating them as a side channel.
"""
from __future__ import annotations

import io
import math
import uuid
from datetime import date, datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.models import IngestionBatch
from app.services.ingestion.pipeline import load_records

CSV_EXTENSIONS = {".csv"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
ALLOWED_EXTENSIONS = CSV_EXTENSIONS | EXCEL_EXTENSIONS

# Reject before reading into memory. The largest shipped dataset (punches) is a
# few MB, so 25MB is generous without letting an upload exhaust the process.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class UploadError(Exception):
    """A problem with the file itself - bad type, unreadable, wrong columns.

    Distinct from a row failing validation: those are quarantined and reported
    as rejected, which does not fail the upload.
    """


class DatasetSpec:
    def __init__(self, key: str, label: str, source_system: str, required: list[str], optional: list[str] | None = None):
        self.key = key
        self.label = label
        self.source_system = source_system
        self.required = required
        self.optional = optional or []

    @property
    def columns(self) -> list[str]:
        return self.required + self.optional


# Only the datasets that have both a validator and a loader are uploadable.
# job_skills / worker_skills / holidays are deliberately absent: they load
# through bespoke pipeline paths (or, for holidays, are counted but not stored),
# so accepting a file for them would imply a persistence guarantee we do not keep.
#
# "required" mirrors what the loader in loaders.py reads as r["..."] - a column
# missing there is a KeyError, not a validation failure - and "optional" mirrors
# what it reads as r.get(...). This is wider than the validator's own required
# set, which only covers the fields the data-quality rules inspect.
DATASETS: list[DatasetSpec] = [
    DatasetSpec("locations", "Locations", "ERP",
                ["location_id", "location_name", "hospital_type", "bed_count"]),
    DatasetSpec("departments", "Departments", "ERP",
                ["department_id", "department_name", "cost_center_id"]),
    DatasetSpec("jobs", "Jobs", "Workday",
                ["job_id", "job_title", "department_id"]),
    DatasetSpec("staffing_standards", "Staffing Standards", "ERP",
                ["department_id", "standard_type", "hppd_ratio"], ["notes"]),
    DatasetSpec("employees", "Employees", "Workday",
                ["employee_id", "employee_name", "location_id", "department_id", "job_id",
                 "employment_type", "standard_weekly_hours", "base_hourly_rate"]),
    DatasetSpec("pay_rates", "Pay Rates", "Payroll",
                ["employee_id", "effective_date", "base_hourly_rate", "overtime_multiplier",
                 "double_time_multiplier", "currency"]),
    DatasetSpec("labor_budgets", "Labor Budgets", "ERP",
                ["location_id", "department_id", "year", "month", "labor_budget", "overtime_budget"]),
    DatasetSpec("hourly_census", "Hourly Census", "ADT",
                ["location_id", "business_date", "hour_of_day", "occupied_beds", "admissions_count",
                 "discharges_count", "transfers_count", "meals_served",
                 "required_tray_line_associates", "required_nutrition_supervisors",
                 "primary_required_skill_id", "primary_required_skill_name", "required_manpower"]),
    DatasetSpec("cafe_hourly_sales", "Cafe Hourly Sales", "POS",
                ["location_id", "business_date", "hour_of_day", "sales_amount", "transaction_count",
                 "required_cafe_associates", "required_cafe_supervisors",
                 "primary_required_skill_id", "primary_required_skill_name", "required_manpower"],
                ["promotion_flag", "event_flag"]),
    DatasetSpec("schedules", "Schedules", "UKG",
                ["schedule_id", "employee_id", "location_id", "department_id", "shift_date",
                 "scheduled_start", "scheduled_end", "scheduled_hours"]),
    DatasetSpec("punches", "Time Punches", "UKG",
                ["punch_id", "employee_id", "location_id", "punch_timestamp", "punch_type", "device_id"],
                ["manual_edit_flag", "exception_flag"]),
    DatasetSpec("attendance", "Attendance", "UKG",
                ["employee_id", "work_date", "schedule_id", "attendance_status"],
                ["late_minutes", "missed_punch_flag"]),
    DatasetSpec("timecards", "Timecards", "UKG",
                ["employee_id", "work_date", "scheduled_hours", "regular_hours", "overtime_hours",
                 "total_worked_hours"],
                ["exception_flag"]),
]

DATASETS_BY_KEY = {d.key: d for d in DATASETS}


def _normalise(value):
    """Turn a pandas cell into something SQLAlchemy and the loaders accept.

    pandas hands back numpy scalars and NaT/NaN, neither of which SQLite will
    bind, and Timestamps that the loaders would otherwise store as pandas objects.
    """
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, (datetime, date)):
        return value
    # numpy int64/float64/bool_ -> the equivalent Python scalar
    item = getattr(value, "item", None)
    if callable(item) and type(value).__module__ == "numpy":
        return item()
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def parse_file(filename: str, raw: bytes) -> list[dict]:
    """Read a CSV/Excel upload into plain dict records."""
    if not raw:
        raise UploadError("The file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadError(f"File is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.")

    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise UploadError(f"Unsupported file type '{suffix or filename}'. Upload a .csv or .xlsx file.")

    try:
        if suffix in CSV_EXTENSIONS:
            frame = pd.read_csv(io.BytesIO(raw))
        else:
            frame = pd.read_excel(io.BytesIO(raw))
    except Exception as exc:
        raise UploadError(f"Could not read the file: {exc}") from exc

    frame.columns = [str(c).strip() for c in frame.columns]
    # An all-empty trailing row is common in hand-edited spreadsheets and would
    # otherwise be reported as a rejected record.
    frame = frame.dropna(how="all")
    return [{k: _normalise(v) for k, v in row.items()} for row in frame.to_dict("records")]


def check_columns(spec: DatasetSpec, records: list[dict]) -> None:
    if not records:
        raise UploadError("The file has no data rows.")
    present = set(records[0].keys())
    missing = [c for c in spec.required if c not in present]
    if missing:
        raise UploadError(
            f"Missing required column(s) for {spec.label}: {', '.join(missing)}. "
            f"Expected: {', '.join(spec.columns)}."
        )


def ingest_upload(db: Session, dataset: str, filename: str, raw: bytes) -> IngestionBatch:
    spec = DATASETS_BY_KEY.get(dataset)
    if spec is None:
        raise UploadError(f"Unknown dataset '{dataset}'.")

    records = parse_file(filename, raw)
    check_columns(spec, records)

    batch = IngestionBatch(
        batch_id=f"BATCH-UPLOAD-{spec.key}-{uuid.uuid4().hex[:10]}",
        source_system=f"Upload ({spec.source_system})",
        source_endpoint=f"/upload/{spec.key}",
        started_at=datetime.utcnow(),
        status="RUNNING",
    )
    db.add(batch)
    db.flush()

    try:
        load_records(db, records, spec.key, batch)
    except KeyError as exc:
        # Safety net for a column the loader needs that the spec above missed -
        # a plain KeyError would surface to the user as a bare column name.
        db.rollback()
        raise UploadError(
            f"Column {exc} is required for {spec.label} but is not in the file. "
            f"Expected: {', '.join(spec.columns)}."
        ) from exc
    db.commit()
    return batch


def template_csv(dataset: str) -> str:
    """A header-only CSV so a user can start from the exact expected columns."""
    spec = DATASETS_BY_KEY.get(dataset)
    if spec is None:
        raise UploadError(f"Unknown dataset '{dataset}'.")
    return ",".join(spec.columns) + "\n"
