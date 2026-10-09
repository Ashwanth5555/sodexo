from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.recommendations import engine

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("")
def list_recommendations(db: Session = Depends(get_db)):
    recommendations = engine.compute_recommendations(db)
    # An empty list is not self-explanatory, so it carries its reason.
    return {
        "recommendations": recommendations,
        "note": None if recommendations else engine.empty_reason(db),
    }


class QuestionRequest(BaseModel):
    question: str


@router.post("/ask")
def ask(payload: QuestionRequest, db: Session = Depends(get_db)):
    return engine.answer_question(db, payload.question)
