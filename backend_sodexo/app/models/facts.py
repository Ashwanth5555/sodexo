from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FactCensus(Base):
    """The ADT (Admission-Discharge-Transfer) feed - hourly patient census and
    the resulting required manpower for Food & Nutrition Services. This is the
    forecasting TARGET table for that department.
    """

    __tablename__ = "fact_census"

    location_id: Mapped[str] = mapped_column(ForeignKey("dim_location.location_id"), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    hour_of_day: Mapped[int] = mapped_column(Integer, primary_key=True)
    occupied_beds: Mapped[float] = mapped_column(Float, nullable=False)
    admissions_count: Mapped[int] = mapped_column(Integer, nullable=False)
    discharges_count: Mapped[int] = mapped_column(Integer, nullable=False)
    transfers_count: Mapped[int] = mapped_column(Integer, nullable=False)
    meals_served: Mapped[int] = mapped_column(Integer, nullable=False)
    required_tray_line_associates: Mapped[int] = mapped_column(Integer, nullable=False)
    required_nutrition_supervisors: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_required_skill_id: Mapped[str] = mapped_column(String(10), nullable=False)
    primary_required_skill_name: Mapped[str] = mapped_column(String(150), nullable=False)
    required_manpower: Mapped[int] = mapped_column(Integer, nullable=False)


class FactCafeSales(Base):
    """The cafe's POS feed - hourly transactions and the resulting required
    manpower for Retail & Cafe Services. Forecasting TARGET table for that
    department.
    """

    __tablename__ = "fact_cafe_sales"

    location_id: Mapped[str] = mapped_column(ForeignKey("dim_location.location_id"), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    hour_of_day: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_amount: Mapped[float] = mapped_column(Float, nullable=False)
    transaction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    promotion_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    event_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    required_cafe_associates: Mapped[int] = mapped_column(Integer, nullable=False)
    required_cafe_supervisors: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_required_skill_id: Mapped[str] = mapped_column(String(10), nullable=False)
    primary_required_skill_name: Mapped[str] = mapped_column(String(150), nullable=False)
    required_manpower: Mapped[int] = mapped_column(Integer, nullable=False)


class FactSchedule(Base):
    __tablename__ = "fact_schedule"

    schedule_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("dim_employee.employee_id"), nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("dim_location.location_id"), nullable=False)
    department_id: Mapped[str] = mapped_column(ForeignKey("dim_department.department_id"), nullable=False)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    scheduled_hours: Mapped[float] = mapped_column(Float, nullable=False)
    source_batch_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_batch.batch_id"), nullable=True)


class FactPunch(Base):
    __tablename__ = "fact_punch"

    punch_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("dim_employee.employee_id"), nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("dim_location.location_id"), nullable=False)
    punch_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    punch_date: Mapped[date] = mapped_column(Date, nullable=False)
    punch_type: Mapped[str] = mapped_column(String(10), nullable=False)
    device_id: Mapped[str] = mapped_column(String(10), nullable=False)
    manual_edit_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exception_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_batch_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_batch.batch_id"), nullable=True)


class FactAttendance(Base):
    __tablename__ = "fact_attendance"

    attendance_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("dim_employee.employee_id"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("fact_schedule.schedule_id"), nullable=False)
    attendance_status: Mapped[str] = mapped_column(String(20), nullable=False)
    late_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missed_punch_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_batch_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_batch.batch_id"), nullable=True)


class FactTimecard(Base):
    __tablename__ = "fact_timecard"

    timecard_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("dim_employee.employee_id"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_hours: Mapped[float] = mapped_column(Float, nullable=False)
    regular_hours: Mapped[float] = mapped_column(Float, nullable=False)
    overtime_hours: Mapped[float] = mapped_column(Float, nullable=False)
    total_worked_hours: Mapped[float] = mapped_column(Float, nullable=False)
    exception_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_batch_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_batch.batch_id"), nullable=True)


class EmployeePayRate(Base):
    __tablename__ = "employee_pay_rate"

    employee_id: Mapped[str] = mapped_column(ForeignKey("dim_employee.employee_id"), primary_key=True)
    effective_date: Mapped[date] = mapped_column(Date, primary_key=True)
    base_hourly_rate: Mapped[float] = mapped_column(Float, nullable=False)
    overtime_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.5)
    double_time_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")


class FactLaborBudget(Base):
    __tablename__ = "fact_labor_budget"

    location_id: Mapped[str] = mapped_column(ForeignKey("dim_location.location_id"), primary_key=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("dim_department.department_id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    labor_budget: Mapped[float] = mapped_column(Float, nullable=False)
    overtime_budget: Mapped[float] = mapped_column(Float, nullable=False)
