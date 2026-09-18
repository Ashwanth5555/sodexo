from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import adt, erp, external, payroll, pos, ukg, workday
from app.services import source_reader as sr

app = FastAPI(
    title="Sodexo Healthcare POC - Mock Enterprise API",
    description=(
        "Simulated UKG/Kronos, Workday/HRIS, ADT (patient census), POS (cafe), Payroll, "
        "and ERP source-system connectors for a Sodexo Healthcare account, backed by a "
        "representative synthetic dataset. No real Sodexo, hospital, patient, or "
        "employee data is included."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ukg.router)
app.include_router(workday.router)
app.include_router(adt.router)
app.include_router(pos.router)
app.include_router(payroll.router)
app.include_router(erp.router)
app.include_router(external.router)


@app.on_event("startup")
def _startup() -> None:
    sr.load_all()


@app.get("/health")
def health():
    return {
        "status": "UP",
        "service": "sodexo-mock-enterprise-api",
        "sheets_loaded": list(sr._cache.keys()) if sr._cache else [],
    }


@app.get("/")
def root():
    return {
        "service": "Sodexo Healthcare POC - Mock Enterprise API",
        "docs": "/docs",
        "health": "/health",
        "sources": ["ukg", "workday", "adt", "pos", "payroll", "erp", "external"],
    }
