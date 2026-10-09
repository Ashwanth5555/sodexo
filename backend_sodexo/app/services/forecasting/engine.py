"""Orchestrates rolling-origin CV, a final full-history fit, future
forecasting, and DB persistence for one series across every model.
Champion selection (lowest valid MAPE among completed models) runs once
every model in the series has a terminal status - ported from Meriton's
_rolling_origin_evaluation + champion-by-MAPE rule.
"""
import time
import warnings

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models import ModelPrediction, ModelRun
from app.services.forecasting.contract import FAST_HOLDOUT_MODEL_IDS, FORECAST_HORIZON_SLOTS, MODEL_SPECS, SLOW_MODEL_SKIP_REASON, SeriesConfig
from app.services.forecasting.eligibility import check_eligibility
from app.services.forecasting.metrics import average_metrics, calculate_metrics, is_valid_metric
from app.services.forecasting.models import FITTERS, ModelContext
from app.services.forecasting.validation_plan import fold_validation_size, holdout_size, resolve_seasonal_period, rolling_folds


def _backtest_records(validation_df: pd.DataFrame, predictions, fold: int) -> list[dict]:
    records = []
    for (_, row), predicted in zip(validation_df.iterrows(), predictions):
        records.append({
            "business_date": row["date"].date(), "hour_of_day": int(row["hour_of_day"]),
            "actual": None if pd.isna(row["target"]) else float(row["target"]),
            "predicted": None if pd.isna(predicted) else float(predicted),
            "fold": fold,
        })
    return records


def _rolling_origin_evaluation(fitter, df: pd.DataFrame, ctx: ModelContext, force_holdout: bool = False) -> dict:
    cycle = ctx.periods_per_week
    validation_size = fold_validation_size(len(df), cycle)
    folds = [] if force_holdout else rolling_folds(len(df), validation_size)
    if len(folds) < 3:
        holdout = holdout_size(len(df), cycle)
        train_df = df.iloc[:-holdout].copy()
        validation_df = df.iloc[-holdout:].copy()
        predictions = fitter(train_df, validation_df, ctx)
        return {
            "mode": "holdout_fast" if force_holdout else "holdout_fallback", "folds": 1,
            "metrics": calculate_metrics(validation_df["target"], predictions),
            "backtest": _backtest_records(validation_df, predictions, fold=1),
            "train_rows": len(train_df), "test_rows": len(validation_df),
        }

    fold_metrics = []
    backtest_records = []
    train_rows_per_fold = []
    for fold_number, (train_end, validation_end) in enumerate(folds, start=1):
        fold_train = df.iloc[:train_end].copy()
        fold_validation = df.iloc[train_end:validation_end].copy()
        predictions = fitter(fold_train, fold_validation, ctx)
        fold_metrics.append(calculate_metrics(fold_validation["target"], predictions))
        backtest_records.extend(_backtest_records(fold_validation, predictions, fold=fold_number))
        train_rows_per_fold.append(len(fold_train))
    return {
        "mode": "rolling_origin", "folds": len(fold_metrics),
        "metrics": average_metrics(fold_metrics),
        "backtest": backtest_records,
        "train_rows": max(train_rows_per_fold), "test_rows": len(backtest_records),
    }


def _friendly_error(exc: Exception) -> str:
    return str(exc)[:490] or exc.__class__.__name__


def _empirical_interval_offsets(backtest_records: list[dict]) -> tuple[float, float] | None:
    """An 80% empirical prediction interval from backtest residuals - honest
    uncertainty bounds derived from how wrong this model actually was, not a
    theoretical/assumed distribution."""
    residuals = [r["actual"] - r["predicted"] for r in backtest_records if r["actual"] is not None and r["predicted"] is not None]
    if len(residuals) < 5:
        return None
    return float(np.percentile(residuals, 10)), float(np.percentile(residuals, 90))


def run_portfolio(
    db: Session,
    series_key: str,
    train_df_full: pd.DataFrame,
    future_df: pd.DataFrame,
    ctx: ModelContext,
    job_id: str,
    on_step=None,
    on_start=None,
    skip_models: frozenset[str] | None = None,
) -> list[ModelRun]:
    """Run every model in the portfolio over one prepared series.

    Split out of train_series so an uploaded dataset - whose frames come from a
    CSV rather than the canonical tables - is evaluated, scored, persisted and
    ranked by exactly the same code path.

    skip_models names models this run should not fit (the fast training
    profile). They are still recorded as not_applicable with a reason, so the
    leaderboard accounts for all 13 and nobody has to guess why a model they
    remember is absent.
    """
    seasonal_period = ctx.seasonal_period
    skip_models = skip_models or frozenset()

    runs: list[ModelRun] = []
    for model_id, label, dependency_module in MODEL_SPECS:
        started = time.perf_counter()
        if on_start:
            on_start(label)
        if model_id in skip_models:
            run = ModelRun(
                job_id=job_id, series_key=series_key, model_id=model_id, model_label=label,
                status="not_applicable", failure_reason=SLOW_MODEL_SKIP_REASON,
            )
            db.add(run)
            db.commit()
            runs.append(run)
            if on_step:
                on_step(label)
            continue

        eligible, reason = check_eligibility(
            model_id, dependency_module, train_df_full, seasonal_period,
            ctx.exog_columns, cycle=ctx.periods_per_week, var_pair_reason=ctx.var_pair_reason,
        )
        if not eligible:
            run = ModelRun(job_id=job_id, series_key=series_key, model_id=model_id, model_label=label, status="not_applicable", failure_reason=reason)
            db.add(run)
            db.commit()
            runs.append(run)
            if on_step:
                on_step(label)
            continue

        fitter = FITTERS[model_id]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                evaluation = _rolling_origin_evaluation(fitter, train_df_full, ctx, force_holdout=model_id in FAST_HOLDOUT_MODEL_IDS)
                if not is_valid_metric({**evaluation["metrics"], "status": "completed"}):
                    raise ValueError("Backtest produced no valid, in-range metrics.")
                future_predictions = fitter(train_df_full, future_df, ctx)
        except Exception as exc:
            run = ModelRun(
                job_id=job_id, series_key=series_key, model_id=model_id, model_label=label, status="failed",
                failure_reason=_friendly_error(exc), runtime_seconds=round(time.perf_counter() - started, 2),
            )
            db.add(run)
            db.commit()
            runs.append(run)
            if on_step:
                on_step(label)
            continue

        metrics = evaluation["metrics"]
        run = ModelRun(
            job_id=job_id, series_key=series_key, model_id=model_id, model_label=label, status="completed",
            evaluation_mode=evaluation["mode"], folds=evaluation["folds"],
            train_rows=evaluation.get("train_rows"), test_rows=evaluation.get("test_rows"),
            mape=metrics["mape"], rmse=metrics["rmse"], mae=metrics["mae"], wape=metrics["wape"], bias=metrics["bias"],
            accuracy=metrics["accuracy"], runtime_seconds=round(time.perf_counter() - started, 2),
        )
        db.add(run)
        db.flush()
        for record in evaluation["backtest"]:
            db.add(ModelPrediction(
                model_run_id=run.id, series_key=series_key, model_id=model_id, business_date=record["business_date"],
                hour_of_day=record["hour_of_day"], actual=record["actual"], predicted=record["predicted"],
                is_future=False, fold=record["fold"],
            ))
        offsets = _empirical_interval_offsets(evaluation["backtest"])
        for timestamp, predicted in zip(future_df["date"], future_predictions):
            if pd.isna(predicted):
                continue
            predicted = float(predicted)
            lower = predicted + offsets[0] if offsets else None
            upper = predicted + offsets[1] if offsets else None
            db.add(ModelPrediction(
                model_run_id=run.id, series_key=series_key, model_id=model_id, business_date=timestamp.date(),
                hour_of_day=int(timestamp.hour), actual=None, predicted=predicted, lower=lower, upper=upper,
                is_future=True, fold=None,
            ))
        db.commit()
        runs.append(run)
        if on_step:
            on_step(label)

    _select_champion(db, runs)
    return runs


def _select_champion(db: Session, runs: list[ModelRun]) -> None:
    completed = [run for run in runs if run.status == "completed" and run.mape is not None]
    completed.sort(key=lambda run: run.mape)
    for rank, run in enumerate(completed, start=1):
        run.rank = rank
        run.is_champion = rank == 1
    db.commit()
