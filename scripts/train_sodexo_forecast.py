"""Trains the 4 manpower-forecasting models (2 locations x 2 departments) directly
against the Sodexo Healthcare focused dataset, to prove out the design before this
gets wired into the full backend pipeline.

Target: required_manpower (a direct headcount, not an intermediate demand number).
Features: calendar-only (hour, day-of-week, month, quarter, weekend, holiday,
trend) - deliberately NOT occupied_beds/admissions_count, so the same model can
predict a future hour where the real patient count isn't known yet. Evaluated
against a seasonal-naive baseline with real MAE/RMSE/MAPE/WAPE/bias - nothing here
is a target number decided in advance.

Usage:
    ./.venv/Scripts/python scripts/train_sodexo_forecast.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "sodexo_healthcare_focused_dataset.xlsx"

SEASON_COLUMNS = ["season_Fall", "season_Spring", "season_Summer", "season_Winter"]
FEATURE_COLUMNS = ["hour_of_day", "day_of_week", "month", "quarter", "is_weekend", "is_holiday", "trend_index"] + SEASON_COLUMNS


def _add_calendar_derived(df: pd.DataFrame) -> pd.DataFrame:
    """season and is_holiday are already first-class columns in the dataset (not re-derived
    here) - this only adds the few fields that don't make sense to store per-row (day_of_week,
    trend_index) and one-hot encodes season for the model.
    """
    df = df.copy()
    df["day_of_week"] = df["business_date"].dt.dayofweek
    df["month"] = df["business_date"].dt.month
    df["quarter"] = df["business_date"].dt.quarter
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["is_holiday"] = df["is_holiday"].astype(int)
    min_date = df["business_date"].min()
    df["trend_index"] = (df["business_date"] - min_date).dt.days
    season_dummies = pd.get_dummies(df["season"], prefix="season")
    for col in SEASON_COLUMNS:
        df[col] = season_dummies.get(col, 0)
    return df


def _chronological_split(df: pd.DataFrame, train_frac=0.70, val_frac=0.15):
    df = df.sort_values(["business_date", "hour_of_day"]).reset_index(drop=True)
    n = len(df)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    return df.iloc[:train_end], df.iloc[train_end:val_end], df.iloc[val_end:]


class SeasonalNaiveBaseline:
    def fit(self, X, y):
        df = X[["day_of_week", "hour_of_day"]].copy()
        df["y"] = y.values
        self.lookup = df.groupby(["day_of_week", "hour_of_day"])["y"].mean().to_dict()
        self.global_mean = float(y.mean())
        return self

    def predict(self, X):
        keys = list(zip(X["day_of_week"], X["hour_of_day"]))
        return np.array([self.lookup.get(k, self.global_mean) for k in keys])


def mae(y_true, y_pred): return float(np.mean(np.abs(y_true - y_pred)))
def rmse(y_true, y_pred): return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
def mape(y_true, y_pred):
    mask = y_true != 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100) if mask.any() else float("nan")
def wape(y_true, y_pred):
    denom = np.sum(np.abs(y_true))
    return float(np.sum(np.abs(y_true - y_pred)) / denom * 100) if denom else float("nan")
def bias(y_true, y_pred): return float(np.mean(y_pred - y_true))


def train_one(df: pd.DataFrame, label: str) -> dict:
    train_df, val_df, test_df = _chronological_split(df)
    X_train, y_train = train_df[FEATURE_COLUMNS], train_df["required_manpower"].astype(float)
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df["required_manpower"].astype(float)

    model = RandomForestRegressor(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    pred_rounded = np.round(np.clip(pred, 0, None))

    baseline = SeasonalNaiveBaseline().fit(X_train, y_train)
    baseline_pred = baseline.predict(X_test)

    y_test_arr = y_test.to_numpy()
    metrics = {
        "label": label,
        "train_rows": len(train_df), "test_rows": len(test_df),
        "mae": mae(y_test_arr, pred_rounded), "rmse": rmse(y_test_arr, pred_rounded),
        "mape": mape(y_test_arr, pred_rounded), "wape": wape(y_test_arr, pred_rounded),
        "bias": bias(y_test_arr, pred_rounded), "baseline_mae": mae(y_test_arr, baseline_pred),
    }
    metrics["beats_baseline"] = metrics["mae"] < metrics["baseline_mae"]

    # Show a few real example predictions for a peak hour and an off-peak hour
    sample = test_df.iloc[:5][["business_date", "hour_of_day", "required_manpower"]].copy()
    sample["predicted_manpower"] = pred_rounded[:5]
    metrics["sample"] = sample

    return metrics


def main() -> None:
    xl = pd.ExcelFile(DATA_PATH)
    census = xl.parse("hourly_census")
    cafe = xl.parse("cafe_hourly_sales")

    results = []
    for loc in census["location_id"].unique():
        sub = _add_calendar_derived(census[census["location_id"] == loc])
        results.append(train_one(sub, f"{loc} - Food & Nutrition Services"))

    for loc in cafe["location_id"].unique():
        sub = _add_calendar_derived(cafe[cafe["location_id"] == loc])
        results.append(train_one(sub, f"{loc} - Retail & Cafe Services"))

    print(f"\n{'Model':<38} {'Train':>7} {'Test':>6} {'MAE':>6} {'RMSE':>6} {'MAPE%':>7} {'WAPE%':>7} {'Bias':>6} {'BaseMAE':>8}  Beats?")
    for r in results:
        print(f"{r['label']:<38} {r['train_rows']:>7} {r['test_rows']:>6} {r['mae']:>6.2f} {r['rmse']:>6.2f} "
              f"{r['mape']:>7.1f} {r['wape']:>7.1f} {r['bias']:>6.2f} {r['baseline_mae']:>8.2f}  {'YES' if r['beats_baseline'] else 'no'}")

    print("\nSample real predictions (first 5 test-period rows per model):")
    for r in results:
        print(f"\n{r['label']}:")
        print(r["sample"].to_string(index=False))


if __name__ == "__main__":
    main()
