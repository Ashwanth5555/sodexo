import pandas as pd

PATH = "data/labor_forecasting_poc_synthetic_dataset.xlsx"
xl = pd.ExcelFile(PATH)
dfs = {name: xl.parse(name) for name in xl.sheet_names}

out = []

out.append("=== job.department_id vs employee.department_id consistency ===")
emp = dfs["employees"]
jobs = dfs["jobs"].set_index("job_id")["department_id"]
emp_job_dep = emp["job_id"].map(jobs)
out.append(f"  mismatches: {(emp_job_dep != emp['department_id']).sum()} / {len(emp)}")

out.append("\n=== holidays within operational window (2026-06-02..2026-07-31) ===")
hol = dfs["holidays"]
mask = (hol["holiday_date"] >= "2026-06-02") & (hol["holiday_date"] <= "2026-07-31")
out.append(hol[mask].to_string())

out.append("\n=== timecards: overtime distribution ===")
tc = dfs["timecards"]
out.append(f"  pct rows with overtime_hours > 0: {(tc['overtime_hours']>0).mean()*100:.1f}%")
out.append(f"  overtime_hours describe:\n{tc['overtime_hours'].describe().to_string()}")
out.append(f"  total_worked_hours describe:\n{tc['total_worked_hours'].describe().to_string()}")

out.append("\n=== attendance: absenteeism / lateness rates ===")
att = dfs["attendance"]
out.append(f"  pct Absent: {(att['attendance_status']=='Absent').mean()*100:.2f}%")
out.append(f"  pct Late: {(att['attendance_status']=='Late').mean()*100:.2f}%")
out.append(f"  pct missed_punch_flag True: {att['missed_punch_flag'].mean()*100:.2f}%")

out.append("\n=== per-employee weekly hours vs standard_weekly_hours (OT risk feasibility) ===")
tc2 = tc.merge(emp[["employee_id","standard_weekly_hours"]], on="employee_id")
tc2["week"] = tc2["work_date"].dt.to_period("W")
weekly = tc2.groupby(["employee_id","week"]).agg(worked=("total_worked_hours","sum"), std=("standard_weekly_hours","first")).reset_index()
weekly["over_std"] = weekly["worked"] > weekly["std"]
out.append(f"  pct employee-weeks exceeding standard_weekly_hours: {weekly['over_std'].mean()*100:.1f}%")

out.append("\n=== punches: exception_flag vs manual_edit_flag overlap ===")
p = dfs["punches"]
out.append(f"  exception AND manual_edit: {((p['exception_flag']) & (p['manual_edit_flag'])).sum()}")
out.append(f"  punches per employee per day (avg, should be ~2 IN/OUT): {len(p)/att['employee_id'].nunique()/60:.2f}")

out.append("\n=== sales/traffic correlation with promo/event/holiday (sanity of synthetic generator) ===")
hs = dfs["hourly_sales"]
out.append(f"  avg sales_amount overall: {hs['sales_amount'].mean():.2f}")
out.append(f"  avg sales_amount when promotion_flag=True: {hs[hs['promotion_flag']]['sales_amount'].mean():.2f}")
out.append(f"  avg sales_amount when event_flag=True: {hs[hs['event_flag']]['sales_amount'].mean():.2f}")
hol_dates = set(hol["holiday_date"])
hs_is_hol = hs["business_date"].isin(hol_dates)
out.append(f"  avg sales_amount on holiday dates: {hs[hs_is_hol]['sales_amount'].mean():.2f}")
out.append(f"  avg sales_amount on non-holiday dates: {hs[~hs_is_hol]['sales_amount'].mean():.2f}")

text = "\n".join(str(x) for x in out)
with open("scripts/inspect_output3.txt", "w", encoding="utf-8") as f:
    f.write(text)
print("done")
