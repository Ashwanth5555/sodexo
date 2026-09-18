"""UKG/Kronos-style endpoints: scheduling and time & attendance for the
Sodexo Healthcare account's own support-services staff (Food & Nutrition,
Retail & Cafe) - not hospital clinical staff, who are the client's employees,
not Sodexo's.
"""
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.common import make_envelope
from app.services import source_reader as sr

router = APIRouter(prefix="/mock/ukg", tags=["ukg"])


@router.get("/schedules")
def get_schedules(
    employee_id: Optional[str] = None,
    location_id: Optional[str] = None,
    department_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    records, total = sr.query(
        "schedules",
        date_col="shift_date",
        start_date=start_date,
        end_date=end_date,
        filters={"employee_id": employee_id, "location_id": location_id, "department_id": department_id},
        page=page,
        page_size=page_size,
    )
    return make_envelope("UKG", "/mock/ukg/schedules", records, total, page, page_size)


@router.get("/punches")
def get_punches(
    employee_id: Optional[str] = None,
    location_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    records, total = sr.query(
        "punches",
        date_col="punch_timestamp",
        start_date=start_date,
        end_date=end_date,
        filters={"employee_id": employee_id, "location_id": location_id},
        page=page,
        page_size=page_size,
    )
    return make_envelope("UKG", "/mock/ukg/punches", records, total, page, page_size)


@router.get("/attendance")
def get_attendance(
    employee_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    records, total = sr.query(
        "attendance",
        date_col="work_date",
        start_date=start_date,
        end_date=end_date,
        filters={"employee_id": employee_id},
        page=page,
        page_size=page_size,
    )
    return make_envelope("UKG", "/mock/ukg/attendance", records, total, page, page_size)


@router.get("/timecards")
def get_timecards(
    employee_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    records, total = sr.query(
        "timecards",
        date_col="work_date",
        start_date=start_date,
        end_date=end_date,
        filters={"employee_id": employee_id},
        page=page,
        page_size=page_size,
    )
    return make_envelope("UKG", "/mock/ukg/timecards", records, total, page, page_size)
