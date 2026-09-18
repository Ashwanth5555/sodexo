"""Ingestion orchestration: Sodexo mock API -> validate -> quarantine bad
records -> upsert canonical tables -> record ingestion_batch lineage.
Idempotent: safe to re-run (Session.merge on each table's natural/composite
primary key; attendance/timecards have surrogate PKs, so a
(employee_id, work_date) -> id map is pre-loaded before writing).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    BridgeEmployeeSkill,
    BridgeJobSkill,
    DataQualityIssue,
    DimDepartment,
    DimEmployee,
    DimJob,
    DimLocation,
    FactAttendance,
    FactSchedule,
    FactTimecard,
    IngestionBatch,
)
from app.services.ingestion import loaders
from app.services.ingestion.client import MockApiClient
from app.services.ingestion.validators import VALIDATORS, ReferenceSets, detect_duplicate_ids

# (source_system, endpoint, sheet_key, id_field)
SOURCE_TABLE_PLAN: list[tuple[str, str, str, str | None]] = [
    ("ERP", "/mock/erp/locations", "locations", "location_id"),
    ("ERP", "/mock/erp/departments", "departments", "department_id"),
    ("Workday", "/mock/workday/jobs", "jobs", "job_id"),
    ("ERP", "/mock/erp/staffing-standards", "staffing_standards", "department_id"),
    ("Workday", "/mock/workday/workers", "employees", "employee_id"),
    ("Payroll", "/mock/payroll/pay-rates", "pay_rates", "employee_id"),
    ("ERP", "/mock/erp/labor-budgets", "labor_budgets", None),
    ("ADT", "/mock/adt/census", "hourly_census", None),
    ("POS", "/mock/pos/cafe-sales", "cafe_hourly_sales", None),
    ("UKG", "/mock/ukg/schedules", "schedules", "schedule_id"),
    ("UKG", "/mock/ukg/punches", "punches", "punch_id"),
    ("UKG", "/mock/ukg/attendance", "attendance", None),
    ("UKG", "/mock/ukg/timecards", "timecards", None),
]

_LOADER_FOR_SHEET = {
    "locations": loaders.load_location,
    "departments": loaders.load_department,
    "jobs": loaders.load_job,
    "staffing_standards": loaders.load_staffing_standard,
    "employees": loaders.load_employee,
    "pay_rates": loaders.load_pay_rate,
    "labor_budgets": loaders.load_labor_budget,
    "hourly_census": loaders.load_census,
    "cafe_hourly_sales": loaders.load_cafe_sales,
    "schedules": loaders.load_schedule,
    "punches": loaders.load_punch,
}


def _new_batch_id(source: str, endpoint: str) -> str:
    return f"BATCH-{source}-{endpoint.strip('/').split('/')[-1]}-{uuid.uuid4().hex[:10]}"


def _build_reference_sets(db: Session) -> ReferenceSets:
    ref = ReferenceSets()
    ref.location_ids = {r[0] for r in db.execute(select(DimLocation.location_id))}
    ref.department_ids = {r[0] for r in db.execute(select(DimDepartment.department_id))}
    ref.job_ids = {r[0] for r in db.execute(select(DimJob.job_id))}
    ref.employee_ids = {r[0] for r in db.execute(select(DimEmployee.employee_id))}
    ref.schedule_ids = {r[0] for r in db.execute(select(FactSchedule.schedule_id))}
    return ref


def ingest_source(db: Session, client: MockApiClient, source_system: str, endpoint: str, sheet: str) -> IngestionBatch:
    batch_id = _new_batch_id(source_system, endpoint)
    batch = IngestionBatch(batch_id=batch_id, source_system=source_system, source_endpoint=endpoint, started_at=datetime.utcnow(), status="RUNNING")
    db.add(batch)
    db.flush()

    try:
        records = client.fetch_all_pages(endpoint)
    except Exception as exc:
        batch.status = "FAILED"
        batch.completed_at = datetime.utcnow()
        batch.error_message = str(exc)
        db.flush()
        return batch

    return load_records(db, records, sheet, batch)


def load_records(db: Session, records: list[dict], sheet: str, batch: IngestionBatch) -> IngestionBatch:
    """Validate and upsert already-fetched records against an open batch.

    Split out of ingest_source so a file upload runs the identical path - same
    validators, same quarantine of bad rows into data_quality_issue, same
    loaders, same batch lineage - and only the source of the records differs.
    """
    batch_id = batch.batch_id
    source_system = batch.source_system
    ref = _build_reference_sets(db)
    validator = VALIDATORS.get(sheet)
    id_field = next((idf for s, e, sh, idf in SOURCE_TABLE_PLAN if sh == sheet), None)
    dupes = detect_duplicate_ids(records, id_field) if id_field else set()

    loaded = rejected = 0
    attendance_id_map: dict = {}
    timecard_id_map: dict = {}
    if sheet == "attendance":
        for row in db.execute(select(FactAttendance.attendance_id, FactAttendance.employee_id, FactAttendance.work_date)):
            attendance_id_map[(row.employee_id, str(row.work_date))] = row.attendance_id
    if sheet == "timecards":
        for row in db.execute(select(FactTimecard.timecard_id, FactTimecard.employee_id, FactTimecard.work_date)):
            timecard_id_map[(row.employee_id, str(row.work_date))] = row.timecard_id

    for record in records:
        issues = validator(record, ref) if validator else []
        if id_field and record.get(id_field) in dupes:
            issues.append(type("I", (), {"issue_type": "DUPLICATE_ID", "severity": "HIGH"})())

        if issues:
            rejected += 1
            for issue in issues:
                db.add(DataQualityIssue(
                    batch_id=batch_id, source_system=source_system,
                    source_record_id=str(record.get(id_field) or record.get("employee_id") or ""),
                    issue_type=issue.issue_type, severity=issue.severity,
                    description=f"{issue.issue_type} on {sheet}", record_payload=record,
                ))
            continue

        if sheet == "attendance":
            loaders.load_attendance(db, record, batch_id, attendance_id_map)
        elif sheet == "timecards":
            loaders.load_timecard(db, record, batch_id, timecard_id_map)
        else:
            _LOADER_FOR_SHEET[sheet](db, record, batch_id)
        loaded += 1

    batch.records_received = len(records)
    batch.records_loaded = loaded
    batch.records_rejected = rejected
    batch.status = "COMPLETED" if rejected == 0 else "COMPLETED_WITH_ISSUES"
    batch.completed_at = datetime.utcnow()
    db.flush()
    return batch


def ingest_job_skills(db: Session, client: MockApiClient) -> IngestionBatch:
    """job_skills has no single id_field (job_id + skill_id composite) - loads
    the skill dimension AND the fixed job->skill requirement bridge.
    """
    endpoint = "/mock/workday/job-skills"
    batch_id = _new_batch_id("Workday", endpoint)
    records = client.fetch_all_pages(endpoint)
    for r in records:
        loaders.load_skill_dim(db, r["skill_id"], r["skill_name"])
        db.merge(BridgeJobSkill(job_id=r["job_id"], skill_id=r["skill_id"]))
    batch = IngestionBatch(
        batch_id=batch_id, source_system="Workday", source_endpoint=endpoint, started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(), status="COMPLETED", records_received=len(records), records_loaded=len(records),
    )
    db.add(batch)
    return batch


def ingest_worker_skills(db: Session, client: MockApiClient) -> IngestionBatch:
    """What skills each hired person actually has - separate from the fixed
    job_skills requirement."""
    endpoint = "/mock/workday/skills"
    batch_id = _new_batch_id("Workday", endpoint)
    records = client.fetch_all_pages(endpoint)
    for r in records:
        loaders.load_skill_dim(db, r["skill_id"], r["skill_name"])
        db.merge(BridgeEmployeeSkill(employee_id=r["employee_id"], skill_id=r["skill_id"], proficiency_level=r["proficiency_level"]))
    batch = IngestionBatch(
        batch_id=batch_id, source_system="Workday", source_endpoint=endpoint, started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(), status="COMPLETED", records_received=len(records), records_loaded=len(records),
    )
    db.add(batch)
    return batch


def run_full_pipeline(db: Session, client: MockApiClient | None = None) -> list[IngestionBatch]:
    client = client or MockApiClient()
    batches: list[IngestionBatch] = []

    for source_system, endpoint, sheet, _ in SOURCE_TABLE_PLAN:
        batch = ingest_source(db, client, source_system, endpoint, sheet)
        db.commit()
        batches.append(batch)
        # job_skills must load right after jobs, worker_skills right after employees,
        # so downstream FK-dependent loads (pay_rates on employees, etc.) still see them.
        if sheet == "jobs":
            batches.append(ingest_job_skills(db, client))
            db.commit()
        if sheet == "employees":
            batches.append(ingest_worker_skills(db, client))
            db.commit()

    holidays_records = client.fetch_all_pages("/mock/external/holidays")
    holiday_batch = IngestionBatch(
        batch_id=_new_batch_id("External", "/mock/external/holidays"), source_system="External",
        source_endpoint="/mock/external/holidays", started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        status="COMPLETED", records_received=len(holidays_records), records_loaded=len(holidays_records),
    )
    db.add(holiday_batch)
    db.commit()
    batches.append(holiday_batch)

    min_d = db.execute(text("SELECT MIN(business_date) FROM fact_census")).scalar()
    max_d = db.execute(text("SELECT MAX(business_date) FROM fact_census")).scalar()
    if min_d and max_d:
        start = min_d if isinstance(min_d, date) else date.fromisoformat(str(min_d)[:10])
        end = max_d if isinstance(max_d, date) else date.fromisoformat(str(max_d)[:10])
        loaders.build_dim_date(db, start, end + timedelta(days=90), holidays_records)
        db.commit()

    return batches
