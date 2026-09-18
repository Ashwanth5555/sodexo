from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.common import make_envelope
from app.services import source_reader as sr

router = APIRouter(prefix="/mock/payroll", tags=["payroll"])


@router.get("/pay-rates")
def get_pay_rates(employee_id: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=5000)):
    records, total = sr.query("pay_rates", filters={"employee_id": employee_id}, page=page, page_size=page_size)
    return make_envelope("Payroll", "/mock/payroll/pay-rates", records, total, page, page_size)


@router.get("/timecards")
def get_payroll_timecards(
    employee_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    """Processed/payroll view of timecards - reads the same underlying
    `timecards` sheet as /mock/ukg/timecards. In a real integration these
    would be two different systems/timings; documented here rather than faked.
    """
    records, total = sr.query(
        "timecards",
        date_col="work_date",
        start_date=start_date,
        end_date=end_date,
        filters={"employee_id": employee_id},
        page=page,
        page_size=page_size,
    )
    return make_envelope("Payroll", "/mock/payroll/timecards", records, total, page, page_size)


@router.get("/exceptions")
def get_payroll_exceptions(
    employee_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    """Derived filter (exception_flag=True) over the timecards dataset - not a separate dataset."""
    df = sr.get_sheet("timecards")
    df = df[df["exception_flag"] == True]  # noqa: E712
    if employee_id:
        df = df[df["employee_id"] == employee_id]
    if start_date:
        df = df[df["work_date"] >= start_date]
    if end_date:
        df = df[df["work_date"] <= end_date]
    total = len(df)
    start = (page - 1) * page_size
    page_df = df.iloc[start : start + page_size]
    records = page_df.assign(work_date=page_df["work_date"].dt.strftime("%Y-%m-%d")).to_dict(orient="records")
    return make_envelope("Payroll", "/mock/payroll/exceptions", records, total, page, page_size)
