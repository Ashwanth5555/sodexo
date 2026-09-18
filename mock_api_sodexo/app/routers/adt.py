"""ADT (Admission-Discharge-Transfer) feed - the real hospital system category
for patient census/movement data. Deliberately NOT modeled as "POS" - a
hospital's patient volume does not flow through a point-of-sale system, and
conflating the two would misrepresent how this data actually originates in a
real account. POS is reserved for the genuinely retail-like Cafe operation
(see routers/pos.py).
"""
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.common import make_envelope
from app.services import source_reader as sr

router = APIRouter(prefix="/mock/adt", tags=["adt"])


@router.get("/census")
def get_census(
    location_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(1000, ge=1, le=20000),
):
    records, total = sr.query(
        "hourly_census",
        date_col="business_date",
        start_date=start_date,
        end_date=end_date,
        filters={"location_id": location_id},
        page=page,
        page_size=page_size,
    )
    return make_envelope("ADT", "/mock/adt/census", records, total, page, page_size)
