from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.analytics import labor_analytics

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/filters")
def filters(db: Session = Depends(get_db)):
    return labor_analytics.get_filters(db)


@router.get("/summary")
def summary(
    location_id: str | None = Query(None),
    department_id: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    last_days: int | None = Query(None, ge=7, le=730, description="Convenience window: the most recent N days of timecard data. Ignored if start_date/end_date are given."),
    grain: str = Query("daily", pattern="^(daily|weekly|monthly)$", description="Reporting grain for every time series. No hourly option: labor cost, overtime and attendance come from daily timecards."),
    db: Session = Depends(get_db),
):
    return labor_analytics.compute(db, location_id, department_id, start_date, end_date, grain=grain, last_days=last_days)
