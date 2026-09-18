from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.data_quality import summary

router = APIRouter(prefix="/api/data-quality", tags=["data-quality"])


@router.get("/summary")
def data_quality_summary(db: Session = Depends(get_db)):
    return summary.get_summary(db)
