from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import (
    DimDate,
    DimDepartment,
    DimEmployee,
    DimJob,
    DimLocation,
    DimSkill,
    EmployeePayRate,
    FactAttendance,
    FactCafeSales,
    FactCensus,
    FactLaborBudget,
    FactPunch,
    FactSchedule,
    FactTimecard,
    StaffingStandard,
)


def _d(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.fromisoformat(str(value)).date()


def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def load_location(db: Session, r: dict, batch_id: str) -> None:
    db.merge(DimLocation(location_id=r["location_id"], location_name=r["location_name"], hospital_type=r["hospital_type"], bed_count=r["bed_count"]))


def load_department(db: Session, r: dict, batch_id: str) -> None:
    db.merge(DimDepartment(department_id=r["department_id"], department_name=r["department_name"], cost_center_id=r["cost_center_id"]))


def load_job(db: Session, r: dict, batch_id: str) -> None:
    db.merge(DimJob(job_id=r["job_id"], job_title=r["job_title"], department_id=r["department_id"]))


def load_staffing_standard(db: Session, r: dict, batch_id: str) -> None:
    db.merge(StaffingStandard(department_id=r["department_id"], standard_type=r["standard_type"], hppd_ratio=r["hppd_ratio"], notes=r.get("notes", "")))


def load_employee(db: Session, r: dict, batch_id: str) -> None:
    db.merge(DimEmployee(
        employee_id=r["employee_id"], employee_name=r["employee_name"], location_id=r["location_id"],
        department_id=r["department_id"], job_id=r["job_id"], employment_type=r["employment_type"],
        standard_weekly_hours=r["standard_weekly_hours"], base_hourly_rate=r["base_hourly_rate"],
    ))


_MERGED_SKILL_IDS: set[str] = set()


def load_skill_dim(db: Session, skill_id: str, skill_name: str) -> None:
    """job_skills/worker_skills repeat the same skill_id across many rows (e.g. every
    Tray Line Associate row references SK01) - merging the same PK repeatedly within
    one flush, before any commit, queues duplicate INSERTs instead of collapsing to
    one. Guard with an in-memory set so each skill_id is merged exactly once per run.
    """
    if skill_id in _MERGED_SKILL_IDS:
        return
    db.merge(DimSkill(skill_id=skill_id, skill_name=skill_name))
    _MERGED_SKILL_IDS.add(skill_id)


def load_pay_rate(db: Session, r: dict, batch_id: str) -> None:
    db.merge(EmployeePayRate(
        employee_id=r["employee_id"], effective_date=_d(r["effective_date"]), base_hourly_rate=r["base_hourly_rate"],
        overtime_multiplier=r["overtime_multiplier"], double_time_multiplier=r["double_time_multiplier"], currency=r["currency"],
    ))


def load_labor_budget(db: Session, r: dict, batch_id: str) -> None:
    db.merge(FactLaborBudget(
        location_id=r["location_id"], department_id=r["department_id"], year=r["year"], month=r["month"],
        labor_budget=r["labor_budget"], overtime_budget=r["overtime_budget"],
    ))


def load_census(db: Session, r: dict, batch_id: str) -> None:
    db.merge(FactCensus(
        location_id=r["location_id"], business_date=_d(r["business_date"]), hour_of_day=r["hour_of_day"],
        occupied_beds=r["occupied_beds"], admissions_count=r["admissions_count"], discharges_count=r["discharges_count"],
        transfers_count=r["transfers_count"], meals_served=r["meals_served"],
        required_tray_line_associates=r["required_tray_line_associates"], required_nutrition_supervisors=r["required_nutrition_supervisors"],
        primary_required_skill_id=r["primary_required_skill_id"], primary_required_skill_name=r["primary_required_skill_name"],
        required_manpower=r["required_manpower"],
    ))


def load_cafe_sales(db: Session, r: dict, batch_id: str) -> None:
    db.merge(FactCafeSales(
        location_id=r["location_id"], business_date=_d(r["business_date"]), hour_of_day=r["hour_of_day"],
        sales_amount=r["sales_amount"], transaction_count=r["transaction_count"],
        promotion_flag=bool(r.get("promotion_flag", False)), event_flag=bool(r.get("event_flag", False)),
        required_cafe_associates=r["required_cafe_associates"], required_cafe_supervisors=r["required_cafe_supervisors"],
        primary_required_skill_id=r["primary_required_skill_id"], primary_required_skill_name=r["primary_required_skill_name"],
        required_manpower=r["required_manpower"],
    ))


def load_schedule(db: Session, r: dict, batch_id: str) -> None:
    db.merge(FactSchedule(
        schedule_id=r["schedule_id"], employee_id=r["employee_id"], location_id=r["location_id"], department_id=r["department_id"],
        shift_date=_d(r["shift_date"]), scheduled_start=_dt(r["scheduled_start"]), scheduled_end=_dt(r["scheduled_end"]),
        scheduled_hours=r["scheduled_hours"], source_batch_id=batch_id,
    ))


def load_punch(db: Session, r: dict, batch_id: str) -> None:
    ts = _dt(r["punch_timestamp"])
    db.merge(FactPunch(
        punch_id=r["punch_id"], employee_id=r["employee_id"], location_id=r["location_id"], punch_timestamp=ts,
        punch_date=ts.date(), punch_type=r["punch_type"], device_id=r["device_id"],
        manual_edit_flag=bool(r.get("manual_edit_flag", False)), exception_flag=bool(r.get("exception_flag", False)),
        source_batch_id=batch_id,
    ))


def load_attendance(db: Session, r: dict, batch_id: str, id_map: dict) -> None:
    key = (r["employee_id"], str(r["work_date"]))
    db.merge(FactAttendance(
        attendance_id=id_map.get(key), employee_id=r["employee_id"], work_date=_d(r["work_date"]), schedule_id=r["schedule_id"],
        attendance_status=r["attendance_status"], late_minutes=r.get("late_minutes", 0),
        missed_punch_flag=bool(r.get("missed_punch_flag", False)), source_batch_id=batch_id,
    ))


def load_timecard(db: Session, r: dict, batch_id: str, id_map: dict) -> None:
    key = (r["employee_id"], str(r["work_date"]))
    db.merge(FactTimecard(
        timecard_id=id_map.get(key), employee_id=r["employee_id"], work_date=_d(r["work_date"]),
        scheduled_hours=r["scheduled_hours"], regular_hours=r["regular_hours"], overtime_hours=r["overtime_hours"],
        total_worked_hours=r["total_worked_hours"], exception_flag=bool(r.get("exception_flag", False)), source_batch_id=batch_id,
    ))


def build_dim_date(db: Session, start: date, end: date, holidays: list[dict]) -> None:
    holiday_map = {}
    for h in holidays:
        hd = _d(h["holiday_date"])
        holiday_map[hd] = (h["holiday_name"], h.get("demand_multiplier"))

    season_by_month = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
                        6: "Summer", 7: "Summer", 8: "Summer", 9: "Fall", 10: "Fall", 11: "Fall"}

    current = start
    while current <= end:
        iso = current.isocalendar()
        is_holiday = current in holiday_map
        name, mult = holiday_map.get(current, (None, None))
        db.merge(DimDate(
            date_key=current.strftime("%Y%m%d"), business_date=current, day_of_week=current.weekday(),
            week=iso.week, month=current.month, quarter=(current.month - 1) // 3 + 1, year=current.year,
            season=season_by_month[current.month], is_weekend=current.weekday() >= 5, is_holiday=is_holiday,
            holiday_name=name, demand_multiplier=mult,
        ))
        current += timedelta(days=1)
