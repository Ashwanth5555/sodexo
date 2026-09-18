"""Per-series, per-model applicability checks - ported from Meriton's
`_model_eligibility`/`_series_ineligibility`/dependency_service. A model that
fails a gate is reported as not_applicable with a reason rather than being
silently dropped, so the leaderboard always shows every model.
"""
import importlib.util

import pandas as pd

from app.services.forecasting.contract import SLOTS_PER_WEEK
from app.services.forecasting.validation_plan import smallest_fold_train_size

_DEPENDENCY_MODULES = {"statsmodels", "pmdarima", "xgboost", "tensorflow"}


def dependency_available(module_name: str | None) -> tuple[bool, str | None]:
    if module_name is None or module_name not in _DEPENDENCY_MODULES:
        return True, None
    if importlib.util.find_spec(module_name) is not None:
        return True, None
    return False, f"{module_name} is not installed in the server environment."


def check_eligibility(
    model_id: str, dependency_module: str | None, train_df: pd.DataFrame, seasonal_period: int | None,
    exog_columns: tuple[str, ...] = (), cycle: int = SLOTS_PER_WEEK, var_pair_reason: str | None = None,
) -> tuple[bool, str | None]:
    available, reason = dependency_available(dependency_module)
    if not available:
        return False, reason

    values = train_df["target"].dropna().astype(float)
    if values.empty:
        return False, "The training window contains no valid target values."
    if len(values) < 10:
        return False, "Fewer than 10 valid training rows are available in the training window."

    if model_id in {"var", "var_exog"}:
        pair = train_df["var_pair"].dropna().astype(float)
        if values.nunique() <= 1 or pair.nunique() <= 1:
            return False, var_pair_reason or "VAR requires two non-constant, co-evolving series; one of the target or paired series is constant in this window."

    if model_id == "var_exog":
        if not exog_columns:
            return False, "No exogenous drivers are currently enabled for this series - turn at least one on in Demand Drivers & Feature Mapping."
        if not any(train_df[column].nunique(dropna=False) > 1 for column in exog_columns):
            return False, "VAR with exogenous variables requires at least one exogenous driver that actually varies in this training window (e.g. no holiday fell within it)."

    if model_id.startswith("exp_"):
        # Checked against the smallest CV fold's size, not the full window -
        # a period that only fits the full series but not an individual
        # fold would make every fold's fit fail at runtime.
        reference_length = smallest_fold_train_size(len(values), cycle)
        if not seasonal_period or reference_length < max(24, seasonal_period * 2):
            return False, "Exponential Smoothing requires at least two complete seasonal cycles of history in every rolling-origin fold, not just the full window."
        if "multiplicative" in model_id and (values <= 0).any():
            return False, "Multiplicative smoothing requires strictly positive target values, but this series has zero/negative hours."

    return True, None
