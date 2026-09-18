from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.common import make_envelope
from app.services import source_reader as sr

router = APIRouter(prefix="/mock/erp", tags=["erp"])


@router.get("/locations")
def get_locations(page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=5000)):
    records, total = sr.query("locations", page=page, page_size=page_size)
    return make_envelope("ERP", "/mock/erp/locations", records, total, page, page_size)


@router.get("/departments")
def get_departments(page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=5000)):
    records, total = sr.query("departments", page=page, page_size=page_size)
    return make_envelope("ERP", "/mock/erp/departments", records, total, page, page_size)


@router.get("/labor-budgets")
def get_labor_budgets(
    location_id: Optional[str] = None,
    department_id: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(1000, ge=1, le=5000),
):
    records, total = sr.query(
        "labor_budgets",
        filters={"location_id": location_id, "department_id": department_id, "year": year, "month": month},
        page=page,
        page_size=page_size,
    )
    return make_envelope("ERP", "/mock/erp/labor-budgets", records, total, page, page_size)


@router.get("/staffing-standards")
def get_staffing_standards(department_id: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=5000)):
    """HPPD (Hours Per Patient Day) and equivalent staffing ratios - the direct,
    no-model census/transaction-to-manpower link, sourced from the contract's
    finance/workforce-planning system.
    """
    records, total = sr.query("staffing_standards", filters={"department_id": department_id}, page=page, page_size=page_size)
    return make_envelope("ERP", "/mock/erp/staffing-standards", records, total, page, page_size)
