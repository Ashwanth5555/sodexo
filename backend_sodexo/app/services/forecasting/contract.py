"""Static per-series configuration - the equivalent of Meriton's Forecast
Contract, but fixed at design time instead of profiled from an uploaded CSV,
since our schema (2 locations x 2 departments, target=required_manpower) is
already known.
"""
from dataclasses import dataclass

from app.models import FactCafeSales, FactCensus

RANDOM_SEED = 42

# --- Forecasting grain -----------------------------------------------------
# Models forecast the staffing requirement per HALF-DAY block (AM 00:00-11:59,
# PM 12:00-23:59) in staff-hours, not per hour.
#
# Grain is a measured trade-off, not a guess. Hour-to-hour variation here is
# dominated by irreducible noise on tiny integer targets (a quarter of
# Community Hospital's hours sit at exactly 1 required staff, where being off
# by one is a 100% error), so achievable hourly accuracy caps out around
# 50-83% even for a perfect oracle. Aggregating averages that noise away, but
# too far and every model converges on the same easy weekly pattern:
#
#   grain        champions      weakest model   spread
#   daily 24h    92.1-97.7%     89.0%           1.6-3.3pt
#   half-day     90.5-97.5%     85.2%           1.4-5.6pt   <- chosen
#   shift 8h     86.4-95.0%     77.9%           up to 8.5pt
#
# Half-day is the finest grain at which every series' champion still clears
# 90%, while leaving a genuine spread between strong and weak models. The
# hour-of-day shape is still shown in Data Analysis, Drift Monitoring and
# Staffing Impact, which read the raw hourly facts directly.
BLOCK_HOURS = 12
SLOTS_PER_DAY = 24 // BLOCK_HOURS          # 2
SLOTS_PER_WEEK = SLOTS_PER_DAY * 7         # 14
MAX_TRAINING_SLOTS = SLOTS_PER_WEEK * 26   # ~180 days of half-day rows
FORECAST_HORIZON_SLOTS = SLOTS_PER_WEEK    # 7 days ahead

# Weekly only. The AM/PM alternation is a 2-slot cycle, but a period of 2 is
# degenerate for the ARIMA-family fitters - their non-seasonal AR order of 2
# collides with the seasonal lag at 1x2, which statsmodels rejects outright.
# A weekly period is a multiple of the AM/PM cycle and so already encodes
# "same block, same weekday", and XGBoost gets the block explicitly via its
# slot_of_day feature.
SEASONAL_PERIOD_CANDIDATES = (SLOTS_PER_WEEK,)
# pmdarima's seasonal search stays affordable at a weekly period.
AUTO_ARIMA_MAX_SEASONAL_PERIOD = SLOTS_PER_WEEK

# Auto ARIMA (a stepwise order search) and LSTM (a neural-net training loop)
# cost far more per fit than the rest of the portfolio, and running them
# through 3 CV folds triples that. They use a single chronological holdout
# instead - and because holdout_size() is deliberately sized to 3x one fold,
# they are still scored on exactly the same 84 test rows as every
# CV-evaluated model, so the leaderboard stays apples-to-apples.
FAST_HOLDOUT_MODEL_IDS = frozenset({"auto_arima", "auto_arima_exog", "lstm"})

CALENDAR_EXOG_COLUMNS = ("is_holiday", "demand_multiplier", "season_Spring", "season_Summer", "season_Fall", "season_Winter")

# Real census/sales columns, used as inputs only in their one-week-lagged form
# ("same half-day block, one week ago"). Because FORECAST_HORIZON_SLOTS is also
# one week, that lag always resolves to an already-observed actual for every
# step of the forecast horizon - never the same block being predicted, so it
# adds real signal without leaking the answer.
LAG_SLOTS = FORECAST_HORIZON_SLOTS
LAG_SUFFIX = "_lag1w"

# XGBoost's own lag/rolling windows, in SLOTS to match the grain above:
# previous block, same block yesterday, same block last week / last fortnight.
XGB_LAG_SLOTS = (1, SLOTS_PER_DAY, SLOTS_PER_WEEK, SLOTS_PER_WEEK * 2)
XGB_ROLLING_SLOTS = (SLOTS_PER_WEEK, SLOTS_PER_WEEK * 4)


@dataclass(frozen=True)
class SeriesConfig:
    key: str
    label: str
    location_id: str
    fact_model: type
    date_column: str
    hour_column: str
    target_column: str
    var_pair_column: str
    extra_exog_columns: tuple[str, ...] = ()
    lagged_raw_columns: tuple[str, ...] = ()


SERIES: list[SeriesConfig] = [
    SeriesConfig("LOC01_FOOD_NUTRITION", "Community Hospital / Food & Nutrition Services", "LOC01", FactCensus, "business_date", "hour_of_day", "required_manpower", "occupied_beds", (), ("occupied_beds", "admissions_count", "discharges_count", "transfers_count", "meals_served")),
    SeriesConfig("LOC02_FOOD_NUTRITION", "Regional Medical Center / Food & Nutrition Services", "LOC02", FactCensus, "business_date", "hour_of_day", "required_manpower", "occupied_beds", (), ("occupied_beds", "admissions_count", "discharges_count", "transfers_count", "meals_served")),
    SeriesConfig("LOC01_CAFE", "Community Hospital / Retail & Cafe Services", "LOC01", FactCafeSales, "business_date", "hour_of_day", "required_manpower", "transaction_count", ("promotion_flag", "event_flag"), ("sales_amount", "transaction_count")),
    SeriesConfig("LOC02_CAFE", "Regional Medical Center / Retail & Cafe Services", "LOC02", FactCafeSales, "business_date", "hour_of_day", "required_manpower", "transaction_count", ("promotion_flag", "event_flag"), ("sales_amount", "transaction_count")),
]

SERIES_BY_KEY = {series.key: series for series in SERIES}

# (model_id, label, dependency_module or None)
MODEL_SPECS: list[tuple[str, str, str | None]] = [
    ("sarimax", "SARIMAX", "statsmodels"),
    ("sarimax_exog", "SARIMAX with exogenous variables", "statsmodels"),
    ("auto_arima", "Auto ARIMA", "pmdarima"),
    ("auto_arima_exog", "Auto ARIMA with exogenous variables", "pmdarima"),
    ("xgboost", "XGBoost", "xgboost"),
    ("xgboost_exog", "XGBoost with exogenous variables", "xgboost"),
    ("exp_additive", "Exponential Smoothing Additive", "statsmodels"),
    ("exp_additive_damped", "Exponential Smoothing Additive Damped", "statsmodels"),
    ("exp_multiplicative", "Exponential Smoothing Multiplicative", "statsmodels"),
    ("exp_multiplicative_damped", "Exponential Smoothing Multiplicative Damped", "statsmodels"),
    ("var", "VAR", "statsmodels"),
    ("var_exog", "VAR with exogenous variables", "statsmodels"),
    ("lstm", "LSTM", "tensorflow"),
]
