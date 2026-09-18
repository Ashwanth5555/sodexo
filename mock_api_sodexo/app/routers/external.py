from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.common import make_envelope
from app.services import source_reader as sr

router = APIRouter(prefix="/mock/external", tags=["external"])


@router.get("/holidays")
def get_holidays(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
):
    records, total = sr.query(
        "holidays", date_col="holiday_date", start_date=start_date, end_date=end_date, page=page, page_size=page_size
    )
    return make_envelope("External", "/mock/external/holidays", records, total, page, page_size)
