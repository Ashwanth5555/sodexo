"""Rolling-origin cross-validation and seasonal-period selection - ported from
Meriton's `_rolling_folds`/`_fold_validation_size`/`_seasonal_period`, sized
for the half-day grain this engine forecasts at (one row per 12-hour block).
"""
import pandas as pd

from app.services.forecasting.contract import SEASONAL_PERIOD_CANDIDATES, SLOTS_PER_WEEK


def fold_validation_size(length: int, cycle: int = SLOTS_PER_WEEK) -> int:
    """Four cycles of rows per fold when there's enough history, and never less
    than one full cycle, scaled down for shorter series.

    `cycle` is the series' dominant seasonal cycle in its own periods - 14
    half-day slots for the built-in series, 7 for a daily upload, 12 for a
    monthly one.
    """
    return min(cycle * 4, max(cycle, length // 8))


def holdout_size(length: int, cycle: int = SLOTS_PER_WEEK) -> int:
    """Sized to match the TOTAL test rows a full 3-fold rolling-origin CV
    would use (3x one fold), so a model evaluated by a single fast holdout
    is judged on the same amount of test data as one evaluated by CV -
    otherwise their reported test_rows (and the metrics computed from them)
    aren't comparable."""
    if length < 10:
        return max(1, length // 3)
    target = fold_validation_size(length, cycle) * 3
    return min(target, max(1, length // 2))


def smallest_fold_train_size(length: int, cycle: int = SLOTS_PER_WEEK) -> int:
    """The smallest training slice any rolling-origin fold will actually
    use (fold 0's). Seasonal-period selection and eligibility must be
    checked against THIS, not the full window - a period that only fits in
    the full series but not in an individual fold makes every fold's fit
    fail at runtime."""
    validation_size = fold_validation_size(length, cycle)
    candidate = length - validation_size * 3
    return candidate if candidate > 0 else length


def rolling_folds(length: int, validation_size: int) -> list[tuple[int, int]]:
    """Three expanding-window folds over the tail of the series. Returns []
    when there isn't enough history for 3 honest folds, so callers fall back
    to a single chronological holdout."""
    min_train = max(30, validation_size * 2)
    if length < min_train + validation_size * 3:
        return []
    folds = []
    for fold_index in range(3):
        validation_start = length - validation_size * (3 - fold_index)
        validation_end = validation_start + validation_size
        folds.append((validation_start, validation_end))
    return folds


def evaluation_mode(length: int, validation_size: int) -> str:
    return "rolling_origin" if len(rolling_folds(length, validation_size)) >= 3 else "holdout_fallback"


def resolve_seasonal_period(
    target: pd.Series,
    candidates: tuple[int, ...] = SEASONAL_PERIOD_CANDIDATES,
    cycle: int = SLOTS_PER_WEEK,
) -> int | None:
    """Pick whichever seasonal-cycle candidate (daily=2 slots, weekly=14 slots
    at this grain) has the stronger autocorrelation, requiring 2 full cycles of
    history in the SMALLEST fold a rolling-origin CV will actually fit on -
    not just the full window - so the chosen period never fails inside an
    individual fold. Returns None if no candidate qualifies."""
    values = target.dropna().astype(float)
    reference_length = smallest_fold_train_size(len(values), cycle)
    best_period = None
    best_strength = -1.0
    for period in candidates:
        if reference_length < period * 2:
            continue
        strength = abs(values.autocorr(lag=period) or 0.0)
        if strength > best_strength:
            best_strength = strength
            best_period = period
    return best_period
