"""Data-quality validation rules run before a record is loaded into the
canonical model. A record that fails validation is quarantined
(data_quality_issue) and NOT loaded - bad records never disappear silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Issue:
    issue_type: str
    severity: str


class ReferenceSets:
    def __init__(self):
        self.location_ids: set[str] = set()
        self.department_ids: set[str] = set()
        self.job_ids: set[str] = set()
        self.employee_ids: set[str] = set()
        self.schedule_ids: set[str] = set()


def _require_fields(record: dict, fields: list[str]) -> list[Issue]:
    issues = []
    for f in fields:
        if record.get(f) is None or record.get(f) == "":
            issues.append(Issue("MISSING_REQUIRED_FIELD", "HIGH"))
    return issues


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def validate_location(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["location_id", "location_name", "hospital_type", "bed_count"])
    if record.get("bed_count") is not None and record["bed_count"] <= 0:
        issues.append(Issue("IMPLAUSIBLE_VALUE", "HIGH"))
    return issues


def validate_department(record: dict, ref: ReferenceSets) -> list[Issue]:
    return _require_fields(record, ["department_id", "department_name", "cost_center_id"])


def validate_job(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["job_id", "job_title", "department_id"])
    if record.get("department_id") and ref.department_ids and record["department_id"] not in ref.department_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "MEDIUM"))
    return issues


def validate_employee(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(
        record, ["employee_id", "employee_name", "location_id", "department_id", "job_id", "employment_type", "standard_weekly_hours", "base_hourly_rate"]
    )
    if record.get("location_id") and ref.location_ids and record["location_id"] not in ref.location_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    if record.get("department_id") and ref.department_ids and record["department_id"] not in ref.department_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    if record.get("job_id") and ref.job_ids and record["job_id"] not in ref.job_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "MEDIUM"))
    if record.get("base_hourly_rate") is not None and record["base_hourly_rate"] < 0:
        issues.append(Issue("NEGATIVE_VALUE", "CRITICAL"))
    return issues


def validate_pay_rate(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["employee_id", "effective_date", "base_hourly_rate"])
    if record.get("employee_id") and ref.employee_ids and record["employee_id"] not in ref.employee_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    if record.get("base_hourly_rate") is not None and record["base_hourly_rate"] < 0:
        issues.append(Issue("NEGATIVE_VALUE", "CRITICAL"))
    return issues


def validate_labor_budget(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["location_id", "department_id", "year", "month", "labor_budget"])
    if record.get("labor_budget") is not None and record["labor_budget"] < 0:
        issues.append(Issue("NEGATIVE_VALUE", "HIGH"))
    return issues


def validate_census(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["location_id", "business_date", "hour_of_day", "occupied_beds", "required_manpower"])
    if record.get("location_id") and ref.location_ids and record["location_id"] not in ref.location_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    if record.get("occupied_beds") is not None and record["occupied_beds"] < 0:
        issues.append(Issue("NEGATIVE_VALUE", "HIGH"))
    if record.get("required_manpower") is not None and record["required_manpower"] < 0:
        issues.append(Issue("NEGATIVE_VALUE", "HIGH"))
    hour = record.get("hour_of_day")
    if hour is not None and not (0 <= hour <= 23):
        issues.append(Issue("IMPLAUSIBLE_VALUE", "MEDIUM"))
    return issues


def validate_cafe_sales(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["location_id", "business_date", "hour_of_day", "sales_amount", "transaction_count", "required_manpower"])
    if record.get("location_id") and ref.location_ids and record["location_id"] not in ref.location_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    if record.get("sales_amount") is not None and record["sales_amount"] < 0:
        issues.append(Issue("NEGATIVE_VALUE", "HIGH"))
    if record.get("transaction_count") is not None and record["transaction_count"] < 0:
        issues.append(Issue("NEGATIVE_VALUE", "HIGH"))
    return issues


def validate_schedule(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["schedule_id", "employee_id", "location_id", "department_id", "shift_date", "scheduled_start", "scheduled_end"])
    if record.get("employee_id") and ref.employee_ids and record["employee_id"] not in ref.employee_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    start = _parse_dt(record.get("scheduled_start"))
    end = _parse_dt(record.get("scheduled_end"))
    if start and end and end <= start:
        issues.append(Issue("IMPOSSIBLE_TIME_RANGE", "CRITICAL"))
    if start is None or end is None:
        issues.append(Issue("INVALID_TIMESTAMP", "HIGH"))
    return issues


def validate_punch(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["punch_id", "employee_id", "location_id", "punch_timestamp", "punch_type"])
    if record.get("employee_id") and ref.employee_ids and record["employee_id"] not in ref.employee_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    if record.get("punch_type") not in ("IN", "OUT"):
        issues.append(Issue("INVALID_PUNCH_TYPE", "MEDIUM"))
    if _parse_dt(record.get("punch_timestamp")) is None:
        issues.append(Issue("INVALID_TIMESTAMP", "HIGH"))
    return issues


def validate_attendance(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["employee_id", "work_date", "schedule_id", "attendance_status"])
    if record.get("employee_id") and ref.employee_ids and record["employee_id"] not in ref.employee_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    if record.get("attendance_status") not in ("Present", "Late", "Absent"):
        issues.append(Issue("IMPLAUSIBLE_VALUE", "MEDIUM"))
    return issues


def validate_timecard(record: dict, ref: ReferenceSets) -> list[Issue]:
    issues = _require_fields(record, ["employee_id", "work_date", "regular_hours", "overtime_hours", "total_worked_hours"])
    if record.get("employee_id") and ref.employee_ids and record["employee_id"] not in ref.employee_ids:
        issues.append(Issue("ORPHAN_FOREIGN_KEY", "HIGH"))
    for field in ("regular_hours", "overtime_hours", "total_worked_hours"):
        v = record.get(field)
        if v is not None and v < 0:
            issues.append(Issue("NEGATIVE_VALUE", "HIGH"))
    if record.get("total_worked_hours") is not None and record["total_worked_hours"] > 24:
        issues.append(Issue("IMPLAUSIBLE_VALUE", "HIGH"))
    return issues


def validate_holiday(record: dict, ref: ReferenceSets) -> list[Issue]:
    return _require_fields(record, ["holiday_date", "holiday_name"])


VALIDATORS = {
    "locations": validate_location,
    "departments": validate_department,
    "jobs": validate_job,
    "employees": validate_employee,
    "pay_rates": validate_pay_rate,
    "labor_budgets": validate_labor_budget,
    "hourly_census": validate_census,
    "cafe_hourly_sales": validate_cafe_sales,
    "schedules": validate_schedule,
    "punches": validate_punch,
    "attendance": validate_attendance,
    "timecards": validate_timecard,
    "holidays": validate_holiday,
}


def detect_duplicate_ids(records: list[dict], id_field: str) -> set:
    seen: set = set()
    dupes: set = set()
    for r in records:
        val = r.get(id_field)
        if val in seen:
            dupes.add(val)
        seen.add(val)
    return dupes
