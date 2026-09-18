import pandas as pd

PATH = "data/labor_forecasting_poc_synthetic_dataset.xlsx"
xl = pd.ExcelFile(PATH)
dfs = {name: xl.parse(name) for name in xl.sheet_names}

out = []

def rng(df, col, label=None):
    out.append(f"  {label or col}: min={df[col].min()} max={df[col].max()}")

out.append("=== DATE RANGES ===")
rng(dfs["hourly_sales"], "business_date", "hourly_sales.business_date")
rng(dfs["customer_traffic"], "business_date", "customer_traffic.business_date")
rng(dfs["schedules"], "shift_date", "schedules.shift_date")
rng(dfs["punches"], "punch_timestamp", "punches.punch_timestamp")
rng(dfs["attendance"], "work_date", "attendance.work_date")
rng(dfs["timecards"], "work_date", "timecards.work_date")
rng(dfs["holidays"], "holiday_date", "holidays.holiday_date")
rng(dfs["hourly_sales"], "hour_of_day", "hourly_sales.hour_of_day")
rng(dfs["pay_rates"], "effective_date", "pay_rates.effective_date")

out.append("\n=== VALUE COUNTS ===")
out.append("employment_type:\n" + dfs["employees"]["employment_type"].value_counts().to_string())
out.append("standard_weekly_hours:\n" + dfs["employees"]["standard_weekly_hours"].value_counts().to_string())
out.append("punch_type:\n" + dfs["punches"]["punch_type"].value_counts().to_string())
out.append("attendance_status:\n" + dfs["attendance"]["attendance_status"].value_counts().to_string())
out.append("manual_edit_flag:\n" + dfs["punches"]["manual_edit_flag"].value_counts().to_string())
out.append("exception_flag(punches):\n" + dfs["punches"]["exception_flag"].value_counts().to_string())
out.append("exception_flag(timecards):\n" + dfs["timecards"]["exception_flag"].value_counts().to_string())
out.append("missed_punch_flag:\n" + dfs["attendance"]["missed_punch_flag"].value_counts().to_string())
out.append("scheduled_hours(schedules):\n" + dfs["schedules"]["scheduled_hours"].value_counts().to_string())
out.append("labor_budgets.year:\n" + dfs["labor_budgets"]["year"].value_counts().to_string())
out.append("promotion_flag(sales):\n" + dfs["hourly_sales"]["promotion_flag"].value_counts().to_string())
out.append("event_flag(sales):\n" + dfs["hourly_sales"]["event_flag"].value_counts().to_string())

out.append("\n=== FK INTEGRITY CHECKS ===")
emp_ids = set(dfs["employees"]["employee_id"])
loc_ids = set(dfs["locations"]["location_id"])
dep_ids = set(dfs["departments"]["department_id"])
job_ids = set(dfs["jobs"]["job_id"])
reg_ids = set(dfs["regions"]["region_id"])

def check_fk(df, col, valid_set, label):
    bad = set(df[col].dropna()) - valid_set
    out.append(f"  {label}: {len(bad)} invalid values" + (f" e.g. {list(bad)[:5]}" if bad else ""))

check_fk(dfs["employees"], "location_id", loc_ids, "employees.location_id -> locations")
check_fk(dfs["employees"], "department_id", dep_ids, "employees.department_id -> departments")
check_fk(dfs["employees"], "job_id", job_ids, "employees.job_id -> jobs")
check_fk(dfs["locations"], "region_id", reg_ids, "locations.region_id -> regions")
check_fk(dfs["jobs"], "department_id", dep_ids, "jobs.department_id -> departments")
check_fk(dfs["schedules"], "employee_id", emp_ids, "schedules.employee_id -> employees")
check_fk(dfs["schedules"], "location_id", loc_ids, "schedules.location_id -> locations")
check_fk(dfs["schedules"], "department_id", dep_ids, "schedules.department_id -> departments")
check_fk(dfs["punches"], "employee_id", emp_ids, "punches.employee_id -> employees")
check_fk(dfs["punches"], "location_id", loc_ids, "punches.location_id -> locations")
check_fk(dfs["attendance"], "employee_id", emp_ids, "attendance.employee_id -> employees")
check_fk(dfs["attendance"], "schedule_id", set(dfs["schedules"]["schedule_id"]), "attendance.schedule_id -> schedules")
check_fk(dfs["timecards"], "employee_id", emp_ids, "timecards.employee_id -> employees")
check_fk(dfs["pay_rates"], "employee_id", emp_ids, "pay_rates.employee_id -> employees")
check_fk(dfs["worker_skills"], "employee_id", emp_ids, "worker_skills.employee_id -> employees")
check_fk(dfs["labor_budgets"], "location_id", loc_ids, "labor_budgets.location_id -> locations")
check_fk(dfs["labor_budgets"], "department_id", dep_ids, "labor_budgets.department_id -> departments")
check_fk(dfs["hourly_sales"], "location_id", loc_ids, "hourly_sales.location_id -> locations")
check_fk(dfs["customer_traffic"], "location_id", loc_ids, "customer_traffic.location_id -> locations")

out.append("\n=== EMPLOYEE <-> SCHEDULE LOCATION/DEPT CONSISTENCY ===")
emp_lookup = dfs["employees"].set_index("employee_id")[["location_id","department_id"]]
sched = dfs["schedules"].join(emp_lookup, on="employee_id", rsuffix="_emp")
mismatch_loc = sched[sched["location_id"] != sched["location_id_emp"]]
mismatch_dep = sched[sched["department_id"] != sched["department_id_emp"]]
out.append(f"  schedules with location_id != employee's home location_id: {len(mismatch_loc)} / {len(sched)}")
out.append(f"  schedules with department_id != employee's home department_id: {len(mismatch_dep)} / {len(sched)}")

out.append("\n=== DUPLICATE CHECKS ===")
out.append(f"  duplicate schedule (employee_id, shift_date) rows: {dfs['schedules'].duplicated(subset=['employee_id','shift_date']).sum()}")
out.append(f"  duplicate attendance (employee_id, work_date) rows: {dfs['attendance'].duplicated(subset=['employee_id','work_date']).sum()}")
out.append(f"  duplicate timecards (employee_id, work_date) rows: {dfs['timecards'].duplicated(subset=['employee_id','work_date']).sum()}")
out.append(f"  duplicate hourly_sales (location_id, business_date, hour_of_day): {dfs['hourly_sales'].duplicated(subset=['location_id','business_date','hour_of_day']).sum()}")
out.append(f"  duplicate customer_traffic (location_id, business_date, hour_of_day): {dfs['customer_traffic'].duplicated(subset=['location_id','business_date','hour_of_day']).sum()}")
out.append(f"  duplicate labor_budgets (location_id, department_id, year, month): {dfs['labor_budgets'].duplicated(subset=['location_id','department_id','year','month']).sum()}")
out.append(f"  duplicate pay_rates (employee_id, effective_date): {dfs['pay_rates'].duplicated(subset=['employee_id','effective_date']).sum()}")

out.append("\n=== SANITY: scheduled vs worked hours, negative checks ===")
out.append(f"  schedules.scheduled_end <= scheduled_start count: {(dfs['schedules']['scheduled_end'] <= dfs['schedules']['scheduled_start']).sum()}")
out.append(f"  timecards negative regular_hours: {(dfs['timecards']['regular_hours'] < 0).sum()}")
out.append(f"  timecards negative overtime_hours: {(dfs['timecards']['overtime_hours'] < 0).sum()}")
out.append(f"  hourly_sales negative sales_amount: {(dfs['hourly_sales']['sales_amount'] < 0).sum()}")
out.append(f"  customer_traffic negative customer_count: {(dfs['customer_traffic']['customer_count'] < 0).sum()}")
out.append(f"  pay_rates negative base_hourly_rate: {(dfs['pay_rates']['base_hourly_rate'] < 0).sum()}")
out.append(f"  employees.base_hourly_rate vs pay_rates.base_hourly_rate mismatch count: {(dfs['employees'].set_index('employee_id')['base_hourly_rate'] != dfs['pay_rates'].set_index('employee_id')['base_hourly_rate']).sum()}")

out.append("\n=== SCHEDULES PER LOCATION/DEPT/DAY (staffing granularity) ===")
out.append(dfs["schedules"].groupby(["location_id","shift_date"]).size().groupby(level=0).mean().round(1).to_string())

out.append("\n=== EMPLOYEES PER LOCATION ===")
out.append(dfs["employees"]["location_id"].value_counts().sort_index().to_string())

out.append("\n=== WORKER_SKILLS proficiency levels / skill list ===")
out.append(dfs["worker_skills"]["skill_name"].value_counts().to_string())
out.append("avg skills per employee: " + str(dfs["worker_skills"].groupby("employee_id").size().mean()))

text = "\n".join(str(x) for x in out)
with open("scripts/inspect_output2.txt", "w", encoding="utf-8") as f:
    f.write(text)
print("done, len=", len(text))
