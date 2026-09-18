"""Loads one series' history from the canonical DB (fact_census /
fact_cafe_sales joined to dim_date) into the flat DataFrame shape the model
fitters expect, and builds the matching future calendar frame for forecasting
beyond the last known actual.
"""
from datetime import timedelta

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import SeriesFeatureSelection
from app.services.forecasting.contract import (
    BLOCK_HOURS,
    CALENDAR_EXOG_COLUMNS,
    LAG_SLOTS,
    LAG_SUFFIX,
    MAX_TRAINING_SLOTS,
    SeriesConfig,
)

_SEASON_COLUMNS = ("season_Spring", "season_Summer", "season_Fall", "season_Winter")


def load_series_history(db: Session, series: SeriesConfig) -> pd.DataFrame:
    table = series.fact_model.__tablename__
    extra_select = "".join(f", f.{column} AS {column}" for column in series.extra_exog_columns)
    lag_select = "".join(f", f.{column} AS {column}" for column in series.lagged_raw_columns)
    sql = text(
        f"""
        SELECT f.{series.date_column} AS business_date, f.{series.hour_column} AS hour_of_day,
               f.{series.target_column} AS target, f.{series.var_pair_column} AS var_pair
               {extra_select}{lag_select},
               d.season, d.is_holiday, d.demand_multiplier
        FROM {table} f
        JOIN dim_date d ON d.business_date = f.{series.date_column}
        WHERE f.location_id = :location_id
        ORDER BY f.{series.date_column}, f.{series.hour_column}
        """
    )
    df = pd.read_sql_query(sql, db.get_bind(), params={"location_id": series.location_id})
    df["business_date"] = pd.to_datetime(df["business_date"])
    df["date"] = df["business_date"] + pd.to_timedelta(df["hour_of_day"], unit="h")
    df["target"] = df["target"].astype(float)
    df["var_pair"] = df["var_pair"].astype(float)
    df["is_holiday"] = df["is_holiday"].astype(float)
    df["demand_multiplier"] = pd.to_numeric(df["demand_multiplier"], errors="coerce").fillna(1.0)
    for column in series.extra_exog_columns:
        df[column] = df[column].astype(float)
    season_dummies = pd.get_dummies(df["season"], prefix="season").reindex(columns=_SEASON_COLUMNS, fill_value=0).astype(float)
    df = pd.concat([df, season_dummies], axis=1)
    df["dayofweek"] = df["date"].dt.dayofweek
    df["month"] = df["date"].dt.month
    df = df.sort_values("date").reset_index(drop=True)
    for column in series.lagged_raw_columns:
        df[column] = df[column].astype(float)
    return df


def to_slots(history: pd.DataFrame, series: SeriesConfig) -> pd.DataFrame:
    """Collapses the hourly history into one row per half-day block - the grain
    the models actually forecast at. The target becomes that block's TOTAL
    staffing requirement in staff-hours; calendar attributes carry through
    unchanged (they're already per-day in dim_date); promotion/event flags are
    a max (the block had one or it didn't); real census/sales columns are summed
    then lagged one week. Lags are computed on the FULL history before any
    windowing, so every row in a later windowed slice already has a populated
    lag."""
    history = history.copy()
    history["slot_index"] = history["hour_of_day"] // BLOCK_HOURS

    aggregations = {"target": ("target", "sum"), "var_pair": ("var_pair", "sum")}
    for column in ("is_holiday", "demand_multiplier", *_SEASON_COLUMNS):
        aggregations[column] = (column, "first")
    for column in series.extra_exog_columns:
        aggregations[column] = (column, "max")
    for column in series.lagged_raw_columns:
        aggregations[column] = (column, "sum")

    slots = history.groupby(["business_date", "slot_index"], as_index=False).agg(**aggregations)
    slots = slots.sort_values(["business_date", "slot_index"]).reset_index(drop=True)
    slots["hour_of_day"] = slots["slot_index"] * BLOCK_HOURS
    slots["date"] = slots["business_date"] + pd.to_timedelta(slots["hour_of_day"], unit="h")
    slots["dayofweek"] = slots["date"].dt.dayofweek
    slots["month"] = slots["date"].dt.month
    for column in series.lagged_raw_columns:
        slots[f"{column}{LAG_SUFFIX}"] = slots[column].shift(LAG_SLOTS)
    return slots


def load_series_forecast_frame(db: Session, series: SeriesConfig) -> pd.DataFrame:
    return to_slots(load_series_history(db, series), series)


def windowed_training_frame(history: pd.DataFrame, max_slots: int | None = None) -> pd.DataFrame:
    """The most recent `max_slots` rows (defaulting to MAX_TRAINING_SLOTS), so
    CV + a final fit both stay fast regardless of how much total history a
    series has. Callers can pass a smaller window to trade history for
    speed."""
    window = max_slots if max_slots and max_slots > 0 else MAX_TRAINING_SLOTS
    if len(history) <= window:
        return history.reset_index(drop=True)
    return history.iloc[-window:].reset_index(drop=True)


def future_calendar_frame(db: Session, series: SeriesConfig, last_timestamp: pd.Timestamp, horizon_slots: int) -> pd.DataFrame:
    """One row per future half-day block over the forecast horizon, carrying
    the same calendar attributes the training frame has."""
    future_timestamps = pd.date_range(start=last_timestamp + timedelta(hours=BLOCK_HOURS), periods=horizon_slots, freq=f"{BLOCK_HOURS}h")
    business_dates = sorted({ts.date().isoformat() for ts in future_timestamps})
    date_list_sql = ", ".join(f"'{d}'" for d in business_dates)
    sql = text(f"SELECT business_date, season, is_holiday, demand_multiplier FROM dim_date WHERE business_date IN ({date_list_sql})")
    calendar = pd.read_sql_query(sql, db.get_bind())
    calendar["business_date"] = pd.to_datetime(calendar["business_date"]).dt.date.astype(str)

    frame = pd.DataFrame({"date": future_timestamps})
    frame["business_date"] = frame["date"].dt.date.astype(str)
    frame["hour_of_day"] = frame["date"].dt.hour
    frame = frame.merge(calendar, on="business_date", how="left")
    frame["is_holiday"] = frame["is_holiday"].fillna(0).astype(float)
    frame["demand_multiplier"] = pd.to_numeric(frame["demand_multiplier"], errors="coerce").fillna(1.0)
    season_dummies = pd.get_dummies(frame["season"], prefix="season").reindex(columns=_SEASON_COLUMNS, fill_value=0).astype(float)
    frame = pd.concat([frame, season_dummies], axis=1)
    for column in series.extra_exog_columns:
        # Not knowable in advance for a genuine future forecast (no confirmed
        # promotion/event calendar in this POC) - defaulted to "none planned".
        frame[column] = 0.0
    frame["dayofweek"] = frame["date"].dt.dayofweek
    frame["month"] = frame["date"].dt.month
    frame["target"] = pd.NA
    frame["var_pair"] = pd.NA
    return frame


def attach_future_lags(future_df: pd.DataFrame, history: pd.DataFrame, series: SeriesConfig) -> pd.DataFrame:
    """The one-week lag of each real census/sales column, for the future frame.
    Because the forecast horizon is also one week, "one week before future step
    i" always lands exactly on the most recent LAG_SLOTS rows of real history -
    taken positionally, not estimated."""
    if not series.lagged_raw_columns:
        return future_df
    future_df = future_df.copy()
    tail = history.tail(len(future_df))
    for column in series.lagged_raw_columns:
        lag_name = f"{column}{LAG_SUFFIX}"
        if len(tail) == len(future_df):
            future_df[lag_name] = tail[column].astype(float).to_numpy()
        else:
            fallback = float(history[column].mean()) if len(history) else 0.0
            future_df[lag_name] = fallback
    return future_df


def exog_columns_for(series: SeriesConfig) -> tuple[str, ...]:
    """The full contract-defined set of exogenous columns for this series -
    every column that's structurally available, regardless of whether the
    user has since clicked any of them off. Use enabled_exog_columns_for for
    what a model actually trains on."""
    lag_columns = tuple(f"{column}{LAG_SUFFIX}" for column in series.lagged_raw_columns)
    return tuple(CALENDAR_EXOG_COLUMNS) + tuple(series.extra_exog_columns) + lag_columns


def get_disabled_columns(db: Session, series_key: str) -> set[str]:
    rows = db.execute(
        select(SeriesFeatureSelection.column_name).where(SeriesFeatureSelection.series_key == series_key, SeriesFeatureSelection.enabled.is_(False))
    ).scalars().all()
    return set(rows)


def enabled_exog_columns_for(db: Session, series: SeriesConfig) -> tuple[str, ...]:
    """The exog columns actually fed to the models right now - the full
    contract set minus anything the user has explicitly clicked off in
    Demand Drivers & Feature Mapping. No selection rows at all means
    everything is enabled (the original, pre-toggle behavior)."""
    disabled = get_disabled_columns(db, series.key)
    if not disabled:
        return exog_columns_for(series)
    return tuple(column for column in exog_columns_for(series) if column not in disabled)


def set_column_enabled(db: Session, series_key: str, column_name: str, enabled: bool) -> None:
    row = db.execute(
        select(SeriesFeatureSelection).where(SeriesFeatureSelection.series_key == series_key, SeriesFeatureSelection.column_name == column_name)
    ).scalars().first()
    if row:
        row.enabled = enabled
    else:
        db.add(SeriesFeatureSelection(series_key=series_key, column_name=column_name, enabled=enabled))
    db.commit()
