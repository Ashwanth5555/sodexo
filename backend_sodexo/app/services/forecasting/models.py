"""The model fitters - ported from Meriton's training_service.py with the
same core approach per model, adapted from daily/panel-shaped data to a
single fixed hourly series with a small, calendar-only exogenous set.
"""
import statistics

import numpy as np
import pandas as pd

from app.services.forecasting.contract import (
    AUTO_ARIMA_MAX_SEASONAL_PERIOD,
    RANDOM_SEED,
    SLOTS_PER_DAY,
    SLOTS_PER_WEEK,
    XGB_LAG_SLOTS,
    XGB_ROLLING_SLOTS,
)


class ModelContext:
    """Per-series fitting context.

    The grain fields default to the canonical half-day constants from
    contract.py, so the built-in series behave exactly as before. An uploaded
    dataset forecast at a different frequency (hourly, daily, weekly, monthly)
    passes its own, which is what lets the same portfolio run on both.
    """

    def __init__(
        self,
        seasonal_period: int | None,
        exog_columns: tuple[str, ...],
        periods_per_day: int = SLOTS_PER_DAY,
        periods_per_week: int = SLOTS_PER_WEEK,
        xgb_lags: tuple[int, ...] = XGB_LAG_SLOTS,
        xgb_rollings: tuple[int, ...] = XGB_ROLLING_SLOTS,
        max_seasonal_period: int = AUTO_ARIMA_MAX_SEASONAL_PERIOD,
        var_pair_reason: str | None = None,
    ):
        self.seasonal_period = seasonal_period
        self.exog_columns = exog_columns
        self.periods_per_day = periods_per_day
        self.periods_per_week = periods_per_week
        self.xgb_lags = xgb_lags
        self.xgb_rollings = xgb_rollings
        self.max_seasonal_period = max_seasonal_period
        # Why this series has no second series for VAR, when that is knowable
        # up front (an uploaded file with no driver column chosen).
        self.var_pair_reason = var_pair_reason


def _require_rows(df: pd.DataFrame, minimum: int, model_name: str) -> None:
    if len(df) < minimum:
        raise ValueError(f"{model_name} requires at least {minimum} training rows.")


_MAX_EXOG_CORRELATION = 0.95


def _independent_exog_columns(train: pd.DataFrame) -> list[str]:
    """Selects a numerically well-conditioned subset of the standardized
    exogenous columns, in the caller's order.

    Three real defects in the raw contract set make an unfiltered matrix
    unusable for the regression-based fitters (SARIMAX/ARIMA/VAR with exog):
    the four season dummies sum to 1 and so are perfectly collinear with the
    intercept statsmodels adds (the classic dummy-variable trap); is_holiday
    and demand_multiplier are the same signal twice; and sales_amount is
    near-duplicate of transaction_count. Left in, they push the matrix
    condition number past 1e18 and the exog variants score *worse* than their
    plain counterparts.

    A column is kept only if it varies, is not near-duplicate of a column
    already kept, and genuinely raises the rank of [intercept | kept...] -
    the intercept is part of the rank test precisely so the dummy trap is
    caught rather than missed (the dummies are independent on their own)."""
    kept: list[str] = []
    for column in train.columns:
        values = train[column].to_numpy(dtype=float)
        if not np.isfinite(values).all() or np.allclose(values, values[0]):
            continue
        if kept and np.max(np.abs([np.corrcoef(values, train[k].to_numpy(dtype=float))[0, 1] for k in kept])) > _MAX_EXOG_CORRELATION:
            continue
        candidate = np.column_stack([np.ones(len(train)), train[kept + [column]].to_numpy(dtype=float)])
        if np.linalg.matrix_rank(candidate) < candidate.shape[1]:
            continue
        kept.append(column)
    return kept


def _exog_pair(train_df: pd.DataFrame, output_df: pd.DataFrame, exog_columns: tuple[str, ...]):
    train = train_df[list(exog_columns)].astype(float)
    output = output_df[list(exog_columns)].astype(float)
    center = train.mean(axis=0)
    scale = train.std(axis=0, ddof=0).replace(0, 1.0)
    train = (train - center) / scale
    output = (output - center) / scale
    if not np.isfinite(train.to_numpy()).all() or not np.isfinite(output.to_numpy()).all():
        raise ValueError("Exogenous matrix contains non-finite values after standardization.")
    usable = _independent_exog_columns(train)
    if not usable:
        raise ValueError("No exogenous driver in this window varies independently enough to regress on.")
    return train[usable], output[usable]


def _clean_predictions(predictions, expected_length: int) -> pd.Series:
    values = pd.Series(predictions).astype(float).reset_index(drop=True)
    if len(values) != expected_length:
        raise ValueError(f"Expected {expected_length} predictions, got {len(values)}.")
    return values.replace([np.inf, -np.inf], np.nan)


# --- SARIMAX ---------------------------------------------------------------

def fit_sarimax(train_df, output_df, ctx: ModelContext, use_exog: bool):
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    _require_rows(train_df, 12, "SARIMAX")
    exog_train, exog_future = _exog_pair(train_df, output_df, ctx.exog_columns) if use_exog else (None, None)
    period = ctx.seasonal_period
    # A real seasonal component (weekly AR + MA terms). This used to be
    # (0,0,0,period) - every seasonal term zero, which made "SARIMAX" a plain
    # ARIMA(2,1,1) with no weekly structure at all. The exog variant hid the
    # gap because its season dummies and 7-day lags carried the weekly signal;
    # the plain variant had nothing, and underperformed accordingly.
    use_seasonal = bool(period and len(train_df) >= period * 3)
    seasonal_order = (1, 0, 1, period) if use_seasonal else (0, 0, 0, 0)
    # statsmodels rejects a model where a non-seasonal AR lag coincides with a
    # seasonal one. With order=(2,1,1) that happens whenever the period is 2 -
    # so drop the non-seasonal AR to 1 in that case rather than failing the fit.
    ar_order = 1 if use_seasonal and period is not None and period <= 2 else 2
    model = SARIMAX(
        train_df["target"].astype(float), exog=exog_train, order=(ar_order, 1, 1), seasonal_order=seasonal_order,
        enforce_stationarity=False, enforce_invertibility=False,
    ).fit(disp=False)
    return _clean_predictions(model.forecast(steps=len(output_df), exog=exog_future), len(output_df))


# --- Auto ARIMA -------------------------------------------------------------

def fit_auto_arima(train_df, output_df, ctx: ModelContext, use_exog: bool):
    import pmdarima as pm

    _require_rows(train_df, 24, "Auto ARIMA")
    x_train, x_future = _exog_pair(train_df, output_df, ctx.exog_columns) if use_exog else (None, None)
    period = ctx.seasonal_period
    seasonal = bool(period and period <= ctx.max_seasonal_period and len(train_df) >= period * 2)
    # A wider order search than the hourly grain could afford - a daily series
    # is ~24x shorter, so the stepwise search is cheap here and the extra
    # flexibility matters when an exogenous matrix is also in play.
    model = pm.auto_arima(
        train_df["target"].astype(float), X=x_train, start_p=1, start_q=1, max_p=3, max_q=3, max_d=2,
        m=period if seasonal else 1, seasonal=seasonal, D=0 if seasonal else None, max_P=2, max_D=1, max_Q=2,
        max_order=8, information_criterion="aicc",
        error_action="ignore", suppress_warnings=True, stepwise=True, trace=False,
    )
    return _clean_predictions(model.predict(n_periods=len(output_df), X=x_future), len(output_df))


# --- XGBoost -----------------------------------------------------------------

def _lag(history, steps):
    if not history:
        return 0.0
    return history[-steps] if len(history) >= steps else _rolling(history, min(len(history), steps))


def _rolling(history, window):
    if not history:
        return 0.0
    tail = history[-window:] if len(history) >= window else history
    return sum(tail) / len(tail)


def _rolling_median(history, window):
    if not history:
        return 0.0
    return float(statistics.median(history[-window:]))


def _rolling_std(history, window):
    tail = history[-window:]
    return float(statistics.pstdev(tail)) if len(tail) > 1 else 0.0


def _xgb_features(slot_of_day, dayofweek, month, history, use_exog, ctx, row=None, exog_columns=()):
    """Lag/rolling windows are in PERIODS of the series' own grain - one row is
    one half-day block for the built-in series, or one hour/day/week/month for
    an uploaded one - so slot_of_day is a real predictor alongside day of week."""
    features = {"slot_of_day": slot_of_day, "dayofweek": dayofweek, "month": month, "trend_index": len(history)}
    for slots in ctx.xgb_lags:
        features[f"lag_{slots}"] = _lag(history, slots)
    for slots in ctx.xgb_rollings:
        features[f"rolling_{slots}"] = _rolling(history, slots)
    features["rolling_median_1w"] = _rolling_median(history, ctx.periods_per_week)
    features["rolling_std_1w"] = _rolling_std(history, ctx.periods_per_week)
    if use_exog:
        for column in exog_columns:
            features[column] = float(row[column]) if row is not None else 0.0
    return features


def _xgb_training_matrix(train_df, use_exog, exog_columns, ctx):
    history = train_df["target"].astype(float).tolist()
    rows, targets = [], []
    for index in range(1, len(train_df)):
        row = train_df.iloc[index]
        rows.append(_xgb_features(int(row["hour_of_day"]), int(row["dayofweek"]), int(row["month"]), history[:index], use_exog, ctx, row=row, exog_columns=exog_columns))
        targets.append(history[index])
    return pd.DataFrame(rows), pd.Series(targets)


def fit_xgboost(train_df, output_df, ctx: ModelContext, use_exog: bool):
    from xgboost import XGBRegressor

    exog_columns = ctx.exog_columns if use_exog else ()
    x_train, y_train = _xgb_training_matrix(train_df, use_exog, exog_columns, ctx)
    if len(x_train) < 5:
        raise ValueError("Not enough lagged rows for XGBoost.")
    # Fixed, sensible hyperparameters instead of a grid search - a grid
    # search multiplies this model's cost by (combos x cv folds) on top of
    # the 3x rolling-origin CV it already runs, which dominated training
    # time for no real accuracy gain on this dataset's size.
    model = XGBRegressor(
        objective="reg:squarederror", random_state=RANDOM_SEED, n_jobs=1,
        learning_rate=0.1, max_depth=4, n_estimators=100, subsample=0.8, colsample_bytree=0.8,
    )
    model.fit(x_train, y_train)

    history = train_df["target"].astype(float).tolist()
    feature_columns = list(x_train.columns)
    predictions = []
    for _index, row in output_df.iterrows():
        features = pd.DataFrame([_xgb_features(int(row["hour_of_day"]), int(row["dayofweek"]), int(row["month"]), history, use_exog, ctx, row=row, exog_columns=exog_columns)])
        features = features.reindex(columns=feature_columns, fill_value=0.0)
        prediction = float(model.predict(features)[0])
        predictions.append(prediction)
        history.append(float(row["target"]) if pd.notna(row.get("target")) else prediction)
    return _clean_predictions(predictions, len(output_df))


# --- Exponential Smoothing ---------------------------------------------------

def fit_exp_smoothing(train_df, output_df, ctx: ModelContext, seasonal: str, damped: bool):
    from statsmodels.tsa.api import ExponentialSmoothing

    _require_rows(train_df, 24, "Exponential Smoothing")
    y = pd.Series(train_df["target"].astype(float).values)
    if seasonal == "mul" and (y <= 0).any():
        raise ValueError("Multiplicative smoothing requires strictly positive target values.")
    period = ctx.seasonal_period
    if not period or len(y) < period * 2:
        raise ValueError("Exponential Smoothing requires at least two complete seasonal cycles.")
    try:
        model = ExponentialSmoothing(
            y, seasonal_periods=period, trend="add", seasonal=seasonal, damped_trend=damped, initialization_method="estimated",
        ).fit(optimized=True)
    except TypeError:
        model = ExponentialSmoothing(y, seasonal_periods=period, trend="add", seasonal=seasonal, damped=damped).fit(optimized=True)
    return _clean_predictions(model.forecast(len(output_df)), len(output_df))


# --- VAR ----------------------------------------------------------------------

def _var_lag_order(endog: pd.DataFrame, ctx: ModelContext, exog_count: int = 0) -> int:
    # A full weekly lag structure on top of an exogenous block gives VAR a lot
    # of coefficients to estimate, and the errors compound across a 7-step
    # forecast. With exog present the weekly signal is already carried by the
    # season dummies and 7-day lags, so a shorter endogenous memory forecasts
    # more stably than a longer one.
    preferred = min(ctx.periods_per_day if exog_count else ctx.periods_per_week, max(1, len(endog) // 10))
    max_supported = (len(endog) - exog_count - 2) // (endog.shape[1] + 1)
    if max_supported < 1:
        raise ValueError("VAR does not have enough observations for the available series and features.")
    return min(preferred, max_supported)


def fit_var(train_df, output_df, ctx: ModelContext, use_exog: bool):
    from statsmodels.tsa.api import VAR

    endog = train_df[["target", "var_pair"]].astype(float).reset_index(drop=True)
    if use_exog:
        exog_train, exog_future = _exog_pair(train_df, output_df, ctx.exog_columns)
        varying = [column for column in exog_train.columns if exog_train[column].nunique(dropna=False) > 1]
        if not varying:
            raise ValueError("VAR with exogenous variables requires at least one varying exogenous feature.")
        exog_train, exog_future = exog_train[varying], exog_future[varying]
        maxlags = _var_lag_order(endog, ctx, exog_train.shape[1])
        model = VAR(endog, exog=exog_train).fit(maxlags=maxlags, ic=None, trend="c")
        lag_order = model.k_ar
        forecast_input = endog.values[-lag_order:] if lag_order else endog.values[-1:]
        forecast = model.forecast(forecast_input, steps=len(output_df), exog_future=exog_future.to_numpy())
    else:
        maxlags = _var_lag_order(endog, ctx)
        model = VAR(endog).fit(maxlags=maxlags, ic=None, trend="c")
        lag_order = model.k_ar
        forecast_input = endog.values[-lag_order:] if lag_order else endog.values[-1:]
        forecast = model.forecast(forecast_input, steps=len(output_df))
    return _clean_predictions(forecast[:, 0], len(output_df))


# --- LSTM -----------------------------------------------------------------

def fit_lstm(train_df, output_df, ctx: ModelContext):
    from sklearn.preprocessing import MinMaxScaler
    from tensorflow.keras.callbacks import EarlyStopping
    from tensorflow.keras.layers import Dense, LSTM
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.utils import set_random_seed

    set_random_seed(RANDOM_SEED)
    try:
        import tensorflow as tf
        tf.config.experimental.enable_op_determinism()
    except (AttributeError, RuntimeError):
        pass

    # Three weekly cycles of half-day rows - enough context to see the weekly
    # shape repeat rather than inferring it from a single pass.
    sequence_length = min(ctx.periods_per_week * 3, max(3, len(train_df) // 4))
    if len(train_df) <= sequence_length + 2:
        raise ValueError("Not enough rows for LSTM sequence training.")

    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(train_df[["target"]].astype(float)).reshape(-1)
    x_train, y_train = [], []
    for index in range(sequence_length, len(scaled)):
        x_train.append(scaled[index - sequence_length:index])
        y_train.append(scaled[index])
    x_train = np.array(x_train).reshape((-1, sequence_length, 1))
    y_train = np.array(y_train)

    model = Sequential()
    model.add(LSTM(32, input_shape=(sequence_length, 1)))
    model.add(Dense(1))
    model.compile(loss="mse", optimizer="adam")
    # epochs=8/batch=128 was tuned for the old hourly frames; on ~320 daily
    # sequences that is only ~3 batches an epoch, so the net saw roughly 24
    # gradient updates and never converged. A small batch with early stopping
    # trains properly and is still quick at this size.
    model.fit(
        x_train, y_train, epochs=80, batch_size=16, verbose=0, shuffle=False,
        callbacks=[EarlyStopping(monitor="loss", patience=8, restore_best_weights=True)],
    )

    history = list(scaled)
    predictions = []
    for _index, row in output_df.iterrows():
        window = np.array(history[-sequence_length:]).reshape((1, sequence_length, 1))
        predicted_scaled = float(model(window, training=False).numpy()[0][0])
        predicted = float(scaler.inverse_transform([[predicted_scaled]])[0][0])
        predictions.append(predicted)
        if pd.notna(row.get("target")):
            history.append(float(scaler.transform([[float(row["target"])]])[0][0]))
        else:
            history.append(predicted_scaled)
    return _clean_predictions(predictions, len(output_df))


FITTERS = {
    "sarimax": lambda train_df, output_df, ctx: fit_sarimax(train_df, output_df, ctx, use_exog=False),
    "sarimax_exog": lambda train_df, output_df, ctx: fit_sarimax(train_df, output_df, ctx, use_exog=True),
    "auto_arima": lambda train_df, output_df, ctx: fit_auto_arima(train_df, output_df, ctx, use_exog=False),
    "auto_arima_exog": lambda train_df, output_df, ctx: fit_auto_arima(train_df, output_df, ctx, use_exog=True),
    "xgboost": lambda train_df, output_df, ctx: fit_xgboost(train_df, output_df, ctx, use_exog=False),
    "xgboost_exog": lambda train_df, output_df, ctx: fit_xgboost(train_df, output_df, ctx, use_exog=True),
    "exp_additive": lambda train_df, output_df, ctx: fit_exp_smoothing(train_df, output_df, ctx, seasonal="add", damped=False),
    "exp_additive_damped": lambda train_df, output_df, ctx: fit_exp_smoothing(train_df, output_df, ctx, seasonal="add", damped=True),
    "exp_multiplicative": lambda train_df, output_df, ctx: fit_exp_smoothing(train_df, output_df, ctx, seasonal="mul", damped=False),
    "exp_multiplicative_damped": lambda train_df, output_df, ctx: fit_exp_smoothing(train_df, output_df, ctx, seasonal="mul", damped=True),
    "var": lambda train_df, output_df, ctx: fit_var(train_df, output_df, ctx, use_exog=False),
    "var_exog": lambda train_df, output_df, ctx: fit_var(train_df, output_df, ctx, use_exog=True),
    "lstm": lambda train_df, output_df, ctx: fit_lstm(train_df, output_df, ctx),
}
