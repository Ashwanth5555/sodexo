"""One-off smoke test: run the forecasting engine for a single series and
print each model's terminal status. Not part of the app - just to verify the
ported 13-model engine end to end before running the full 4-series job."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend_sodexo"))

from app.core.database import SessionLocal
from app.services.forecasting.contract import SERIES_BY_KEY
from app.services.forecasting.engine import train_series

db = SessionLocal()
series = SERIES_BY_KEY["LOC01_FOOD_NUTRITION"]
started = time.perf_counter()
runs = train_series(db, series, job_id="SMOKE-TEST-1")
print(f"Total time: {time.perf_counter() - started:.1f}s")
for run in runs:
    print(f"{run.model_id:28s} status={run.status:15s} mape={run.mape} rank={run.rank} champion={run.is_champion} reason={run.failure_reason}")
db.close()
