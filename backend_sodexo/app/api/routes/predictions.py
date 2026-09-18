from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.predictions import engine

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


@router.get("/risk-models")
def risk_models(db: Session = Depends(get_db)):
    return engine.compute_all(db)
