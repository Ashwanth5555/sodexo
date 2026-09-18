"""Workday/HRIS-style endpoints: the employee master, job catalog, and skills/
certifications for Sodexo's own support-services workforce.
"""
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.common import make_envelope, not_available
from app.services import source_reader as sr

router = APIRouter(prefix="/mock/workday", tags=["workday"])


@router.get("/workers")
def get_workers(
    employee_id: Optional[str] = None,
    location_id: Optional[str] = None,
    department_id: Optional[str] = None,
    job_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    records, total = sr.query(
        "employees",
        filters={"employee_id": employee_id, "location_id": location_id, "department_id": department_id, "job_id": job_id},
        page=page,
        page_size=page_size,
    )
    return make_envelope("Workday", "/mock/workday/workers", records, total, page, page_size)


@router.get("/jobs")
def get_jobs(department_id: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=5000)):
    records, total = sr.query("jobs", filters={"department_id": department_id}, page=page, page_size=page_size)
    return make_envelope("Workday", "/mock/workday/jobs", records, total, page, page_size)


@router.get("/skills")
def get_worker_skills(employee_id: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=5000)):
    """What skills each employee actually holds (worker_skills) - distinct from
    /job-skills, which is the fixed requirement per job role, not per person.
    """
    records, total = sr.query("worker_skills", filters={"employee_id": employee_id}, page=page, page_size=page_size)
    return make_envelope("Workday", "/mock/workday/skills", records, total, page, page_size)


@router.get("/job-skills")
def get_job_skills(job_id: Optional[str] = None, department_id: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=5000)):
    """The FIXED skill requirement per job role (job_skills) - what a role
    demands, not what a specific hired person happens to have.
    """
    records, total = sr.query("job_skills", filters={"job_id": job_id, "department_id": department_id}, page=page, page_size=page_size)
    return make_envelope("Workday", "/mock/workday/job-skills", records, total, page, page_size)


@router.get("/certifications")
def get_certifications():
    return not_available(
        "Workday",
        "/mock/workday/certifications",
        "No standalone certification-tracking data exists in the synthetic dataset for this POC - "
        "required certifications are captured via /mock/workday/job-skills instead.",
    )


@router.get("/availability")
def get_availability():
    return not_available(
        "Workday",
        "/mock/workday/availability",
        "No employee availability data exists in the synthetic dataset for this POC. "
        "This endpoint is a contract placeholder for a future real Workday integration.",
    )
