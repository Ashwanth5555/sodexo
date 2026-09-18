"""Data Quality presentation over the real ingestion_batch lineage table -
freshness and a quality-status derivation, computed live, not stored."""
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_DATASET_LABELS = {
    "/mock/erp/locations": "Locations", "/mock/erp/departments": "Departments",
    "/mock/erp/staffing-standards": "Staffing Standards", "/mock/erp/labor-budgets": "Labor Budgets",
    "/mock/workday/jobs": "Jobs", "/mock/workday/workers": "Employees", "/mock/workday/skills": "Worker Skills",
    "/mock/workday/job-skills": "Job Skill Requirements",
    "/mock/adt/census": "Patient Census (ADT)", "/mock/pos/cafe-sales": "Cafe Sales (POS)",
    "/mock/ukg/schedules": "Schedules (UKG)", "/mock/ukg/punches": "Punches (UKG)",
    "/mock/ukg/attendance": "Attendance (UKG)", "/mock/ukg/timecards": "Timecards (UKG)",
    "/mock/payroll/pay-rates": "Pay Rates (Payroll)", "/mock/external/holidays": "Holidays (External)",
}


def _quality_status(rejected: int, received: int) -> str:
    if received == 0:
        return "No Data"
    rate = rejected / received
    if rate == 0:
        return "Good"
    if rate < 0.10:
        return "Warning"
    return "Poor"


def get_summary(db: Session) -> dict:
    rows = db.execute(text("""
        SELECT b.source_system, b.source_endpoint, b.status, b.started_at, b.completed_at,
               b.records_received, b.records_loaded, b.records_rejected, b.error_message
        FROM ingestion_batch b
        WHERE b.started_at = (SELECT MAX(b2.started_at) FROM ingestion_batch b2 WHERE b2.source_endpoint = b.source_endpoint)
        ORDER BY b.source_system, b.source_endpoint
    """)).mappings().all()

    now = datetime.utcnow()
    sources = []
    for row in rows:
        completed_at = row["completed_at"]
        freshness_hours = None
        if completed_at:
            completed_dt = completed_at if isinstance(completed_at, datetime) else datetime.fromisoformat(str(completed_at))
            freshness_hours = round((now - completed_dt).total_seconds() / 3600, 1)
        sources.append({
            **dict(row),
            "dataset_label": _DATASET_LABELS.get(row["source_endpoint"], row["source_endpoint"]),
            "freshness_hours": freshness_hours,
            "quality_status": _quality_status(row["records_rejected"], row["records_received"]),
        })

    total_received = sum(s["records_received"] for s in sources)
    total_rejected = sum(s["records_rejected"] for s in sources)
    return {
        "sources": sources,
        "summary": {
            "total_sources": len(sources),
            "total_records_received": total_received,
            "total_records_rejected": total_rejected,
            "overall_quality_status": _quality_status(total_rejected, total_received) if sources else "No Data",
        },
    }
