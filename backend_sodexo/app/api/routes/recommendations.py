from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.recommendations import engine

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("")
def list_recommendations(db: Session = Depends(get_db)):
    return {"recommendations": engine.compute_recommendations(db)}


class QuestionRequest(BaseModel):
    question: str


@router.post("/ask")
def ask(payload: QuestionRequest, db: Session = Depends(get_db)):
    return engine.answer_question(db, payload.question)
