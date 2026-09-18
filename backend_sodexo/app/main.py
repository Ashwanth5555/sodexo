from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import assistant, auth, data_quality, datasets, forecast, forecast_workspace, ingestion, predictions, recommendations, training
from app.core.config import get_settings
from app.core.database import Base, engine

app = FastAPI(
    title="Sodexo Healthcare Forecasting POC - Application API",
    description="Ingestion trigger, multi-model forecasting engine, predictive risk models, AI recommendations, AI assistant, and data quality for the Sodexo Healthcare labor-forecasting POC.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Opt-in (AUTH_PROTECT_API=true): reject unauthenticated /api calls at the edge
# so the login gate covers the API itself, not only the UI.
if get_settings().auth_protect_api:
    app.middleware("http")(auth.require_api_auth)


@app.on_event("startup")
def _startup() -> None:
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        # Lightweight self-migration: create_all only creates missing tables,
        # it never adds columns to a table that already exists. Add any new
        # model_run columns here so an existing sodexo.db picks them up.
        existing_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(model_run)"))}
        if "train_rows" not in existing_columns:
            conn.execute(text("ALTER TABLE model_run ADD COLUMN train_rows INTEGER"))
        if "test_rows" not in existing_columns:
            conn.execute(text("ALTER TABLE model_run ADD COLUMN test_rows INTEGER"))

        # A training_job can only be genuinely "RUNNING" while the process
        # that started its background thread is still alive - if we're
        # starting up and one is marked RUNNING, that process is gone (a
        # restart or crash killed the thread mid-run). Self-heal so the UI
        # doesn't show a stale "training in progress" forever.
        conn.execute(text(
            "UPDATE training_job SET status='FAILED', "
            "error_message='Interrupted by a server restart mid-training.', "
            "completed_at=CURRENT_TIMESTAMP WHERE status='RUNNING'"
        ))


app.include_router(auth.router)
app.include_router(datasets.router)
app.include_router(ingestion.router)
app.include_router(training.router)
app.include_router(forecast.router)
app.include_router(forecast_workspace.router)
app.include_router(predictions.router)
app.include_router(recommendations.router)
app.include_router(assistant.router)
app.include_router(data_quality.router)


@app.get("/health")
def health():
    return {"status": "UP", "service": "backend-sodexo-application-api"}


@app.get("/")
def root():
    return {"service": "Sodexo Healthcare Forecasting POC - Application API", "docs": "/docs", "health": "/health"}
