"""Turns an uploaded dataset into forecastable series.

The forecasting engine is DataFrame-driven: give it a frame with `date`,
`hour_of_day`, `dayofweek`, `month`, `target`, `var_pair` and any exogenous
columns, plus a matching future frame, and the full 13-model portfolio runs
unchanged. This module builds exactly that shape from a user's file and the
column mapping they chose, so an arbitrary CSV is forecast by the same engine,
scored by the same metrics and ranked by the same champion rule as the
built-in series.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import re

import pandas as pd

from app.models import UploadedDataset
from app.services.datasets import storage
from app.services.forecasting.contract import STATE_SPACE_SEASONAL_BUDGET
from app.services.forecasting.models import ModelContext

# Per frequency: the seasonal-cycle candidates to test, the dominant cycle used
# to size CV folds, how many periods make a day, and XGBoost's lag/rolling
# windows - all expressed in periods of that frequency.
FREQUENCY_GRAIN: dict[str, dict] = {
    "H": {
        "candidates": (24, 168), "cycle": 168, "per_day": 24,
        "xgb_lags": (1, 24, 168), "xgb_rollings": (24, 168),
        "default_horizon": 48, "label": "Hourly",
    },
    "D": {
        "candidates": (7,), "cycle": 7, "per_day": 1,
        "xgb_lags": (1, 7, 14, 28), "xgb_rollings": (7, 28),
        "default_horizon": 14, "label": "Daily",
    },
    "W": {
        "candidates": (52,), "cycle": 13, "per_day": 1,
        "xgb_lags": (1, 4, 13, 52), "xgb_rollings": (4, 13),
        "default_horizon": 8, "label": "Weekly",
    },
    "MS": {
        "candidates": (12,), "cycle": 12, "per_day": 1,
        "xgb_lags": (1, 3, 6, 12), "xgb_rollings": (3, 12),
        "default_horizon": 6, "label": "Monthly",
    },
}

MIN_ROWS_PER_SERIES = 20


@dataclass(frozen=True)
class UploadedSeries:
    """One forecastable series from an uploaded dataset - the whole file, or
    one value of the group column when the user chose to split by one."""

    key: str
    label: str
    dataset_id: str
    # The combination this series represents, one value per group column,
    # in the same order. None for an ungrouped file.
    group_values: tuple[str, ...] | None = None
    exog_columns: tuple[str, ...] = field(default=())


def grain_for(frequency: str) -> dict:
    return FREQUENCY_GRAIN.get(frequency, FREQUENCY_GRAIN["D"])


def default_horizon(frequency: str) -> int:
    return grain_for(frequency)["default_horizon"]


def series_key_for(dataset_id: str, group_values: tuple[str, ...] | None) -> str:
    """Stable, <=60 char key - model_run.series_key is a String(60).

    With several group columns the fragments are shortened so the combined
    key still fits: two columns get 12 characters each rather than 18, which
    keeps "North Region / Food & Nutrition" distinguishable from "North
    Region / Retail" without overflowing the column.
    """
    if not group_values:
        return f"{dataset_id}__ALL"
    per_value = max(6, 24 // len(group_values))
    suffix = "_".join(storage.safe_key_fragment(v, limit=per_value) for v in group_values)
    return f"{dataset_id}__{suffix}"


def list_series(dataset: UploadedDataset) -> list[UploadedSeries]:
    """The series this dataset yields, without loading the whole file if we can
    avoid it. Returns [] until the user has mapped date + target columns."""
    if not dataset.date_column or not dataset.target_column:
        return []

    exog = tuple(dataset.exog_columns or ())
    group_columns = [c for c in (dataset.group_columns or [])]
    if not group_columns:
        return [UploadedSeries(series_key_for(dataset.dataset_id, None), dataset.name, dataset.dataset_id, None, exog)]

    frame = storage.load_frame(dataset.stored_filename)
    missing = [c for c in group_columns if c not in frame.columns]
    if missing:
        return []

    # The combinations that OCCUR, not the cartesian product. A file with
    # three regions and four departments rarely has all twelve pairings, and
    # inventing the absent ones would produce series with no rows that fail
    # at training with a confusing "too little history".
    combos = (
        frame[group_columns].dropna().astype(str)
        .drop_duplicates()
        .sort_values(group_columns)
        .itertuples(index=False, name=None)
    )
    return [
        UploadedSeries(
            key=series_key_for(dataset.dataset_id, values),
            label=f"{dataset.name} / {' / '.join(values)}",
            dataset_id=dataset.dataset_id,
            group_values=tuple(values),
            exog_columns=exog,
        )
        for values in list(combos)[: storage.MAX_GROUP_SERIES]
    ]


def _calendar_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Calendar columns every fitter expects, derived from the timestamp alone.

    Unlike the canonical series there is no dim_date to join - but these are all
    computable for future dates too, which is what makes them usable as
    exogenous inputs without leaking anything.
    """
    frame = frame.copy()
    frame["hour_of_day"] = frame["date"].dt.hour.astype(int)
    frame["dayofweek"] = frame["date"].dt.dayofweek.astype(int)
    frame["month"] = frame["date"].dt.month.astype(int)
    frame["is_weekend"] = (frame["date"].dt.dayofweek >= 5).astype(float)
    return frame


def unforecastable_series(dataset: UploadedDataset) -> list[str]:
    """Group combinations this mapping leaves with nothing to forecast.

    A file covering several departments carries each one's own measures, so a
    column can be a real measure and still be blank for most of the file.
    Chosen as the target it produces series with no rows at all, and without
    this the run gets that far before failing, on a message about the whole
    file rather than the series that is actually empty.

    One pass over the file, not one per series: a wrong mapping is usually
    wrong for many series at once, and re-reading a 50,000-row file forty
    times is the slowest possible way to find that out.
    """
    frame = storage.load_frame(dataset.stored_filename)
    if dataset.date_column not in frame.columns or dataset.target_column not in frame.columns:
        return []
    usable = (
        pd.to_datetime(frame[dataset.date_column], errors="coerce").notna()
        & pd.to_numeric(frame[dataset.target_column], errors="coerce").notna()
    )
    groups = [c for c in (dataset.group_columns or []) if c in frame.columns]
    if not groups:
        return [] if bool(usable.any()) else ["the whole file"]
    per_group = usable.groupby([frame[c].astype(str) for c in groups]).any()
    return [
        " / ".join(key) if isinstance(key, tuple) else str(key)
        for key, ok in per_group.items()
        if not ok
    ]


def build_history(
    dataset: UploadedDataset,
    series: UploadedSeries,
    extra_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Load, filter, aggregate and regularise one series' history.

    extra_columns are carried through the same resampling as the target but
    are NOT fed to the models - Staffing Impact uses it for the capacity
    column, which must line up period-for-period with the target without
    becoming a predictor of it.

    Raises ValueError with a user-facing message when the mapping cannot
    produce a usable series - that surfaces as the model run's failure reason
    rather than an opaque traceback.
    """
    frame = storage.load_frame(dataset.stored_filename)

    for column, role in ((dataset.date_column, "date"), (dataset.target_column, "target")):
        if column not in frame.columns:
            raise ValueError(f"The {role} column '{column}' is no longer present in the uploaded file.")

    if series.group_values:
        for column, value in zip(dataset.group_columns or [], series.group_values):
            if column not in frame.columns:
                raise ValueError(f"The group column '{column}' is no longer present in the uploaded file.")
            frame = frame[frame[column].astype(str) == value]

    wanted = {dataset.date_column, dataset.target_column, *series.exog_columns, *extra_columns}
    frame = frame[[c for c in wanted if c in frame.columns]].copy()
    frame["date"] = pd.to_datetime(frame[dataset.date_column], errors="coerce")
    frame["target"] = pd.to_numeric(frame[dataset.target_column], errors="coerce")
    frame = frame.dropna(subset=["date", "target"])
    if frame.empty:
        raise ValueError(
            f"No rows have both a readable date in '{dataset.date_column}' and a number in '{dataset.target_column}'."
        )

    carried = []
    for column in extra_columns:
        if column in frame.columns and column not in (dataset.date_column, dataset.target_column):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            carried.append(column)

    numeric_exog = []
    # What each text driver became, so the model context can find the columns
    # it was expanded into - the original name is gone by the time it looks.
    encoded: dict[str, list[str]] = {}
    for column in series.exog_columns:
        if column not in frame.columns or column in (dataset.date_column, dataset.target_column):
            continue
        if is_encodable_category(frame[column]):
            # Words cannot be coerced to numbers - pd.to_numeric turns
            # "Summer" into NaN - so a text driver is expanded into one
            # indicator column per level instead of being silently emptied.
            created = encode_category(frame, column)
            if created:
                encoded[column] = created
                numeric_exog.extend(created)
            continue
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        numeric_exog.append(column)

    # Collapse duplicate timestamps, then put the series on a regular grid so
    # the seasonal models see evenly spaced periods. Gaps are interpolated
    # rather than zero-filled: a missing reading is unknown, not zero demand.
    aggregation = dataset.aggregation if dataset.aggregation in ("sum", "mean") else "sum"
    value_columns = ["target", *numeric_exog, *[c for c in carried if c not in numeric_exog]]
    resampled = frame.set_index("date")[value_columns].resample(dataset.frequency or "D")
    # min_count=1, because pandas sums an all-empty period to 0 rather than to
    # nothing - and 0 is a claim, not an absence. That zero defeated the whole
    # design below: the target's "interpolate, never zero-fill" rule never
    # fired, and a roster column absent for a series reported as "0 planned",
    # which read as a total shortfall on every period. mean() already returns
    # nothing for an empty period, so only the sum needed saying.
    grouped = resampled.sum(min_count=1) if aggregation == "sum" else resampled.mean()
    grouped["target"] = grouped["target"].interpolate(limit_direction="both")
    for column in numeric_exog:
        grouped[column] = grouped[column].interpolate(limit_direction="both").fillna(0.0)
    # Carried columns are deliberately NOT interpolated. A period with no
    # roster rows means nothing was planned, and filling between two staffed
    # days would manufacture coverage that was never scheduled.

    history = grouped.reset_index().rename(columns={"index": "date"})
    history = history.dropna(subset=["target"])
    if len(history) < MIN_ROWS_PER_SERIES:
        raise ValueError(
            f"Only {len(history)} period{'' if len(history) == 1 else 's'} of history at "
            f"{grain_for(dataset.frequency or 'D')['label'].lower()} frequency; at least "
            f"{MIN_ROWS_PER_SERIES} are needed to train and validate a model."
        )

    history = _calendar_features(history)
    history.attrs["encoded_columns"] = encoded
    history["target"] = history["target"].astype(float)
    history["var_pair"] = _choose_var_pair(history, numeric_exog)
    return history


def _choose_var_pair(history: pd.DataFrame, numeric_exog: list[str]) -> pd.Series:
    """The second series VAR models jointly with the target.

    Must actually vary, which is why this is not simply the first driver.
    Resampling can flatten a column that varied in the raw file: an hourly
    export carrying `hour_of_day` summed to a daily grain gives 0+1+...+23 =
    276 for every complete day, so the "first driver" was constant and VAR
    was reported not_applicable on a dataset that had nine usable drivers.

    Falls back to any non-constant numeric column, then to a constant, which
    the eligibility check turns into a clear not_applicable rather than a
    meaningless fit.
    """
    # "Co-evolving" is the requirement, so rank candidates by how strongly
    # they actually move with the target rather than taking whichever the
    # user happened to list first. On a census file that is the difference
    # between pairing with occupied_beds and pairing with a day-of-week
    # index, which carries almost no information about level.
    target = history["target"].astype(float)
    best_column, best_score = None, -1.0
    for column in numeric_exog:
        series = history[column].astype(float)
        if series.nunique(dropna=True) <= 1:
            continue
        correlation = series.corr(target)
        score = 0.0 if pd.isna(correlation) else abs(float(correlation))
        if score > best_score:
            best_column, best_score = column, score
    if best_column is not None:
        return history[best_column].astype(float)

    # No chosen driver survived resampling. Anything else in the frame that
    # moves is still a more honest pairing than a constant - the calendar
    # features are derived from the timestamp and are known for future
    # periods too, so they do not leak.
    for column in history.columns:
        if column in ("target", "var_pair", "date") or column in numeric_exog:
            continue
        candidate = history[column]
        if pd.api.types.is_numeric_dtype(candidate) and candidate.nunique(dropna=True) > 1:
            return candidate.astype(float)

    return pd.Series(0.0, index=history.index)


# A text column worth encoding names a handful of states - a season, a shift
# pattern, a weather band. Above this it is an identifier or free text, and
# one indicator column per value would add more parameters than rows.
MAX_CATEGORY_LEVELS = 12


# A roster column records what was PLANNED. Calendar parts and flags are
# numeric and low-cardinality, which makes them look like candidates and read
# like nonsense: mapping `dayofweek` as the roster made Staffing Impact compare
# 55 person-hours against the number 5 meaning Saturday, and every period in
# the file reported as understaffed by nearly its whole requirement.
_NOT_A_ROSTER = re.compile(
    r"^(day.?of.?week|dow|month|year|week(.?of.?year)?|quarter|hour(.?of.?day)?"
    r"|minute|day|date|season|is_.*|.*_flag|.*_index)$",
    re.I,
)


def series_missing_column(dataset: UploadedDataset, column: str) -> list[str]:
    """Series for which this column holds no value at all.

    One pass over the file. A column can be a real measure and still be absent
    from part of a combined workbook - beds exist for the catering sites and
    not the cafes - and a roster that is missing for a series cannot be
    compared against anything there.
    """
    frame = storage.load_frame(dataset.stored_filename)
    if column not in frame.columns:
        return []
    groups = [c for c in (dataset.group_columns or []) if c in frame.columns]
    present = pd.to_numeric(frame[column], errors="coerce").notna()
    if not groups:
        return [] if bool(present.any()) else ["the whole file"]
    by_group = present.groupby([frame[c].astype(str) for c in groups]).any()
    return [
        " / ".join(key) if isinstance(key, tuple) else str(key)
        for key, has_any in by_group.items()
        if not has_any
    ]


def is_plausible_capacity(column: str | None) -> bool:
    """Could this column be a roster, or is it plainly a calendar part?

    Deliberately a rejection list rather than an approval one: an export may
    call its roster anything, and refusing everything we do not recognise
    would lock out files we have never seen. What it does refuse is the small
    set of names that cannot be a plan under any reading.
    """
    if not column:
        return False
    return not _NOT_A_ROSTER.match(column.strip())


def is_encodable_category(values: pd.Series, row_count: int | None = None) -> bool:
    """Is this text column a handful of repeating states rather than free text?"""
    if pd.api.types.is_numeric_dtype(values) or pd.api.types.is_datetime64_any_dtype(values):
        return False
    levels = values.dropna().nunique()
    if not 2 <= levels <= MAX_CATEGORY_LEVELS:
        return False
    # A column with almost as many values as rows is an identifier, however
    # few rows there happen to be.
    rows = row_count if row_count is not None else len(values)
    return levels <= max(2, rows // 10)


def _category_column_name(column: str, level: str) -> str:
    """A safe name for one indicator column.

    Kept to letters, digits and underscores: these names travel into
    DataFrame attribute access and itertuples, which mangle anything else.
    """
    slug = re.sub(r"[^0-9A-Za-z]+", "_", str(level)).strip("_") or "other"
    return f"{column}_{slug}"[:60]


def encode_category(frame: pd.DataFrame, column: str) -> list[str]:
    """Expand a text column into indicator columns, in place.

    One column per level except the first, which becomes the reference the
    others are measured against. Encoding every level as well would make the
    indicators sum to one in every row - the same information the models
    already have as a constant term - and leave the exogenous matrix singular,
    which SARIMAX and VAR reject outright rather than tolerate.
    """
    levels = sorted(frame[column].dropna().astype(str).unique())
    created = []
    for level in levels[1:]:
        name = _category_column_name(column, level)
        if name in frame.columns:
            continue
        frame[name] = (frame[column].astype(str) == level).astype(float)
        created.append(name)
    return created


def build_future(dataset: UploadedDataset, history: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Future calendar rows to forecast over, beyond the last known actual."""
    frequency = dataset.frequency or "D"
    last = history["date"].iloc[-1]
    future_dates = pd.date_range(start=last, periods=horizon + 1, freq=frequency)[1:]
    future = pd.DataFrame({"date": future_dates})
    future = _calendar_features(future)
    future["target"] = float("nan")
    # Exogenous drivers are unknown ahead - hold them at the last observed
    # value. Honest and leak-free: it uses only what was already known.
    for column in history.columns:
        if column not in future.columns and column not in ("target",):
            future[column] = history[column].iloc[-1]
    return future


def context_for(dataset: UploadedDataset, history: pd.DataFrame, seasonal_period: int | None) -> ModelContext:
    grain = grain_for(dataset.frequency or "D")
    # A text driver is no longer present under its own name by the time it
    # gets here - it was expanded into indicator columns on the way in.
    expansions = history.attrs.get("encoded_columns", {})
    chosen: list[str] = []
    for column in (dataset.exog_columns or ()):
        if column in history.columns:
            chosen.append(column)
        else:
            chosen.extend(c for c in expansions.get(column, []) if c in history.columns)
    # Drop drivers that are flat at this grain. A constant column carries no
    # information for any model, and some fitters reject a rank-deficient
    # exogenous matrix outright - so keeping it costs accuracy nowhere and
    # eligibility somewhere. This is not hypothetical: an hourly export's
    # `hour_of_day` sums to 276 for every complete day, and carrying it made
    # the whole exogenous matrix singular at daily grain.
    # Also drops columns that belong to a different sheet of a combined
    # workbook: a catering series carries `transaction_count` as all-blank,
    # which interpolates to a constant and would otherwise ride along as a
    # useless input.
    exog = tuple(
        c for c in chosen
        if history[c].notna().any() and history[c].nunique(dropna=True) > 1
    )
    # is_weekend is derived, always known ahead, and cheap signal - include it.
    exog = exog + ("is_weekend",) if "is_weekend" in history.columns else exog
    # VAR models two series jointly. With no driver column chosen there is no
    # honest second series, so say that rather than reporting a bare
    # "one series is constant", which reads as arbitrary.
    var_pair_reason = None
    if "var_pair" in history.columns and history["var_pair"].nunique(dropna=True) <= 1:
        chosen = [c for c in (dataset.exog_columns or ()) if c in history.columns]
        if not chosen:
            var_pair_reason = (
                "VAR forecasts two co-evolving series together, and no driver column was chosen for "
                "this dataset - so there is no second series to pair with the target. Pick a numeric "
                "driver in Demand Drivers & Feature Mapping to enable VAR."
            )
        else:
            var_pair_reason = (
                "VAR needs a second series that varies, and every column in this file is flat once "
                f"aggregated to {grain['label'].lower()} periods (checked: {', '.join(chosen)}). "
                "A column like hour-of-day becomes constant when summed to a daily grain."
            )

    return ModelContext(
        seasonal_period=seasonal_period,
        exog_columns=exog,
        periods_per_day=grain["per_day"],
        periods_per_week=grain["cycle"],
        xgb_lags=grain["xgb_lags"],
        xgb_rollings=grain["xgb_rollings"],
        max_seasonal_period=max(grain["candidates"]),
        var_pair_reason=var_pair_reason,
        state_space_seasonal_period=_state_space_period(seasonal_period, grain["candidates"], len(history)),
    )


def _state_space_period(resolved: int | None, candidates: tuple[int, ...], rows: int) -> int | None:
    """The seasonal period SARIMAX and Exponential Smoothing may use.

    Those two carry one state per seasonal slot, so a fit costs roughly
    rows x period^2. On hourly data the resolver can pick 168 - a weekly cycle -
    and against two years of history that is half a billion units of work: a
    single fit does not return, and a whole run sits at zero with nothing to
    show for it. Above the budget this falls back to the longest candidate that
    does fit, which on hourly data is the daily cycle - the strong signal in
    staffing data anyway - and XGBoost keeps the weekly shape through its
    168-slot lag features.
    """
    def affordable(period: int) -> bool:
        return rows * period * period <= STATE_SPACE_SEASONAL_BUDGET

    if resolved is None or affordable(resolved):
        return resolved
    shorter = sorted((c for c in candidates if c < resolved and affordable(c)), reverse=True)
    return shorter[0] if shorter else None
