# Phase 1 — Dataset Inspection & Architecture Design Review

**Project:** Labor Forecasting AI POC — AI-Powered Labor Analytics, Forecasting, Workforce Optimization & Manager Decision Support
**Source file:** `data/labor_forecasting_poc_synthetic_dataset.xlsx` (copied from the user's Downloads folder; 9.0 MB, 16 sheets)
**Working root:** `D:\demo\Labour_AI forecast`
**Status:** Design review only. No forecasting, ML, optimization, recommendation engine, React UI, or GenAI code has been implemented. Per the master spec, implementation is gated on approval of this review.

---

## 1. Workbook Inventory

| # | Sheet | Rows (excl. header) | Cols | Role |
|---|-------|----|----|------|
| 1 | README | 7 | 2 | Documentation only |
| 2 | regions | 3 | 2 | Dimension |
| 3 | locations | 10 | 3 | Dimension |
| 4 | departments | 10 | 3 | Dimension |
| 5 | jobs | 15 | 3 | Dimension |
| 6 | employees | 500 | 8 | Dimension (workforce master) |
| 7 | hourly_sales | 102,200 | 7 | Demand fact |
| 8 | customer_traffic | 102,200 | 6 | Demand fact |
| 9 | schedules | 19,490 | 8 | Workforce fact |
| 10 | punches | 37,450 | 8 | Workforce fact |
| 11 | attendance | 19,490 | 6 | Workforce fact |
| 12 | timecards | 19,490 | 7 | Workforce/payroll fact |
| 13 | pay_rates | 500 | 6 | Financial reference |
| 14 | labor_budgets | 1,200 | 6 | Financial fact |
| 15 | worker_skills | 1,500 | 4 | Bridge (many-to-many) |
| 16 | holidays | 9 | 3 | External reference |

The README confirms the intended shape: **24 months of hourly demand** (2024-08-01 → 2026-07-31) and **60 days of workforce operations** (2026-06-02 → 2026-07-31), 500 employees / 10 locations / 3 regions / 10 departments / 15 job roles, no real UKG/Workday/payroll/PII data.

---

## 2. Column-Level Detail & Mapping to Enterprise Source Systems

Per the spec, mock endpoints will only be built for data that actually exists in this workbook.

| Sheet | Key columns | Logical enterprise source | Notes |
|---|---|---|---|
| regions | region_id, region_name | ERP | Org hierarchy top level |
| locations | location_id, location_name, region_id | ERP | |
| departments | department_id, department_name, cost_center_id | ERP | Cost center already present — good for labor-cost-by-cost-center KPI |
| jobs | job_id, job_title, department_id | Workday/HRIS | "Home" department for the *role*, not necessarily the employee (see §4.4) |
| employees | employee_id, employee_name, location_id, department_id, job_id, employment_type, standard_weekly_hours, base_hourly_rate | Workday/HRIS | Employee master. `base_hourly_rate` is duplicated in `pay_rates` (see §4.5) |
| hourly_sales | location_id, business_date, hour_of_day, sales_amount, transaction_count, promotion_flag, event_flag | POS | Full grid, no gaps |
| customer_traffic | location_id, business_date, hour_of_day, customer_count, promotion_flag, event_flag | POS | Same grid as sales |
| schedules | schedule_id, employee_id, location_id, department_id, shift_date, scheduled_start, scheduled_end, scheduled_hours | UKG/Kronos | scheduled_hours is constant (8) for every row |
| punches | punch_id, employee_id, location_id, punch_timestamp, punch_type, device_id, manual_edit_flag, exception_flag | UKG/Kronos | punch_type ∈ {IN, OUT} only — no BREAK_START/BREAK_END, so long-break detection cannot be done from raw punches (see §6) |
| attendance | employee_id, work_date, schedule_id, attendance_status, late_minutes, missed_punch_flag | UKG/Kronos | attendance_status ∈ {Present, Late, Absent} |
| timecards | employee_id, work_date, scheduled_hours, regular_hours, overtime_hours, total_worked_hours, exception_flag | UKG/Kronos (raw) + Payroll (processed) | Same grain as attendance |
| pay_rates | employee_id, effective_date, base_hourly_rate, overtime_multiplier, double_time_multiplier, currency | Payroll | Single `effective_date` (2026-01-01) for all rows — no historical rate changes |
| labor_budgets | location_id, department_id, year, month, labor_budget, overtime_budget | ERP/Finance | Monthly grain, 2026 only |
| worker_skills | employee_id, skill_id, skill_name, proficiency_level (1–4) | Workday/HRIS | 10 distinct skills, ~3 per employee |
| holidays | holiday_date, holiday_name, demand_multiplier | External | Only 9 rows total across 3 years |

---

## 3. Primary Keys, Foreign Keys, Date Relationships

**Primary keys (all confirmed unique, no duplicates):**
`region_id`, `location_id`, `department_id`, `job_id`, `employee_id`, `schedule_id`, `punch_id`, `skill_id` (composite w/ employee for the bridge), `(employee_id, work_date)` for attendance/timecards, `(location_id, business_date, hour_of_day)` for sales/traffic, `(location_id, department_id, year, month)` for budgets.

**Foreign-key integrity — checked exhaustively, zero orphans found:**
`employees→locations/departments/jobs`, `jobs→departments`, `locations→regions`, `schedules→employees/locations/departments`, `punches→employees/locations`, `attendance→employees/schedules`, `timecards→employees`, `pay_rates→employees`, `worker_skills→employees`, `labor_budgets→locations/departments`, `hourly_sales/customer_traffic→locations`. **All 0 invalid.** This is an unusually clean synthetic set.

**Date/time relationships:**
- `hourly_sales` / `customer_traffic`: 2024‑08‑01 → 2026‑07‑31, hour_of_day 8–21 (14 operating hours/day), a complete cross-join of 730 dates × 10 locations × 14 hours = 102,200 rows exactly — **no missing hours**.
- `schedules` / `punches` / `attendance` / `timecards`: 2026‑06‑02 → 2026‑07‑31 (60 days), fully inside the demand window, as documented.
- `scheduled_end > scheduled_start` for 100% of schedule rows (0 violations).
- `holidays`: 2024‑01‑01 → 2026‑12‑25, but only **9 rows total** (New Year, Jul 4, Christmas × 3 years). Only **one** holiday (2026‑07‑04) falls inside the 60-day operational window — thin holiday signal for the short-horizon operational models, though adequate for the 24-month demand forecaster.

---

## 4. Data Quality Findings

1. **No nulls anywhere**, no negative hours/sales/traffic/pay-rates, no duplicate business keys, no orphaned foreign keys. This is a clean, internally-consistent set — good for building the pipeline, but it means the **data-quality/quarantine mechanism (§10 of the master spec) will have nothing real to catch** until either (a) the mock-API layer intentionally simulates late/partial/duplicate batches at the *ingestion* level (recommended — this is normal and doesn't fabricate business data, it just exercises the DQ engine), or (b) the client later provides messier real data.
2. **`employees.base_hourly_rate` and `pay_rates.base_hourly_rate` are byte-for-byte identical for all 500 employees**, and `pay_rates` has a single `effective_date` (2026‑01‑01) for every employee — i.e., no rate history exists. Design decision: treat `pay_rates` (payroll) as the single canonical, time-varying source of truth for pay in the canonical model, and drop the redundant `base_hourly_rate` column from the employee dimension to avoid two sources of truth for the same fact.
3. **`jobs.department_id` (the role's "home" department) disagrees with `employees.department_id` (the employee's actual assigned department) for 245 of 500 employees (49%).** This is not a data error — it likely reflects employees whose job title's nominal department differs from the department they're staffed in (e.g., cross-trained or multi-department roles). Design decision: **`employees.department_id` is authoritative** for all organizational/costing/scheduling logic; `job_id`/`job_title` is descriptive only and must not be used to re-derive department.
4. **`punch_type` only has IN/OUT values** — there is no BREAK_START/BREAK_END or LUNCH punch type. This means "long break" detection (explicitly requested in the SOP and master spec) **cannot be computed from raw punch gaps with confidence**, because a gap between an OUT and the next IN for the same employee/day could be a break, a split shift, or a missed punch pair. This will be implemented as a best-effort heuristic (flag same-day OUT→IN gaps within a configurable window as a "potential unscheduled break," clearly labeled as inferred, not measured) rather than a precise long-break metric.
5. **No employee hire date / termination date / employment status field.** Every employee is implicitly "active" for the whole window. This rules out any credible **turnover/attrition model** (no tenure feature, no termination target label) — consistent with the master spec's guidance that turnover is optional and should not be forced if the data can't support it. **Recommendation: exclude turnover prediction from this POC.**
6. **Events and promotions exist only as booleans (`event_flag`, `promotion_flag`) embedded in the POS tables** — there is no standalone events/campaigns dataset with a name, type, cost, or expected uplift. Explainability for "why did demand spike" will be limited to "an event/promotion flag was active that hour," not "which specific campaign."
7. **No weather data at all** (not even a flag). Weather is called out explicitly in the SOP as a forecasting driver; it is fully absent here.
8. **No employee availability, certifications, shift preferences, union rules, or labor-law/compliance rule tables.** These are explicitly required as optimizer inputs in both the master spec and the SOP.
9. **`scheduled_hours` is a constant 8 for all 19,490 schedule rows**, and `timecards.scheduled_hours` is likewise constant — so "shift length variability" is not represented; all shifts are modeled as 8-hour shifts, only the start time (300 distinct start times) varies.
10. **Reasonable target-label balance for the risk models that *are* supported by the data** (validated by direct computation, not assumed):
    - 52.1% of timecard-days have `overtime_hours > 0` (mean 0.31h, max 3.02h) → good signal for an **overtime-risk** model.
    - 53.1% of employee-weeks exceed the employee's `standard_weekly_hours` → strong signal for **weekly OT-risk / staffing-shortage** features.
    - Attendance: 3.35% Absent, 17.59% Late, 2.41% missed-punch → workable but imbalanced classes for **absenteeism-risk** and **payroll-exception-risk** models; will need class-imbalance handling (class weights / PR-AUC focus, not accuracy).
    - Demand tables cleanly show promotion (+12.7%) and event (+19.1%) sales uplift versus baseline (\$1,734.55 avg) — confirms the synthetic generator encodes a genuine, learnable signal for the forecaster, not noise.

---

## 5. Sufficiency Assessment — Is This Dataset Enough for a Credible POC?

**Yes, with scope adjustments**, for:
- Labor analytics (cost, OT, utilization, productivity, adherence, staffing gap) — fully supported.
- Punch/timecard anomaly detection (missed punch, duplicate, early/late, manual edits, excess hours, payroll exceptions) — fully supported, except long-break (heuristic only, see §4.4).
- Hourly/daily/weekly/monthly demand forecasting from 24 months of sales/traffic — fully supported, real train/validation/test evaluation is possible.
- Overtime-risk, staffing-shortage, absenteeism-risk, schedule-adherence-risk, payroll-anomaly-risk prediction — supported with real, non-trivial class balance.
- Schedule optimization using OR-Tools — supported, but **only with soft/approximate constraints** for availability, certifications, and union/labor-law rules, since none of that data exists (see below). Skill-based matching *is* supported via `worker_skills`.
- Recommendation engine, explainable AI, RBAC, audit, MLflow tracking, dashboards — all supported; these consume the outputs above, not raw workbook columns.

**Not credibly supportable without additional synthetic data:**
- Turnover/attrition prediction (no hire/termination dates).
- Hard-constraint availability-based scheduling (no availability calendar).
- Certification-driven compliance scheduling (no certifications table).
- Weather-driven forecasting (no weather data).
- Named-event/campaign explainability (only boolean flags, no metadata).
- Union rule / labor-law compliance checking against real policy (no rules table) — the SOP's "labor laws / union rules" input.

**Overall verdict:** the dataset is a strong, clean foundation for a genuinely dynamic POC covering ~80% of the SOP's scope end-to-end with real calculations. The remaining ~20% (availability/certifications/weather/events/labor-rules) should be **explicitly scoped out or flagged as "not modeled in this POC due to synthetic data limits"** in the client-facing narrative, rather than faked. Recommended additional synthetic datasets are listed next — **not generated without your explicit go-ahead**, per the master spec.

## 6. Additional Synthetic Data — Recommended, Not Yet Generated

| # | Proposed dataset | Why it's needed |
|---|---|---|
| 1 | `employee_availability` (employee_id, day_of_week/date, available_start, available_end) | Only real way to enforce the optimizer's "employee must be available" **hard constraint** and the SOP's explicit availability input |
| 2 | `employee_certifications` (employee_id, certification_name, issue_date, expiry_date, status) | Needed for certification-based hard constraints and the `COMPLIANCE_REVIEW` recommendation type |
| 3 | `employee_hire_date` / status (hire_date, termination_date, termination_reason, is_active) | Needed for any tenure feature and to make turnover modeling credible; also improves realism of "active workforce" filtering |
| 4 | `weather_daily` (location_id/region_id, business_date, temp, precipitation, condition) | Explicit SOP forecasting driver; currently 0% covered |
| 5 | `events_calendar` (event_id, location_id, business_date, event_name, event_type, expected_impact) | Replaces the opaque `event_flag` boolean with an explainable, named driver |
| 6 | `campaigns_calendar` (campaign_id, location_id, start_date, end_date, name, channel, discount_pct) | Replaces the opaque `promotion_flag` boolean with an explainable, named driver |
| 7 | `labor_rules` (rule_id, scope [location/region/global], rule_type [MAX_DAILY_HOURS, MIN_REST_HOURS, MAX_CONSECUTIVE_DAYS, MEAL_BREAK], value) | Needed so optimizer hard/soft constraints are driven by *configured policy* rather than code-level generic defaults |
| 8 | `shift_preferences` (employee_id, preferred_start_time/day/location, priority) | Needed for the optimizer's soft "preferred shift" objective term |

If none of these are added, the POC will still run end-to-end; the optimizer and predictive layers will simply document (in the UI's explainability panel) which constraints are approximated (e.g., "availability approximated as: not already scheduled elsewhere, within weekly-hour cap") versus data-driven.

---

## 7. Proposed Canonical PostgreSQL Schema

```text
-- Dimensions
dim_region            (region_id PK, region_name)
dim_location          (location_id PK, location_name, region_id FK)
dim_department        (department_id PK, department_name, cost_center_id)
dim_job               (job_id PK, job_title, department_id FK)      -- descriptive only, see Finding 3
dim_employee          (employee_id PK, employee_name, location_id FK, department_id FK,
                        job_id FK, employment_type, standard_weekly_hours,
                        is_active, effective_from, effective_to)     -- SCD1 for POC; SCD2-ready columns
dim_skill             (skill_id PK, skill_name)
dim_date              (date_key PK, business_date, day_of_week, week, month, quarter, year,
                        is_weekend, is_holiday, holiday_name, demand_multiplier)

-- Bridge
bridge_employee_skill (employee_id FK, skill_id FK, proficiency_level, PK(employee_id, skill_id))

-- Workforce facts
fact_schedule         (schedule_id PK, employee_id FK, location_id FK, department_id FK,
                        shift_date FK dim_date, scheduled_start, scheduled_end, scheduled_hours,
                        source_batch_id FK)
fact_punch            (punch_id PK, employee_id FK, location_id FK, punch_timestamp, punch_date,
                        punch_type, device_id, manual_edit_flag, exception_flag, source_batch_id FK)
fact_attendance       (attendance_id PK, employee_id FK, work_date FK dim_date, schedule_id FK,
                        attendance_status, late_minutes, missed_punch_flag, source_batch_id FK)
fact_timecard         (timecard_id PK, employee_id FK, work_date FK dim_date, scheduled_hours,
                        regular_hours, overtime_hours, total_worked_hours, exception_flag, source_batch_id FK)

-- Demand facts
fact_sales            (location_id FK, business_date FK, hour_of_day, sales_amount, transaction_count,
                        promotion_flag, event_flag, PK(location_id, business_date, hour_of_day))
fact_customer_traffic (location_id FK, business_date FK, hour_of_day, customer_count,
                        promotion_flag, event_flag, PK(location_id, business_date, hour_of_day))

-- Financial
employee_pay_rate     (employee_id FK, effective_date, base_hourly_rate, overtime_multiplier,
                        double_time_multiplier, currency, PK(employee_id, effective_date))
fact_labor_budget     (location_id FK, department_id FK, year, month, labor_budget, overtime_budget,
                        PK(location_id, department_id, year, month))

-- AI / Analytics (all populated by the pipeline — never seeded from the workbook)
forecast_run          (run_id PK, model_name, model_version, algorithm, horizon, trained_at,
                        train_start, train_end, feature_set JSON, hyperparameters JSON,
                        mae, rmse, mape, wape, bias)
fact_forecast         (forecast_id PK, run_id FK, location_id FK, department_id FK NULL,
                        business_date, hour_of_day NULL, horizon, predicted_sales, predicted_traffic,
                        predicted_transactions, required_labor_hours, required_headcount,
                        confidence_low, confidence_high, generated_at)
fact_prediction       (prediction_id PK, employee_id FK NULL, location_id FK, department_id FK NULL,
                        model_name, model_version, prediction_type, target_date, target_shift NULL,
                        probability, risk_level, top_drivers JSON, created_at)
fact_punch_anomaly    (anomaly_id PK, punch_id FK NULL, employee_id FK, work_date, rule_code,
                        rule_description, severity, expected_value, actual_value, difference,
                        status, created_at)
optimization_run      (run_id PK, location_id FK, department_id FK NULL, business_date,
                        objective_weights JSON, status, solver_stats JSON, created_at)
optimization_action   (action_id PK, run_id FK, employee_id FK NULL, action_type, from_shift JSON NULL,
                        to_shift JSON NULL, rationale)
fact_recommendation   (recommendation_id PK, recommendation_type, priority, location_id FK,
                        department_id FK NULL, employee_id FK NULL, target_date, problem, reason,
                        recommended_action, expected_impact, estimated_cost, estimated_savings,
                        confidence, source_models JSON, status, created_by, created_at, updated_at)
recommendation_event  (event_id PK, recommendation_id FK, actor, decision, comment,
                        actual_impact, actual_savings, created_at)                -- lifecycle/audit trail
model_metric_snapshot (snapshot_id PK, model_name, model_version, metric_name, metric_value,
                        evaluated_at)                                              -- cache of MLflow metrics for fast UI reads

-- Operations / governance
ingestion_batch       (batch_id PK, source_system, source_endpoint, started_at, completed_at, status,
                        records_received, records_loaded, records_rejected, error_message)
data_quality_issue    (issue_id PK, batch_id FK, source_system, source_record_id, issue_type,
                        severity, description, created_at, resolved)
audit_log             (audit_id PK, user_id, action, resource_type, resource_id, old_value JSON,
                        new_value JSON, created_at)
app_user              (user_id PK, username, password_hash, role, assigned_location_id FK NULL,
                        assigned_region_id FK NULL, created_at)
```

Design notes:
- `dim_certification` / `dim_availability` are **deliberately omitted** for now (Finding 8) — not invented, added only if §6 datasets are approved.
- `employee_pay_rate` replaces `dim_employee.base_hourly_rate` as the single source of truth for pay (Finding 2).
- Every fact table carries `source_batch_id` (workforce facts) for lineage back to `ingestion_batch`.
- All AI/analytics tables are outputs of the pipeline; nothing here is seeded directly from the spreadsheet.

---

## 8. Mock Enterprise API Mapping

| Router | Endpoint | Backed by | Notes |
|---|---|---|---|
| `ukg.py` | `GET /mock/ukg/schedules` | `schedules` | filters: employee_id, location_id, department_id, start_date, end_date |
| | `GET /mock/ukg/punches` | `punches` | filters: employee_id, location_id, start_date, end_date |
| | `GET /mock/ukg/attendance` | `attendance` | filters: employee_id, start_date, end_date |
| | `GET /mock/ukg/timecards` | `timecards` | filters: employee_id, start_date, end_date |
| `workday.py` | `GET /mock/workday/workers` | `employees` | employee master; `/mock/ukg/employees` intentionally **not** duplicated — Workday is the single authoritative employee-master source in this design |
| | `GET /mock/workday/jobs` | `jobs` | |
| | `GET /mock/workday/skills` | `worker_skills` | |
| | `GET /mock/workday/certifications` | *not implemented* | no data — returns `501`-style "not available in this POC" payload, not fabricated data |
| | `GET /mock/workday/availability` | *not implemented* | same as above |
| `pos.py` | `GET /mock/pos/sales` | `hourly_sales` | filters: location_id, start_date, end_date |
| | `GET /mock/pos/traffic` | `customer_traffic` | |
| `payroll.py` | `GET /mock/payroll/pay-rates` | `pay_rates` | |
| | `GET /mock/payroll/timecards` | `timecards` | same underlying table as UKG timecards (Finding — the workbook doesn't distinguish raw vs. processed); documented as a logical/processed view, not a second data source |
| | `GET /mock/payroll/exceptions` | `timecards` filtered `exception_flag=True` | derived filter, not a new dataset |
| `erp.py` | `GET /mock/erp/regions` | `regions` | |
| | `GET /mock/erp/locations` | `locations` | |
| | `GET /mock/erp/departments` | `departments` | |
| | `GET /mock/erp/labor-budgets` | `labor_budgets` | filters: location_id, department_id, year, month |
| `external.py` | `GET /mock/external/holidays` | `holidays` | |
| | `GET /mock/external/events` | `hourly_sales`/`customer_traffic` rows where `event_flag=True`, re-projected into a calendar shape | **explicitly labeled** as derived from embedded POS flags, not a standalone events source |
| | `GET /mock/external/campaigns` | same tables, `promotion_flag=True` | same caveat as above |
| | `GET /mock/external/weather` | *not implemented* | no data at all |

All routers share a `services/source_reader.py` that reads from the canonical Postgres tables (loaded once from Excel at startup/seed time) rather than re-parsing the workbook per request, plus a common response envelope (`data`, `meta: {source, endpoint, count, page}`, `errors`) and a `/health` endpoint per the spec.

---

## 9. Backend Application API Structure (`/api/...`)

```text
/api/auth            POST /login, GET /me
/api/analytics        /labor-cost, /utilization, /overtime, /attendance, /adherence, /productivity
                       (all accept region/location/department/date-range/time-grain query params)
/api/punch-analytics   /exceptions, /exceptions/{id}, /summary
/api/forecast          /runs, /runs/{id}, /hourly, /daily, /weekly, /monthly, /accuracy
/api/predictions       /overtime-risk, /absence-risk, /staffing-shortage, /schedule-adherence,
                       /payroll-anomaly, /peak-workload
/api/optimization      POST /run, GET /runs/{id}, GET /runs/{id}/actions
/api/recommendations   GET /, GET /{id}, POST /{id}/approve|reject|modify
/api/alerts            GET /, POST /{id}/ack
/api/data-quality      /sources, /batches, /issues
/api/models            /forecast, /predictive  (metrics for Model Performance page)
/api/assistant         POST /ask   (routes to trusted analytics APIs, then optionally an LLM)
```

Routers call **services → repositories → SQLAlchemy models**; no raw SQL or DB sessions inside routers, no business calculations inside the React layer.

---

## 10. Frontend Structure

React Router pages, one folder per page, matching §23 of the master spec exactly (Overview, ExecutiveDashboard, OperationsDashboard, ManagerDashboard, LaborAnalytics, Forecasting, PunchAnalytics, PredictiveRisk, Optimization, Recommendations, AIAssistant, DataQuality, ModelPerformance, Administration), with a shared `services/api.ts` client, typed `hooks/useAnalytics.ts` / `useForecast.ts` / `usePredictions.ts` / `useRecommendations.ts`, and reusable `components/{layout,cards,charts,tables,filters,badges,alerts,recommendations,dialogs,assistant}`. No page-local fetch logic, no duplicated formatting/calculation code.

---

## 11. Docker Compose Services

```text
postgres        -- canonical DB + staging schema
mock-api        -- FastAPI mock source-system layer (Section 8)
backend-api     -- FastAPI application layer (Section 9), owns analytics/forecast/ml/optimization/recs
frontend        -- Vite production build served via a lightweight static server
mlflow          -- local tracking server (SQLite/local artifact store — no cloud backend)
ollama          -- optional, only started if LLM_PROVIDER=ollama
```
`.env.example` will hold `DATABASE_URL`, `JWT_SECRET`, `LLM_PROVIDER`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OPENAI_API_KEY` (optional), `OPENAI_MODEL` (optional), `APP_DEMO_ASOF_DATE` (see §13, risk #5).

---

## 12. Implementation Phases & Dependencies

Following the master spec's §52 ordering, with dependencies made explicit:

1. **Dataset inspection & design review** — *this document* (complete).
2. **Project foundation + Postgres + Docker** — depends on §1 schema sign-off.
3. **Mock enterprise APIs** — depends on #2 (DB must exist to seed from).
4. **Ingestion + validation + canonical model** — depends on #3.
5. **Labor analytics** — depends on #4 (needs canonical facts).
6. **Punch analytics** — depends on #4; independent of #5.
7. **Labor forecasting** — depends on #4 (fact_sales/fact_customer_traffic populated); independent of #5/#6.
8. **Predictive analytics** — depends on #5 and #6 (features come from labor + punch analytics outputs).
9. **Schedule optimization** — depends on #7 (needs forecast → required labor) and #6 (skills); availability/certifications will be approximated per Finding 8 unless §6 data is approved.
10. **Recommendation engine** — depends on #5–#9 (consumes all upstream outputs).
11. **Explainable AI** — depends on #7–#10 (wraps existing model/optimizer outputs; adds no new data).
12. **React frontend** — can start in parallel with #5 once #9's API contracts (Section 9) are frozen; wired page-by-page as each backend area lands.
13. **Optional GenAI assistant** — depends on #5–#10 being callable as trusted "tools."
14. **Auth/RBAC/audit** — should land early (end of #4) so every later phase writes audit records from the start, not retrofitted.
15. **MLflow + model monitoring** — wired in during #7 and #8 (log at training time, not after the fact).
16. **End-to-end testing + dynamic demo scenarios** — depends on everything; validates the 4 scenarios in §47 of the master spec actually recompute rather than being scripted.
17. **Documentation + demo readiness** — final.

---

## 13. Technical Risks

1. **Thin operational window (60 days).** ~8–9 weeks per employee is workable for classification models (with the class balance shown in §4.10) but leaves little room for chronological train/val/test splitting *within* the punch/attendance data alone — the forecaster benefits from the full 24 months, but the punch-anomaly/predictive-risk models are constrained to the 60-day window since that's the only workforce-operations history available.
2. **`employees.department_id` vs `jobs.department_id` disagreement (49% of employees)** must be handled consistently everywhere (analytics, optimization, RBAC scoping) — resolved in §4.3 by treating `employees.department_id` as authoritative; this must be enforced in code review, not just documented.
3. **No availability/certification/labor-rule data** means the optimizer's "hard constraints" will initially be approximated (see §6) rather than sourced from real policy — must be clearly labeled in the Optimization UI's explanation panel so it isn't mistaken for compliance-grade output.
4. **Punch data has no break punch type**, so "long break" detection is inferred from IN/OUT gaps, not measured directly — must be labeled as a heuristic in punch-anomaly explanations, consistent with the master spec's "potential suspicious pattern, not an accusation" requirement.
5. **Wall-clock date mismatch.** Today's date is 2026-08-13, but the workbook's operational data ends 2026-07-31 (~2 weeks earlier). "Today" screens (Operations/Manager dashboards) must not key off the real system clock or they'll show an empty/stale "today." Mitigation: introduce a configurable `APP_DEMO_ASOF_DATE` (defaulting to the max date present in the ingested data) that all "current day" views resolve against, so the demo behaves sensibly regardless of the actual calendar date.
6. **Clean data means the DQ/quarantine mechanism has nothing to catch from the workbook alone** — mitigated by having the mock-API/ingestion layer simulate realistic operational noise (duplicate batch replay, a late/partial batch, a малformed record) at the *pipeline* level, which exercises the DQ engine honestly without fabricating business data.

## 14. Assumptions

- `dim_employee` is treated as a current snapshot (SCD1) for the POC; SCD2 columns (`effective_from`/`effective_to`) are included in the schema so history can be turned on later without a migration.
- `employee_pay_rate` (payroll) is the canonical, evolving pay source going forward, even though today it's a 1:1 mirror of `employees.base_hourly_rate`.
- `job_id`/`job_title` is descriptive metadata only; it is never used to override `employees.department_id`.
- Store operating hours are fixed at 08:00–21:00 (14 hours), derived from the data, and used to bound the hourly forecast horizon.
- Currency is fixed at USD.
- Turnover prediction is out of scope for this POC (no hire/termination data); this will be called out explicitly to the client rather than silently dropped.
- RBAC location/region scoping (Location Manager, Operations Manager personas) is implemented via `app_user.assigned_location_id/assigned_region_id`, seeded independently of the workbook — this does **not** require new synthetic HR data, since access scope is an application-config concern, not a workforce-data concern.

## 15. What Should Be Simplified to Stay Free/Practical

- MLflow with local file-based backend store + local artifact store (no S3/Postgres-backed MLflow needed for POC scale).
- No Kafka/streaming — batch ingestion via scheduled/triggered pulls from the mock API is sufficient at this data volume (~160k rows total).
- No Kubernetes — Docker Compose only.
- Ollama is optional and the app must run fully (minus the chat assistant) with `LLM_PROVIDER` unset.
- Prometheus/Grafana are skipped for the POC; structured logs + the Model Performance/Data Quality pages cover the monitoring story adequately at this scale.

## 16. Ensuring the UI Stays Professional, Dynamic, and Traceable

- **Professional feel:** a single design system (shadcn/ui + Tailwind tokens) reused across every page — KPI cards, tables, badges, and chart wrappers are shared components, not one-off styles per page, which is what avoids the "classroom project" look more than any single visual choice.
- **Filters/drill-down:** global filter state (date range, region, location, department, employee-if-authorized) lives in a shared context/query-param layer; every page's data hooks (`useAnalytics`, `useForecast`, etc.) take the current filter state as input to their API calls, so changing a filter always re-fetches from the backend — there is no page that reads filters but ignores them.
- **Dynamic KPIs:** every number rendered in the UI traces to a backend API call whose SQL/pandas computation reads canonical tables — there is no JSON fixture, mock data file, or hard-coded constant behind any KPI, chart, or table in the design.
- **Real ML, not fixed outputs:** forecast/prediction endpoints return the live output of a model object trained from `forecast_run`/model artifacts, evaluated with real MAE/RMSE/MAPE/WAPE/bias computed against held-out chronological data — accuracy is measured, not asserted.
- **Future real-system swap-in:** because the mock API layer's response contracts (Section 8) are what the ingestion layer consumes — not the Excel file directly — replacing `mock_api` with a real UKG/Workday/POS/Payroll/ERP connector later only requires matching the same endpoint contracts; canonical schema, analytics, ML, optimization, and the entire frontend are unaffected.

---

## Next Step

This concludes the Phase 1 design review. Per the master spec, **implementation does not begin until this is approved.** Two things need a decision before Phase 2 starts:

1. **Schema/mapping sign-off** — confirm the canonical model (§7), mock API mapping (§8), and the authoritative-source decisions in Findings 2/3 are acceptable.
2. **Additional synthetic data (§6)** — say which, if any, of the 8 proposed datasets (availability, certifications, hire/termination dates, weather, events calendar, campaigns calendar, labor rules, shift preferences) should be generated before Phase 2, versus deferring those features and documenting them as known POC limitations.
