from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.punch_analytics import rules

router = APIRouter(prefix="/api/punch-analytics", tags=["punch-analytics"])


@router.get("/anomalies")
def anomalies(
    location_id: str | None = Query(None),
    department_id: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    grain: str = Query("daily", pattern="^(hourly|daily|monthly)$", description="Reporting grain. hourly renders a time-of-day profile."),
    db: Session = Depends(get_db),
):
    return rules.detect_anomalies(db, location_id, department_id, start_date, end_date, grain=grain)
