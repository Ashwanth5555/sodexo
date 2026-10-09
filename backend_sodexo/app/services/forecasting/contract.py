"""Forecasting constants shared by every series.

This was the design-time contract for a fixed Sodexo schema - four series
hardcoded here, with their own tables and target column. That schema is
gone: every series now comes from a file the client uploaded, and its grain,
seasonality and horizon are resolved per dataset in datasets/series.py.

What remains is the portfolio definition (which models exist, which are slow,
which are evaluated on a holdout) plus the half-day-grain constants the
engine still reads. SERIES is empty and SeriesConfig unused by any live path;
both are kept so the built-in training entry point stays a no-op rather than
an import error.
"""
from dataclasses import dataclass


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

# How much work SARIMAX and Exponential Smoothing may be asked to do for a
# seasonal component. Both carry one state per seasonal slot and cost roughly
# rows x period^2, so the limit has to account for both - a flat cap on the
# period alone would either let hourly data through or rule out an annual cycle
# on weekly data, which is cheap and perfectly reasonable.
#
#   hourly, 2 years, period 168 : 17,520 x 168^2 = 494M  - never finishes
#   hourly, 2 years, period  24 : 17,520 x  24^2 =  10M  - fine
#   weekly, 6 years, period  52 :    312 x  52^2 = 0.8M  - fine
#
# 50M sits well clear of both sides. Above it the model falls back to a shorter
# seasonal candidate, and the long cycle is left to XGBoost's lag features.
STATE_SPACE_SEASONAL_BUDGET = 50_000_000

# Auto ARIMA (a stepwise order search) and LSTM (a neural-net training loop)
# cost far more per fit than the rest of the portfolio, and running them
# through 3 CV folds triples that. They use a single chronological holdout
# instead - and because holdout_size() is deliberately sized to 3x one fold,
# they are still scored on exactly the same 84 test rows as every
# CV-evaluated model, so the leaderboard stays apples-to-apples.
FAST_HOLDOUT_MODEL_IDS = frozenset({"auto_arima", "auto_arima_exog", "lstm"})

# The same three models, for a different reason: they dominate training cost.
# Measured over one full 4-series run at half-day grain, the totals were
# LSTM 1054s, Auto ARIMA 968s and Auto ARIMA+exog 882s - 88% of all model
# time - while the champions were Exponential Smoothing (19s across all four
# series) and VAR (under a second). Worth fitting when there is time to
# spare; not worth making a client wait for on a 40-series upload. This is
# what the "fast" training profile skips.
SLOW_MODEL_IDS = frozenset({"auto_arima", "auto_arima_exog", "lstm"})

SLOW_MODEL_SKIP_REASON = (
    "Skipped in fast training mode - one of the three slowest models in the portfolio "
    "(together 88% of training time), and it won on no series in benchmarking. "
    "Set TRAINING_PROFILE=full to include it."
)

CALENDAR_EXOG_COLUMNS =("is_holiday", "demand_multiplier", "season_Spring", "season_Summer", "season_Fall", "season_Winter")

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
    fact_model: type | None
    date_column: str
    hour_column: str
    target_column: str
    var_pair_column: str
    extra_exog_columns: tuple[str, ...] = ()
    lagged_raw_columns: tuple[str, ...] = ()


# No built-in series. This deployment forecasts whatever the client uploads
# and nothing else - there is no Sodexo canonical dataset, so there are no
# design-time series to fit. Every loop over SERIES is therefore a no-op,
# which is what makes the built-in training path inert rather than removed.
SERIES: list[SeriesConfig] = []

SERIES_BY_KEY: dict[str, SeriesConfig] = {}

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
