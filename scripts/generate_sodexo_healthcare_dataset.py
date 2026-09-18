"""Generates a synthetic Sodexo Healthcare (hospital support-services) dataset.

Produces data/sodexo_healthcare_synthetic_dataset.xlsx WITHOUT touching the
existing retail workbook or any part of the running application (mock_api,
backend, frontend) - this script only creates the new data file. Wiring the
rest of the pipeline to it is a deliberately separate, later phase.

Deterministic (seeded) - re-running produces the same file byte-for-byte in
content (timestamps aside).

Usage:
    ./.venv/Scripts/python scripts/generate_sodexo_healthcare_dataset.py
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)

OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "sodexo_healthcare_focused_dataset.xlsx"

DEMAND_START = date(2024, 8, 1)
DEMAND_END = date(2026, 7, 31)
OPS_START = date(2026, 6, 2)
OPS_END = date(2026, 7, 31)

CENSUS_HOURS = list(range(0, 24))  # hospitals run 24/7
CAFE_HOURS = list(range(6, 20))  # staff/visitor cafeteria hours, 6am-8pm


# ---------------------------------------------------------------------------
# 1. Dimensions
# ---------------------------------------------------------------------------

# location_id, hospital_type, bed_count
_LOCATIONS_RAW = [
    ("LOC01", "Community Hospital", 120),
    ("LOC02", "Regional Medical Center", 350),
]


def _build_locations() -> pd.DataFrame:
    type_counters: dict[str, int] = {}
    rows = []
    for location_id, hospital_type, beds in _LOCATIONS_RAW:
        type_counters[hospital_type] = type_counters.get(hospital_type, 0) + 1
        suffix = chr(ord("A") + type_counters[hospital_type] - 1)
        rows.append((location_id, f"{hospital_type} {suffix}", hospital_type, beds))
    return pd.DataFrame(rows, columns=["location_id", "location_name", "hospital_type", "bed_count"])


LOCATIONS = _build_locations()

DEPARTMENTS = pd.DataFrame(
    [
        ("DEP01", "Food & Nutrition Services", "CC01"),
        ("DEP02", "Retail & Cafe Services", "CC02"),
    ],
    columns=["department_id", "department_name", "cost_center_id"],
)

JOBS = pd.DataFrame(
    [
        ("JOB01", "Tray Line Associate", "DEP01"),
        ("JOB02", "Nutrition Services Supervisor", "DEP01"),
        ("JOB03", "Cafe Associate", "DEP02"),
        ("JOB04", "Cafe Supervisor", "DEP02"),
    ],
    columns=["job_id", "job_title", "department_id"],
)

SKILLS = pd.DataFrame(
    [
        ("SK01", "Food Handling & Sanitation Certification"),
        ("SK02", "Patient Interaction / HIPAA Awareness"),
        ("SK03", "Cash Handling"),
        ("SK04", "Therapeutic Diet Handling"),
        ("SK05", "Supervision"),
        ("SK06", "Customer Service"),
    ],
    columns=["skill_id", "skill_name"],
)

# job_id -> the FIXED set of skills that role requires - not a random pool. This is what
# actually answers "which skill does predicted manpower map to": required_manpower splits
# into job roles (e.g. required_tray_line_associates), and every job role here has a
# defined, non-negotiable skill requirement. Supervisor roles require everything their
# frontline counterpart does, PLUS Supervision.
JOB_REQUIRED_SKILLS = {
    "JOB01": ["SK01", "SK02", "SK04"],  # Tray Line Associate: Food Safety, Patient Interaction/HIPAA, Therapeutic Diet Handling
    "JOB02": ["SK01", "SK02", "SK04", "SK05"],  # Nutrition Services Supervisor: + Supervision
    "JOB03": ["SK06", "SK03"],  # Cafe Associate: Customer Service, Cash Handling
    "JOB04": ["SK06", "SK03", "SK05"],  # Cafe Supervisor: + Supervision
}

# The single most defining/primary skill for each department's FRONTLINE role - what gets
# surfaced directly on the demand tables so a predicted manpower number has an immediate,
# unambiguous skill attached without needing to join to job_skills.
PRIMARY_SKILL_BY_DEPT = {
    "DEP01": ("SK01", "Food Handling & Sanitation Certification"),  # Food & Nutrition frontline = Tray Line Associate
    "DEP02": ("SK03", "Cash Handling"),  # Retail & Cafe frontline = Cafe Associate
}


def _build_job_skills() -> pd.DataFrame:
    rows = []
    for job_id, skill_ids in JOB_REQUIRED_SKILLS.items():
        job_title = JOBS.loc[JOBS["job_id"] == job_id, "job_title"].iloc[0]
        department_id = JOBS.loc[JOBS["job_id"] == job_id, "department_id"].iloc[0]
        for skill_id in skill_ids:
            skill_name = SKILLS.loc[SKILLS["skill_id"] == skill_id, "skill_name"].iloc[0]
            rows.append((job_id, job_title, department_id, skill_id, skill_name))
    return pd.DataFrame(rows, columns=["job_id", "job_title", "department_id", "skill_id", "skill_name"])


JOB_SKILLS = _build_job_skills()

# HPPD (Hours Per Patient Day) - the real industry-standard ratio Sodexo/hospital
# contracts use to convert "how many patients right now" directly into "how many
# labor-hours are required," per department. This is the DIRECT census -> manpower
# link (independent of meals_served/forecasting): required_labor_hours(day) =
# occupied_beds(day) * hppd. Retail & Cafe serves staff/visitors, not patients
# directly, so it scales off hospital size (bed capacity) instead of daily census.
STAFFING_STANDARDS = pd.DataFrame(
    [
        ("DEP01", "PER_OCCUPIED_BED", 0.60, "Food & Nutrition: 0.60 labor-hours per occupied bed per day (tray prep, delivery, cleanup)."),
        ("DEP02", "PER_BED_CAPACITY", 0.12, "Retail & Cafe: 0.12 labor-hours per licensed bed per day (scales with hospital size/visitor traffic, not daily census)."),
    ],
    columns=["department_id", "standard_type", "hppd_ratio", "notes"],
)

# department_id -> [(job_id, is_supervisor, wage_low, wage_high), ...]
DEPT_JOBS = {
    "DEP01": [("JOB01", False, 14, 17), ("JOB02", True, 22, 26)],
    "DEP02": [("JOB03", False, 13, 16), ("JOB04", True, 20, 24)],
}

# department_id -> headcount share of a hospital's total support-services staff
DEPT_HEADCOUNT_SHARE = {
    "DEP01": 0.56, "DEP02": 0.44,
}

# department_id -> shift start hours it needs coverage across
DEPT_SHIFT_STARTS = {
    "DEP01": [6, 11, 16],  # breakfast / lunch / dinner tray lines
    "DEP02": [6, 12],
}

SHIFT_HOURS = 8


# ---------------------------------------------------------------------------
# 2. Employees, pay rates, worker skills
# ---------------------------------------------------------------------------

def _build_employees() -> pd.DataFrame:
    total_beds = LOCATIONS["bed_count"].sum()
    target_total = 150
    rows = []
    emp_seq = 1
    for _, loc in LOCATIONS.iterrows():
        loc_headcount = max(30, round(target_total * loc["bed_count"] / total_beds))
        for dept_id, share in DEPT_HEADCOUNT_SHARE.items():
            dept_headcount = max(1, round(loc_headcount * share))
            dept_jobs = DEPT_JOBS[dept_id]
            for _ in range(dept_headcount):
                is_supervisor_slot = RNG.random() < 0.12 and any(j[1] for j in dept_jobs)
                candidates = [j for j in dept_jobs if j[1] == is_supervisor_slot] or dept_jobs
                job_id, _is_sup, wage_lo, wage_hi = candidates[RNG.integers(0, len(candidates))]
                employment_type = "Part-Time" if RNG.random() < 0.22 else "Full-Time"
                standard_weekly_hours = 20 if employment_type == "Part-Time" else 40
                base_hourly_rate = round(float(RNG.uniform(wage_lo, wage_hi)), 2)
                rows.append((
                    f"EMP{emp_seq:04d}", f"Worker{emp_seq:04d}", loc["location_id"], dept_id, job_id,
                    employment_type, standard_weekly_hours, base_hourly_rate,
                ))
                emp_seq += 1
    return pd.DataFrame(rows, columns=[
        "employee_id", "employee_name", "location_id", "department_id", "job_id",
        "employment_type", "standard_weekly_hours", "base_hourly_rate",
    ])


EMPLOYEES = _build_employees()


def _build_pay_rates(employees: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "employee_id": employees["employee_id"],
        "effective_date": pd.Timestamp(2026, 1, 1),
        "base_hourly_rate": employees["base_hourly_rate"],
        "overtime_multiplier": 1.5,
        "double_time_multiplier": 2,
        "currency": "USD",
    })


def _build_worker_skills(employees: pd.DataFrame) -> pd.DataFrame:
    """Every employee gets exactly the skills their JOB requires (JOB_REQUIRED_SKILLS) -
    deterministic, not a random pool. This is what makes 'predicted manpower -> required
    skill' an unambiguous lookup: a Nutrition Services Supervisor always has Supervision,
    a Tray Line Associate always has Food Safety, no exceptions.
    """
    rows = []
    for _, emp in employees.iterrows():
        for skill_id in JOB_REQUIRED_SKILLS[emp["job_id"]]:
            skill_name = SKILLS.loc[SKILLS["skill_id"] == skill_id, "skill_name"].iloc[0]
            rows.append((emp["employee_id"], skill_id, skill_name, int(RNG.integers(2, 6))))
    return pd.DataFrame(rows, columns=["employee_id", "skill_id", "skill_name", "proficiency_level"])


PAY_RATES = _build_pay_rates(EMPLOYEES)
WORKER_SKILLS = _build_worker_skills(EMPLOYEES)


# ---------------------------------------------------------------------------
# 3. Holidays
# ---------------------------------------------------------------------------

def _build_holidays() -> pd.DataFrame:
    rows = [
        ("2024-01-01", "New Year's Day"), ("2024-05-27", "Memorial Day"),
        ("2024-07-04", "Independence Day"), ("2024-09-02", "Labor Day"),
        ("2024-11-28", "Thanksgiving Day"), ("2024-12-25", "Christmas Day"),
        ("2025-01-01", "New Year's Day"), ("2025-07-04", "Independence Day"),
        ("2025-12-25", "Christmas Day"),
    ]
    return pd.DataFrame(
        [(pd.Timestamp(d), name, round(float(RNG.uniform(0.55, 0.85)), 2)) for d, name in rows],
        columns=["holiday_date", "holiday_name", "demand_multiplier"],
    )


HOLIDAYS = _build_holidays()
_HOLIDAY_MULT = dict(zip(HOLIDAYS["holiday_date"].dt.date, HOLIDAYS["demand_multiplier"]))
_HOLIDAY_NAME = dict(zip(HOLIDAYS["holiday_date"].dt.date, HOLIDAYS["holiday_name"]))


# ---------------------------------------------------------------------------
# 4. Demand: hourly_census (primary driver) + cafe_hourly_sales
# ---------------------------------------------------------------------------

def _date_range(start: date, end: date) -> list[date]:
    days = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(days)]


def _seasonal_flu_factor(d: date) -> float:
    """Winter (flu-season) admissions run higher than summer - a smooth annual cycle peaking in January."""
    day_of_year = d.timetuple().tm_yday
    return 1.0 + 0.12 * np.cos(2 * np.pi * (day_of_year - 15) / 365.25)


def _growth_factor(d: date) -> float:
    days_since_start = (d - DEMAND_START).days
    total_days = (DEMAND_END - DEMAND_START).days
    return 1.0 + 0.05 * (days_since_start / total_days)


_SEASON_BY_MONTH = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer",
    9: "Fall", 10: "Fall", 11: "Fall",
}


def _season_for_month(month: int) -> str:
    return _SEASON_BY_MONTH[month]


# Direct, client-facing staffing ratios: "N patients need this many skilled staff."
# These are what the forecasting model's target (required_manpower) is built from -
# not an intermediate demand number the client has to convert themselves.
MEAL_HOURS = {7: 15, 8: 45, 12: 15, 13: 45, 17: 15, 18: 45}  # hour -> patients per FTE (lower = more staff)
OFF_MEAL_PATIENTS_PER_FTE = 60  # skeleton crew for prep/cleanup/nourishment rounds
CAFE_TRANSACTIONS_PER_FTE = 4.0  # 1 cafe staff handles ~4 transactions/hour
SUPERVISOR_SPAN = 8  # 1 supervisor oversees up to 8 frontline staff on duty


def _split_by_role(total_manpower: int) -> tuple[int, int]:
    """Splits a total headcount into (frontline, supervisor) counts. A lone skeleton-crew
    worker (total=1) covers themselves with no dedicated supervisor; anything larger needs at
    least 1 supervisor, scaling up roughly 1 per SUPERVISOR_SPAN frontline staff.
    """
    if total_manpower <= 1:
        return total_manpower, 0
    supervisors = max(1, int(np.ceil(total_manpower / (SUPERVISOR_SPAN + 1))))
    frontline = max(0, total_manpower - supervisors)
    return frontline, supervisors


ADMISSION_WORKLOAD_FTE = 0.20  # each new admission needs ~0.20 FTE-hours for dietary intake/first-meal setup


def _food_nutrition_required_manpower(occupied_beds: float, admissions_count: int, hour: int) -> int:
    """Routine census workload (occupied_beds, via the meal-hour ratio) PLUS the one-time
    extra workload each new admission creates that hour (dietary intake, first-meal setup) -
    not occupied_beds alone. Two hours with the same patient count but different admission
    volume genuinely need different staffing, which is exactly what makes this worth learning
    from calendar patterns rather than reading off a single snapshot number.
    """
    patients_per_fte = MEAL_HOURS.get(hour, OFF_MEAL_PATIENTS_PER_FTE)
    census_load = occupied_beds / patients_per_fte if occupied_beds > 0 else 0.0
    admission_load = admissions_count * ADMISSION_WORKLOAD_FTE
    return int(np.ceil(census_load + admission_load))


def _build_hourly_census() -> pd.DataFrame:
    days = _date_range(DEMAND_START, DEMAND_END)
    rows = []
    for _, loc in LOCATIONS.iterrows():
        base_occupancy_rate = 0.78  # hospitals run high, stable occupancy
        beds = loc["bed_count"]
        for d in days:
            weekend_dip = 0.94 if d.weekday() >= 5 else 1.0  # elective procedures skew weekday
            holiday_mult = _HOLIDAY_MULT.get(d, 1.0)
            day_factor = _seasonal_flu_factor(d) * _growth_factor(d) * weekend_dip * holiday_mult
            season = _season_for_month(d.month)
            is_holiday = d in _HOLIDAY_MULT
            holiday_name = _HOLIDAY_NAME.get(d, "")
            for h in CENSUS_HOURS:
                # census dips slightly overnight as discharges outpace admissions early morning
                hour_shape = 1.0 - 0.05 * np.cos(2 * np.pi * (h - 14) / 24)
                occupied = beds * base_occupancy_rate * day_factor * hour_shape
                occupied = float(np.clip(occupied + RNG.normal(0, beds * 0.02), 0, beds))
                admissions = max(0, RNG.poisson(beds * 0.006 * day_factor)) if h in (9, 10, 11, 14, 15) else max(0, RNG.poisson(beds * 0.002 * day_factor))
                discharges = max(0, RNG.poisson(beds * 0.005 * day_factor)) if h in (10, 11, 12, 13) else max(0, RNG.poisson(beds * 0.0015 * day_factor))
                transfers = max(0, RNG.poisson(beds * 0.001))

                # meals_served is a FLOW (trays delivered this hour), unlike occupied_beds which is a
                # snapshot/stock - it's what actually drives Food & Nutrition Services labor demand.
                # One tray per occupied patient at each of 3 meal windows; near-zero outside them.
                if h in (7, 8):
                    meals_served = occupied * (0.85 if h == 7 else 0.15)
                elif h in (12, 13):
                    meals_served = occupied * (0.80 if h == 12 else 0.20)
                elif h in (17, 18):
                    meals_served = occupied * (0.80 if h == 17 else 0.20)
                else:
                    meals_served = occupied * 0.02  # late trays, nourishment snacks, etc.
                meals_served = max(0, float(RNG.poisson(max(meals_served, 0.1))))

                # required_manpower is the DIRECT target: given this hour's patient count AND
                # admission volume, how many Food & Nutrition staff are needed. A small amount
                # of noise (+/-1) reflects real variation in patient acuity/dietary complexity
                # that a pure headcount ratio can't capture - without it every hour with the
                # same occupied_beds+admissions would need identical staff, which is
                # unrealistically clean for something a model is meant to learn.
                required_manpower = _food_nutrition_required_manpower(occupied, admissions, h)
                required_manpower = max(0, required_manpower + int(RNG.integers(-1, 2)))
                required_tray_line_associates, required_nutrition_supervisors = _split_by_role(required_manpower)

                primary_skill_id, primary_skill_name = PRIMARY_SKILL_BY_DEPT["DEP01"]

                rows.append((
                    # --- input / context columns ---
                    loc["location_id"], pd.Timestamp(d), h, round(occupied, 1),
                    int(admissions), int(discharges), int(transfers), int(meals_served),
                    season, bool(is_holiday), holiday_name,
                    # --- role/skill breakdown (derived FROM required_manpower, not the target itself) ---
                    required_tray_line_associates, required_nutrition_supervisors,
                    primary_skill_id, primary_skill_name,
                    # --- THE target column - must stay last ---
                    required_manpower,
                ))
    return pd.DataFrame(rows, columns=[
        # --- input / context columns ---
        "location_id", "business_date", "hour_of_day", "occupied_beds", "admissions_count",
        "discharges_count", "transfers_count", "meals_served", "season", "is_holiday", "holiday_name",
        # --- role/skill breakdown (derived FROM required_manpower, not the target itself) ---
        "required_tray_line_associates", "required_nutrition_supervisors",
        "primary_required_skill_id", "primary_required_skill_name",
        # --- THE target column - must stay last ---
        "required_manpower",
    ])


def _build_cafe_sales() -> pd.DataFrame:
    days = _date_range(DEMAND_START, DEMAND_END)
    rows = []
    for _, loc in LOCATIONS.iterrows():
        beds = loc["bed_count"]
        baseline_txn_per_hour = beds * 0.09  # staff + visitor traffic scales with hospital size
        for d in days:
            weekday_boost = 1.15 if d.weekday() < 5 else 0.85  # more staff/visitors on weekdays
            holiday_mult = _HOLIDAY_MULT.get(d, 1.0)
            promo = RNG.random() < 0.04
            event = RNG.random() < 0.02
            day_factor = _growth_factor(d) * weekday_boost * holiday_mult * (1.15 if promo else 1.0) * (1.2 if event else 1.0)
            season = _season_for_month(d.month)
            is_holiday = d in _HOLIDAY_MULT
            holiday_name = _HOLIDAY_NAME.get(d, "")
            for h in CAFE_HOURS:
                meal_peak = 1.6 if h in (7, 8, 12, 13, 17, 18) else 1.0
                txn = max(0, RNG.poisson(baseline_txn_per_hour * meal_peak * day_factor))
                avg_ticket = RNG.uniform(6.5, 11.5)
                sales_amount = round(txn * avg_ticket, 2)

                # Direct target, same principle as Food & Nutrition: given this hour's transaction
                # volume, how many cafe staff are needed.
                required_manpower = int(np.ceil(txn / CAFE_TRANSACTIONS_PER_FTE)) if txn > 0 else 0
                required_manpower = max(0, required_manpower + int(RNG.integers(-1, 2)))
                required_cafe_associates, required_cafe_supervisors = _split_by_role(required_manpower)

                primary_skill_id, primary_skill_name = PRIMARY_SKILL_BY_DEPT["DEP02"]

                rows.append((
                    # --- input / context columns ---
                    loc["location_id"], pd.Timestamp(d), h, sales_amount, int(txn), bool(promo), bool(event),
                    season, bool(is_holiday), holiday_name,
                    # --- role/skill breakdown (derived FROM required_manpower, not the target itself) ---
                    required_cafe_associates, required_cafe_supervisors,
                    primary_skill_id, primary_skill_name,
                    # --- THE target column - must stay last ---
                    required_manpower,
                ))
    return pd.DataFrame(rows, columns=[
        # --- input / context columns ---
        "location_id", "business_date", "hour_of_day", "sales_amount", "transaction_count",
        "promotion_flag", "event_flag", "season", "is_holiday", "holiday_name",
        # --- role/skill breakdown (derived FROM required_manpower, not the target itself) ---
        "required_cafe_associates", "required_cafe_supervisors",
        "primary_required_skill_id", "primary_required_skill_name",
        # --- THE target column - must stay last ---
        "required_manpower",
    ])


HOURLY_CENSUS = _build_hourly_census()
CAFE_HOURLY_SALES = _build_cafe_sales()


# ---------------------------------------------------------------------------
# 5. Workforce operational data (60-day window): schedules, punches, attendance, timecards
# ---------------------------------------------------------------------------

def _build_workforce_ops(employees: pd.DataFrame):
    days = _date_range(OPS_START, OPS_END)
    schedules, punches, attendance, timecards = [], [], [], []
    schedule_seq, punch_seq = 1, 1

    for _, emp in employees.iterrows():
        work_rate = 0.71 if emp["employment_type"] == "Full-Time" else 0.43
        shift_starts = DEPT_SHIFT_STARTS[emp["department_id"]]
        shift_start_hour = int(shift_starts[RNG.integers(0, len(shift_starts))])

        for d in days:
            if RNG.random() >= work_rate:
                continue  # not scheduled this day

            schedule_id = f"SCH{schedule_seq:07d}"
            schedule_seq += 1
            scheduled_start = datetime.combine(d, datetime.min.time()) + timedelta(hours=shift_start_hour)
            scheduled_end = scheduled_start + timedelta(hours=SHIFT_HOURS)
            schedules.append((
                schedule_id, emp["employee_id"], emp["location_id"], emp["department_id"],
                pd.Timestamp(d), scheduled_start, scheduled_end, SHIFT_HOURS,
            ))

            roll = RNG.random()
            if roll < 0.04:
                status, late_minutes, missed_punch = "Absent", 0, True
            elif roll < 0.12:
                status, late_minutes, missed_punch = "Late", int(RNG.integers(6, 30)), False
            else:
                status, late_minutes, missed_punch = "Present", 0, False

            attendance.append((emp["employee_id"], pd.Timestamp(d), schedule_id, status, late_minutes, missed_punch))

            if status == "Absent":
                timecards.append((emp["employee_id"], pd.Timestamp(d), SHIFT_HOURS, 0.0, 0.0, 0.0, False))
                continue

            in_jitter = late_minutes if status == "Late" else int(RNG.integers(-8, 4))
            out_jitter = int(RNG.integers(-3, 25))
            actual_in = scheduled_start + timedelta(minutes=in_jitter)
            actual_out = scheduled_end + timedelta(minutes=out_jitter)
            worked_hours = max(0.0, (actual_out - actual_in).total_seconds() / 3600)
            regular_hours = min(worked_hours, 8.0)
            overtime_hours = max(0.0, worked_hours - 8.0)
            tc_exception = RNG.random() < 0.04

            manual_edit_in = RNG.random() < 0.035
            manual_edit_out = RNG.random() < 0.035
            punch_exception_in = RNG.random() < 0.006
            punch_exception_out = RNG.random() < 0.006

            if not RNG.random() < 0.02:  # ~2% chance the IN punch itself is missing
                punches.append((
                    f"PCH{punch_seq:08d}", emp["employee_id"], emp["location_id"], actual_in, "IN",
                    f"DEV{int(emp['location_id'][-2:]):02d}", manual_edit_in, punch_exception_in,
                ))
                punch_seq += 1
                if RNG.random() < 0.01:  # rare duplicate punch within the window
                    punches.append((
                        f"PCH{punch_seq:08d}", emp["employee_id"], emp["location_id"],
                        actual_in + timedelta(seconds=int(RNG.integers(10, 90))), "IN",
                        f"DEV{int(emp['location_id'][-2:]):02d}", False, False,
                    ))
                    punch_seq += 1

            if not RNG.random() < 0.015:  # ~1.5% chance the OUT punch is missing
                punches.append((
                    f"PCH{punch_seq:08d}", emp["employee_id"], emp["location_id"], actual_out, "OUT",
                    f"DEV{int(emp['location_id'][-2:]):02d}", manual_edit_out, punch_exception_out,
                ))
                punch_seq += 1

            timecards.append((
                emp["employee_id"], pd.Timestamp(d), SHIFT_HOURS,
                round(regular_hours, 2), round(overtime_hours, 2), round(worked_hours, 2), bool(tc_exception),
            ))

    schedules_df = pd.DataFrame(schedules, columns=[
        "schedule_id", "employee_id", "location_id", "department_id", "shift_date",
        "scheduled_start", "scheduled_end", "scheduled_hours",
    ])
    punches_df = pd.DataFrame(punches, columns=[
        "punch_id", "employee_id", "location_id", "punch_timestamp", "punch_type", "device_id",
        "manual_edit_flag", "exception_flag",
    ])
    attendance_df = pd.DataFrame(attendance, columns=[
        "employee_id", "work_date", "schedule_id", "attendance_status", "late_minutes", "missed_punch_flag",
    ])
    timecards_df = pd.DataFrame(timecards, columns=[
        "employee_id", "work_date", "scheduled_hours", "regular_hours", "overtime_hours",
        "total_worked_hours", "exception_flag",
    ])
    return schedules_df, punches_df, attendance_df, timecards_df


SCHEDULES, PUNCHES, ATTENDANCE, TIMECARDS = _build_workforce_ops(EMPLOYEES)


# ---------------------------------------------------------------------------
# 6. Labor budgets (12 months x 10 locations x 10 departments)
# ---------------------------------------------------------------------------

def _build_labor_budgets() -> pd.DataFrame:
    dept_avg_wage = {}
    for dept_id, jobs in DEPT_JOBS.items():
        wages = [((lo + hi) / 2) for _, _, lo, hi in jobs]
        dept_avg_wage[dept_id] = sum(wages) / len(wages)

    rows = []
    for _, loc in LOCATIONS.iterrows():
        headcount_base = max(30, round(150 * loc["bed_count"] / LOCATIONS["bed_count"].sum()))
        for dept_id, share in DEPT_HEADCOUNT_SHARE.items():
            dept_headcount = max(1, round(headcount_base * share))
            monthly_hours = dept_headcount * 40 * 4.33
            monthly_budget = monthly_hours * dept_avg_wage[dept_id] * float(RNG.uniform(0.97, 1.05))
            for month in range(1, 13):
                seasonal = 1.0 + 0.05 * np.cos(2 * np.pi * (month - 1) / 12)
                budget = round(monthly_budget * seasonal)
                rows.append((loc["location_id"], dept_id, 2026, month, int(budget), int(round(budget * 0.08))))
    return pd.DataFrame(rows, columns=["location_id", "department_id", "year", "month", "labor_budget", "overtime_budget"])


LABOR_BUDGETS = _build_labor_budgets()


# ---------------------------------------------------------------------------
# 7. Write workbook
# ---------------------------------------------------------------------------

README_ROWS = [
    ("Sodexo Healthcare POC - Synthetic Dataset", ""),
    ("Purpose", "Synthetic, non-production data modeling a Sodexo Healthcare (hospital support-services) account."),
    ("Segment", "Healthcare only - hospital sites. Not education, not manpower/staffing supply."),
    ("Focus", "Narrowed to 2 hospital sites and 2 departments (Food & Nutrition Services, Retail & Cafe "
              "Services) to keep the dataset targeted at Labor Analytics, Time Punch Analytics, Labor "
              "Forecasting, Predictive Analytics, and AI Recommendations. Administration & Management was "
              "removed entirely - it had no demand signal to forecast on, so it added headcount without "
              "adding anything trainable."),
    ("Demand history", f"{DEMAND_START} through {DEMAND_END} (24 months, hourly)."),
    ("Workforce operational history", f"{OPS_START} through {OPS_END} (60 days, to keep file size practical)."),
    ("Scale", f"{len(EMPLOYEES)} employees, {len(LOCATIONS)} hospitals, {len(DEPARTMENTS)} departments, {len(JOBS)} job roles."),
    ("Important", "No real Sodexo, hospital, patient, or employee data is included."),
    ("Demand model", "The forecasting model's TARGET is manpower directly, not an intermediate demand "
                      "number: hourly_census.required_manpower and cafe_hourly_sales.required_manpower are "
                      "the actual training targets - 'given this many patients/transactions this hour, this "
                      "many skilled staff are needed,' the same shape a client reads directly. "
                      "required_manpower is computed from occupied_beds (Food & Nutrition) or "
                      "transaction_count (Retail & Cafe) via a patients/transactions-per-staff ratio that "
                      "tightens during meal-rush hours, plus small realistic noise - not a flat, mechanical "
                      "ratio. staffing_standards (HPPD - Hours Per Patient Day, the real industry-standard "
                      "ratio Sodexo/hospital contracts use) gives the same census-to-manpower link at a "
                      "daily/no-model-needed grain, for cross-checking the model's output. required_manpower "
                      "is broken down by actual job role, not left as a bare headcount: "
                      "required_tray_line_associates + required_nutrition_supervisors (Food & Nutrition), "
                      "required_cafe_associates + required_cafe_supervisors (Retail & Cafe) - roughly 1 "
                      "supervisor per 8 frontline staff on duty, so 'how many people' also answers 'which "
                      "skilled role.' occupied_beds/admissions/discharges/transfers/meals_served remain as "
                      "supporting context columns."),
    ("Relationships", "Employee/location/department IDs are consistent across workforce, demand, and budget tables."),
    ("Injected patterns", "Winter/flu-season seasonality, weekend dip in elective volume, holiday effects, growth trend, "
                           "absences, missed punches, duplicate punches, manual edits, and overtime."),
    ("Scope note", "This file was generated for a scoped design step: redesigning the synthetic SOURCE DATA only. "
                   "The mock API, ingestion pipeline, backend analytics, and frontend still point at the original "
                   "retail workbook until a later phase rewires them to this dataset's tables and vocabulary."),
]
README_DF = pd.DataFrame(README_ROWS, columns=["Sodexo Healthcare POC - Synthetic Dataset", "Unnamed: 1"])


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT_PATH, engine="openpyxl") as writer:
        README_DF.to_excel(writer, sheet_name="README", index=False)
        LOCATIONS.to_excel(writer, sheet_name="locations", index=False)
        DEPARTMENTS.to_excel(writer, sheet_name="departments", index=False)
        JOBS.to_excel(writer, sheet_name="jobs", index=False)
        JOB_SKILLS.to_excel(writer, sheet_name="job_skills", index=False)
        STAFFING_STANDARDS.to_excel(writer, sheet_name="staffing_standards", index=False)
        EMPLOYEES.to_excel(writer, sheet_name="employees", index=False)
        HOURLY_CENSUS.to_excel(writer, sheet_name="hourly_census", index=False)
        CAFE_HOURLY_SALES.to_excel(writer, sheet_name="cafe_hourly_sales", index=False)
        SCHEDULES.to_excel(writer, sheet_name="schedules", index=False)
        PUNCHES.to_excel(writer, sheet_name="punches", index=False)
        ATTENDANCE.to_excel(writer, sheet_name="attendance", index=False)
        TIMECARDS.to_excel(writer, sheet_name="timecards", index=False)
        PAY_RATES.to_excel(writer, sheet_name="pay_rates", index=False)
        LABOR_BUDGETS.to_excel(writer, sheet_name="labor_budgets", index=False)
        WORKER_SKILLS.to_excel(writer, sheet_name="worker_skills", index=False)
        HOLIDAYS.to_excel(writer, sheet_name="holidays", index=False)

    print(f"Wrote {OUT_PATH}")
    print(f"  employees={len(EMPLOYEES)} locations={len(LOCATIONS)} hourly_census={len(HOURLY_CENSUS)} "
          f"cafe_hourly_sales={len(CAFE_HOURLY_SALES)} schedules={len(SCHEDULES)} punches={len(PUNCHES)} "
          f"attendance={len(ATTENDANCE)} timecards={len(TIMECARDS)} labor_budgets={len(LABOR_BUDGETS)} "
          f"worker_skills={len(WORKER_SKILLS)}")


if __name__ == "__main__":
    main()
