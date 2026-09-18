"""Orchestrates every Predictive Analytics risk model. Turnover Risk is
reported as unsupported rather than forced - this dataset has no
termination/tenure-end field, so a real turnover model would need
fabricated labels (confirmed with the user when scoping this phase)."""
from sqlalchemy.orm import Session

from app.services.predictions import employee_risk, staffing_gap_risk
from app.services.predictions.common import ModelNotApplicable

TURNOVER_RISK_NOTE = {
    "model_id": "turnover_risk", "model_label": "Turnover Risk", "status": "not_applicable",
    "reason": "The dataset has no employee termination/tenure-end field, so a real turnover model would need fabricated labels. Excluded rather than forced - add a termination-date field to the generator to make this supportable.",
}

_EMPLOYEE_MODELS = [
    ("overtime_risk", employee_risk.overtime_risk),
    ("absenteeism_risk", employee_risk.absenteeism_risk),
    ("schedule_adherence_risk", employee_risk.schedule_adherence_risk),
    ("payroll_anomaly_risk", employee_risk.payroll_anomaly_risk),
]


def compute_all(db: Session) -> dict:
    models = []
    for model_id, fn in _EMPLOYEE_MODELS:
        try:
            result = fn(db)
            result["status"] = "completed"
            models.append(result)
        except ModelNotApplicable as exc:
            models.append({"model_id": model_id, "status": "not_applicable", "reason": str(exc)})

    staffing_gap = staffing_gap_risk.compute(db)
    return {"employee_models": models, "staffing_gap_risk": staffing_gap, "turnover_risk": TURNOVER_RISK_NOTE}
