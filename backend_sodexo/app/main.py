import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import assistant, auth, data_quality, datasets, forecast, forecast_workspace, recommendations, training
from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.schema import add_missing_columns

# uvicorn configures its own loggers and leaves the root alone, so without
# this an app-level logger.info() goes nowhere. The startup data wipe and the
# training worker count are the two things an operator needs to see in the
# container log to know what the process actually did, so attach a handler
# for the app tree - but only if nothing else has (gunicorn, a test harness,
# or `uvicorn --log-config` may own logging already).
_app_logger = logging.getLogger("app")
if not _app_logger.handlers:
    # Force UTF-8 on the stream. A Windows console defaults to cp1252, and a
    # log line carrying any non-ASCII character - an arrow in a user-facing
    # message, or a column name from the client's own file - then raises
    # inside logging itself and prints "--- Logging error ---" instead of the
    # record. errors="replace" keeps a mangled character preferable to a lost
    # line, which matters most when the line is a stack trace.
    _stream = sys.stdout
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    _handler = logging.StreamHandler(_stream)
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))
    _app_logger.addHandler(_handler)
    _app_logger.setLevel(logging.INFO)

app = FastAPI(
    title="Labor Forecasting POC - Application API",
    description="Upload-driven multi-model forecasting engine, AI recommendations, AI assistant and data quality. Every series comes from a file the user uploads; there is no built-in dataset.",
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

    # create_all only creates missing tables, never columns on existing ones.
    # This walks every model and adds what the database lacks, so a new field
    # does not surface later as "no such column" from a query.
    add_missing_columns(engine, Base.metadata)

    with engine.begin() as conn:
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

    # Single-tenant POC: come up empty so one client never sees another's
    # data. Runs last, after create_all and the self-migration, so every table
    # it wants to empty exists. See services/maintenance/reset.py.
    if get_settings().reset_on_startup:
        from app.services.datasets.storage import UPLOAD_DIR
        from app.services.maintenance import reset

        reset.reset_on_startup(UPLOAD_DIR)


app.include_router(auth.router)
app.include_router(datasets.router)
app.include_router(training.router)
app.include_router(forecast.router)
app.include_router(forecast_workspace.router)
app.include_router(recommendations.router)
app.include_router(assistant.router)
app.include_router(data_quality.router)


@app.get("/health")
def health():
    return {"status": "UP", "service": "backend-sodexo-application-api"}


@app.get("/")
def root():
    return {"service": "Sodexo Healthcare Forecasting POC - Application API", "docs": "/docs", "health": "/health"}
