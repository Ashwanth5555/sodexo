from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DimLocation(Base):
    __tablename__ = "dim_location"

    location_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    location_name: Mapped[str] = mapped_column(String(100), nullable=False)
    hospital_type: Mapped[str] = mapped_column(String(50), nullable=False)
    bed_count: Mapped[int] = mapped_column(Integer, nullable=False)


class DimDepartment(Base):
    __tablename__ = "dim_department"

    department_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    department_name: Mapped[str] = mapped_column(String(100), nullable=False)
    cost_center_id: Mapped[str] = mapped_column(String(10), nullable=False)


class DimJob(Base):
    __tablename__ = "dim_job"

    job_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    job_title: Mapped[str] = mapped_column(String(100), nullable=False)
    department_id: Mapped[str] = mapped_column(ForeignKey("dim_department.department_id"), nullable=False)


class DimSkill(Base):
    __tablename__ = "dim_skill"

    skill_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(150), nullable=False)


class DimEmployee(Base):
    __tablename__ = "dim_employee"

    employee_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    employee_name: Mapped[str] = mapped_column(String(100), nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("dim_location.location_id"), nullable=False)
    department_id: Mapped[str] = mapped_column(ForeignKey("dim_department.department_id"), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("dim_job.job_id"), nullable=False)
    employment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    standard_weekly_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    base_hourly_rate: Mapped[float] = mapped_column(Float, nullable=False)


class DimDate(Base):
    __tablename__ = "dim_date"

    date_key: Mapped[str] = mapped_column(String(8), primary_key=True)  # YYYYMMDD
    business_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[str] = mapped_column(String(10), nullable=False)
    is_weekend: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_holiday: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    holiday_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    demand_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)


class BridgeJobSkill(Base):
    """The FIXED skill requirement per job role - a business rule, not a per-employee fact."""

    __tablename__ = "bridge_job_skill"

    job_id: Mapped[str] = mapped_column(ForeignKey("dim_job.job_id"), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("dim_skill.skill_id"), primary_key=True)


class BridgeEmployeeSkill(Base):
    """What skills a specific hired person actually holds."""

    __tablename__ = "bridge_employee_skill"

    employee_id: Mapped[str] = mapped_column(ForeignKey("dim_employee.employee_id"), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("dim_skill.skill_id"), primary_key=True)
    proficiency_level: Mapped[int] = mapped_column(Integer, nullable=False)


class StaffingStandard(Base):
    """HPPD (Hours Per Patient Day) and equivalent ratios - the direct census/
    transaction-to-manpower link, independent of the forecasting model."""

    __tablename__ = "staffing_standard"

    department_id: Mapped[str] = mapped_column(ForeignKey("dim_department.department_id"), primary_key=True)
    standard_type: Mapped[str] = mapped_column(String(30), nullable=False)
    hppd_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str] = mapped_column(String(300), nullable=False, default="")
