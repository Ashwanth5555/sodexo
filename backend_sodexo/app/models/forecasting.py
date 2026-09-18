from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TrainingJob(Base):
    """One row per 'Train All Models' click. Polled by the frontend for
    live progress while the engine works through series x model combinations
    in a background thread."""

    __tablename__ = "training_job"

    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_series: Mapped[str | None] = mapped_column(String(60), nullable=True)
    current_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    total_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class ModelRun(Base):
    """One row per (series, model) attempt from a training job. Every model in
    the portfolio is always represented per series - completed with metrics,
    failed with an error, or not_applicable with an eligibility reason. Never
    silently dropped, so the leaderboard always shows the full portfolio."""

    __tablename__ = "model_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), nullable=False)
    series_key: Mapped[str] = mapped_column(String(60), nullable=False)
    model_id: Mapped[str] = mapped_column(String(40), nullable=False)
    model_label: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    evaluation_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    folds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    train_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    test_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mape: Mapped[float | None] = mapped_column(Float, nullable=True)
    rmse: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    wape: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_champion: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    runtime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ModelPrediction(Base):
    """Backtest + future forecast rows for one model_run, used by the Forecast
    Explorer chart. is_future=False rows have an actual for comparison;
    is_future=True rows are genuine forward forecasts beyond the last known
    actual."""

    __tablename__ = "model_prediction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    series_key: Mapped[str] = mapped_column(String(60), nullable=False)
    model_id: Mapped[str] = mapped_column(String(40), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    hour_of_day: Mapped[int] = mapped_column(Integer, nullable=False)
    actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted: Mapped[float] = mapped_column(Float, nullable=False)
    lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_future: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fold: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SeriesFeatureSelection(Base):
    """Per-series override of which input columns are actually fed to the
    models as exogenous drivers - lets a user click a column off in Demand
    Drivers & Feature Mapping without editing contract.py. A column with no
    row here defaults to enabled (the contract's full set)."""

    __tablename__ = "series_feature_selection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_key: Mapped[str] = mapped_column(String(60), nullable=False)
    column_name: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
