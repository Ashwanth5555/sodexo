"""MAPE/MAE/RMSE/WAPE/Bias metrics - ported near-verbatim from Meriton's
metrics_service.py. Generic, correct math with no dataset-specific
assumptions, so no adaptation needed.
"""
import math
from statistics import mean


def calculate_metrics(actuals, predictions) -> dict:
    pairs = []
    for actual, predicted in zip(actuals, predictions):
        actual_value = _to_float(actual)
        predicted_value = _to_float(predicted)
        if actual_value is not None and predicted_value is not None:
            pairs.append((actual_value, predicted_value))

    if not pairs:
        return {"mape": None, "accuracy": None, "mae": None, "rmse": None, "wape": None, "bias": None}

    abs_errors = [abs(actual - predicted) for actual, predicted in pairs]
    squared_errors = [(actual - predicted) ** 2 for actual, predicted in pairs]
    nonzero_pairs = [(actual, predicted) for actual, predicted in pairs if actual != 0]
    total_actual = sum(actual for actual, _predicted in pairs)

    mape = (
        mean(abs((actual - predicted) / actual) for actual, predicted in nonzero_pairs) * 100
        if nonzero_pairs
        else None
    )
    mae = mean(abs_errors)
    rmse = math.sqrt(mean(squared_errors))
    wape = (sum(abs_errors) / total_actual * 100) if total_actual else None
    bias = (sum(predicted - actual for actual, predicted in pairs) / total_actual * 100) if total_actual else None
    accuracy = max(100 - mape, 0) if mape is not None else None

    return {
        "mape": _round_or_none(mape),
        "accuracy": _round_or_none(accuracy),
        "mae": _round_or_none(mae),
        "rmse": _round_or_none(rmse),
        "wape": _round_or_none(wape),
        "bias": _round_or_none(bias),
    }


def is_valid_metric(row: dict) -> bool:
    if not isinstance(row, dict) or str(row.get("status", "")).lower() == "failed":
        return False
    values = [_to_float(row.get(key)) for key in ("mape", "mae", "rmse")]
    if any(value is None or not math.isfinite(value) for value in values):
        return False
    mape, mae, rmse = values
    return 0 <= mape <= 1000 and 0 <= mae <= 1e12 and 0 <= rmse <= 1e12


def average_metrics(metrics: list[dict]) -> dict:
    averaged = {}
    for key in ("mape", "accuracy", "mae", "rmse", "wape", "bias"):
        values = [metric[key] for metric in metrics if metric.get(key) is not None]
        averaged[key] = round(sum(values) / len(values), 2) if values else None
    return averaged


def _to_float(value):
    if value is None:
        return None
    try:
        if math.isnan(float(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value):
    return round(value, 2) if value is not None else None
