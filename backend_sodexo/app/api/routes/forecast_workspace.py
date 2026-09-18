from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.forecasting import workspace

router = APIRouter(prefix="/api/forecast", tags=["forecast-workspace"])


@router.get("/drivers")
def drivers(db: Session = Depends(get_db)):
    return workspace.get_drivers(db)


class ColumnSelectionRequest(BaseModel):
    series_key: str
    column: str
    enabled: bool


@router.post("/drivers/selection")
def set_column_selection(payload: ColumnSelectionRequest, db: Session = Depends(get_db)):
    try:
        return workspace.set_input_column_enabled(db, payload.series_key, payload.column, payload.enabled)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/eda")
def eda(series_key: str = Query(...), db: Session = Depends(get_db)):
    try:
        return workspace.get_eda(db, series_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/preprocessing")
def preprocessing(series_key: str = Query(...), db: Session = Depends(get_db)):
    try:
        return workspace.get_preprocessing(db, series_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/confidence")
def confidence(series_key: str = Query(...), model_id: str | None = Query(None), db: Session = Depends(get_db)):
    try:
        return workspace.get_confidence(db, series_key, model_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class SensitivityRequest(BaseModel):
    series_key: str
    demand_multiplier_pct: float = 0.0
    promotion: bool | None = None
    event: bool | None = None


@router.post("/sensitivity")
def sensitivity(payload: SensitivityRequest, db: Session = Depends(get_db)):
    try:
        return workspace.run_sensitivity(db, payload.series_key, payload.demand_multiplier_pct, payload.promotion, payload.event)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/drift")
def drift(series_key: str = Query(...), model_id: str | None = Query(None), db: Session = Depends(get_db)):
    try:
        return workspace.get_drift(db, series_key, model_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/staffing-impact")
def staffing_impact(series_key: str = Query(...), days: int = Query(14, ge=1, le=60), db: Session = Depends(get_db)):
    try:
        return workspace.get_staffing_impact(db, series_key, days)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
