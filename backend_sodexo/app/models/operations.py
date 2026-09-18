from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IngestionBatch(Base):
    __tablename__ = "ingestion_batch"

    batch_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(30), nullable=False)
    source_endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    load_type: Mapped[str] = mapped_column(String(20), nullable=False, default="FULL")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING")
    records_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_loaded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issue"

    issue_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(40), nullable=False)
    source_system: Mapped[str] = mapped_column(String(30), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    record_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    resolved: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)
