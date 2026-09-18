"""Point-of-Sale endpoints - reserved for the Retail & Cafe department's
actual transactional register, the one part of this account that's genuinely
retail in nature (staff/visitor purchases, not patient care).
"""
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.common import make_envelope
from app.services import source_reader as sr

router = APIRouter(prefix="/mock/pos", tags=["pos"])


@router.get("/cafe-sales")
def get_cafe_sales(
    location_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(1000, ge=1, le=20000),
):
    records, total = sr.query(
        "cafe_hourly_sales",
        date_col="business_date",
        start_date=start_date,
        end_date=end_date,
        filters={"location_id": location_id},
        page=page,
        page_size=page_size,
    )
    return make_envelope("POS", "/mock/pos/cafe-sales", records, total, page, page_size)
