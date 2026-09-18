"""Shared chronological-split classifier training + evaluation for
Predictive Analytics. Every risk model in this package is a real,
trained scikit-learn classifier over real historical labels already in the
canonical DB - never a fabricated probability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score, roc_auc_score

RANDOM_SEED = 42
MIN_ROWS = 60
MIN_POSITIVE_RATE = 0.02


class ModelNotApplicable(ValueError):
    pass


def add_recency_features(df: pd.DataFrame, employee_col: str, date_col: str, label_col: str, windows: tuple[int, ...] = (7, 14, 30)) -> pd.DataFrame:
    """Rolling count of the label in the trailing N days per employee,
    shifted by one row so the feature never sees the day it's predicting -
    a real recency signal ("how often has this happened to this person
    lately"), not a leak of the outcome itself."""
    df = df.sort_values([employee_col, date_col]).copy()
    for window in windows:
        df[f"recent_{window}d"] = (
            df.groupby(employee_col)[label_col]
            .apply(lambda s: s.shift(1).rolling(window, min_periods=1).sum())
            .reset_index(level=0, drop=True)
        )
    df["dayofweek"] = pd.to_datetime(df[date_col]).dt.dayofweek
    df["is_weekend"] = (df["dayofweek"] >= 5).astype(int)
    return df


def chronological_split(df: pd.DataFrame, date_col: str, test_frac: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values(date_col)
    cutoff = int(len(df) * (1 - test_frac))
    return df.iloc[:cutoff], df.iloc[cutoff:]


def train_and_evaluate(df: pd.DataFrame, feature_columns: list[str], label_col: str, date_col: str) -> dict:
    df = df.dropna(subset=feature_columns + [label_col])
    if len(df) < MIN_ROWS:
        raise ModelNotApplicable(f"Only {len(df)} labeled rows available; at least {MIN_ROWS} are needed for an honest train/test split.")
    positive_rate = float(df[label_col].mean())
    if positive_rate < MIN_POSITIVE_RATE or positive_rate > (1 - MIN_POSITIVE_RATE):
        raise ModelNotApplicable(f"Positive class rate is {positive_rate:.1%}, too rare/common in this window for a meaningful classifier.")

    train_df, test_df = chronological_split(df, date_col)
    if train_df[label_col].nunique() < 2 or test_df[label_col].nunique() < 2:
        raise ModelNotApplicable("The chronological train or test split doesn't contain both outcome classes.")

    model = GradientBoostingClassifier(random_state=RANDOM_SEED)
    model.fit(train_df[feature_columns], train_df[label_col])

    test_probabilities = model.predict_proba(test_df[feature_columns])[:, 1]
    test_predictions = (test_probabilities >= 0.5).astype(int)

    metrics = {
        "precision": round(float(precision_score(test_df[label_col], test_predictions, zero_division=0)), 3),
        "recall": round(float(recall_score(test_df[label_col], test_predictions, zero_division=0)), 3),
        "f1": round(float(f1_score(test_df[label_col], test_predictions, zero_division=0)), 3),
        "roc_auc": round(float(roc_auc_score(test_df[label_col], test_probabilities)), 3),
        "pr_auc": round(float(average_precision_score(test_df[label_col], test_probabilities)), 3),
    }
    importances = sorted(zip(feature_columns, model.feature_importances_), key=lambda kv: kv[1], reverse=True)
    top_drivers = [name for name, weight in importances if weight > 0][:5]

    return {
        "model": model, "metrics": metrics, "top_drivers": top_drivers,
        "train_rows": len(train_df), "test_rows": len(test_df), "positive_rate": round(positive_rate, 3),
    }


def risk_level(probability: float) -> str:
    if probability >= 0.66:
        return "High"
    if probability >= 0.33:
        return "Medium"
    return "Low"
