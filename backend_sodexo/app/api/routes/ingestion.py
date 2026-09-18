from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.ingestion import uploads
from app.services.ingestion.pipeline import run_full_pipeline

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


@router.post("/run")
def run_ingestion(db: Session = Depends(get_db)):
    """Extracts every source from the Sodexo mock API and upserts it into the
    canonical DB - the same pipeline scripts/run_sodexo_ingestion.py drives
    from the CLI, triggered here from the UI's "Run Ingestion" button."""
    started = datetime.utcnow()
    batches = run_full_pipeline(db)
    return {
        "started_at": started.isoformat(),
        "finished_at": datetime.utcnow().isoformat(),
        "batches": [
            {
                "source_system": batch.source_system,
                "source_endpoint": batch.source_endpoint,
                "status": batch.status,
                "records_received": batch.records_received,
                "records_loaded": batch.records_loaded,
                "records_rejected": batch.records_rejected,
                "error_message": batch.error_message,
            }
            for batch in batches
        ],
    }


@router.get("/status")
def ingestion_status(db: Session = Depends(get_db)):
    """The most recent batch per source endpoint - source health at a glance."""
    rows = db.execute(text(
        """
        SELECT b.source_system, b.source_endpoint, b.status, b.started_at, b.completed_at,
               b.records_received, b.records_loaded, b.records_rejected, b.error_message
        FROM ingestion_batch b
        WHERE b.started_at = (
            SELECT MAX(b2.started_at) FROM ingestion_batch b2 WHERE b2.source_endpoint = b.source_endpoint
        )
        ORDER BY b.source_system, b.source_endpoint
        """
    )).mappings().all()
    return {"sources": [dict(row) for row in rows]}


@router.get("/upload/datasets")
def upload_datasets():
    """The datasets a user may upload, with the columns each one expects."""
    return {
        "datasets": [
            {
                "key": spec.key,
                "label": spec.label,
                "source_system": spec.source_system,
                "required_columns": spec.required,
                "optional_columns": spec.optional,
            }
            for spec in uploads.DATASETS
        ],
        "accepted_extensions": sorted(uploads.ALLOWED_EXTENSIONS),
        "max_bytes": uploads.MAX_UPLOAD_BYTES,
    }


@router.get("/upload/template/{dataset}", response_class=PlainTextResponse)
def upload_template(dataset: str):
    """Header-only CSV for a dataset, so a file starts from the right columns."""
    try:
        body = uploads.template_csv(dataset)
    except uploads.UploadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{dataset}_template.csv"'},
    )


@router.post("/upload")
async def upload_dataset(
    dataset: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Load a CSV/Excel file into the canonical tables.

    Runs the same validators and loaders as the API pull, so rows that fail
    validation are quarantined and counted as rejected rather than aborting the
    upload. Only a problem with the file itself is a 400.
    """
    raw = await file.read()
    try:
        batch = uploads.ingest_upload(db, dataset, file.filename or "", raw)
    except uploads.UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Upload failed: {exc}") from exc

    return {
        "dataset": dataset,
        "filename": file.filename,
        "source_system": batch.source_system,
        "status": batch.status,
        "records_received": batch.records_received,
        "records_loaded": batch.records_loaded,
        "records_rejected": batch.records_rejected,
    }
