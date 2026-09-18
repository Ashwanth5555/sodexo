# Sodexo Healthcare — Labor Forecasting & Workforce Intelligence

A working proof of concept that ingests enterprise workforce data, forecasts how much
labor each site needs, and turns that into decisions a manager can act on.

Everything on screen is computed from the database at request time. There are no
hardcoded numbers, no mocked chart data, and no invented AI answers anywhere in the
application.

---

## Contents

1. [How to run it](#1-how-to-run-it)
2. [What the data looks like](#2-what-the-data-looks-like)
3. [Page by page](#3-page-by-page)
4. [How the forecasting works](#4-how-the-forecasting-works)
5. [What this build does *not* do](#5-what-this-build-does-not-do)
6. [Actual output, page by page](#6-actual-output-page-by-page)

---

## 1. How to run it

Three processes. Start them in this order.

**Mock enterprise API** (stands in for UKG / Workday / POS / Payroll / ERP):

```bash
cd "D:/demo/Labour_AI forecast/mock_api_sodexo" && "../.venv/Scripts/python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8200
```

**Backend** (ingestion, forecasting engine, analytics, AI services):

```bash
cd "D:/demo/Labour_AI forecast/backend_sodexo" && "../.venv/Scripts/python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8300 --reload
```

**Frontend**:

```bash
cd "D:/demo/Labour_AI forecast/frontend_sodexo" && npm run dev -- --port 5183 --host 127.0.0.1
```

Then open `http://127.0.0.1:5183`.

### Signing in

The application is behind a login — no page is reachable without a valid session. Three
accounts ship with the build:

| Username  | Password        | Role               |
| --------- | --------------- | ------------------ |
| `admin`   | `Admin@2026`    | Administrator      |
| `manager` | `Manager@2026`  | Operations Manager |
| `analyst` | `Analyst@2026`  | Data Analyst       |

The backend hashes these with PBKDF2-SHA256 at startup and issues an HMAC-signed bearer
token (8 hours) on `POST /api/auth/login`; the UI stores it and sends it on every request,
signing out automatically on any `401`. The roles are recorded on the token and shown in
the header — they do not yet gate features.

Override the passwords, token lifetime and signing secret in `.env.sodexo`:

```
AUTH_SECRET_KEY=<a long random string>
AUTH_TOKEN_EXPIRE_MINUTES=480
AUTH_ADMIN_PASSWORD=...
AUTH_MANAGER_PASSWORD=...
AUTH_ANALYST_PASSWORD=...
AUTH_PROTECT_API=false
```

`AUTH_PROTECT_API=true` additionally rejects unauthenticated calls to every `/api` route,
not just the UI. It is off by default because the ingestion/training scripts and `/docs`
call the API directly without a token.

### Deployed demo (Azure)

Live at **https://ca-sodexo-demo.nicerock-adeb5dcd.centralindia.azurecontainerapps.io**,
with the same three logins.

| | |
| --- | --- |
| Resource group | `rg-sodexo-demo-ci` (centralindia) |
| Registry / image | `acrsodexodemoci` → `sodexo-forecast:v1` |
| Container App | `ca-sodexo-demo` — 2 vCPU / 4Gi, pinned to 1 replica |

One image runs all three processes (`Dockerfile` + `deploy/`): nginx serves the built
frontend and proxies `/api` to the backend, with the mock enterprise API alongside — so
there is one URL and no CORS.

**Pinned to a single replica on purpose.** Training runs in background threads behind an
in-process lock, so scaling out would corrupt runs and scale-to-zero would kill them
mid-training.

**Access is IP-restricted.** Only allowlisted addresses can reach it; everything else is
denied. Add one with:

```bash
az containerapp ingress access-restriction set -g rg-sodexo-demo-ci -n ca-sodexo-demo --rule-name "<name>" --ip-address <ip>/32 --action Allow
```

**Secrets are never in the image.** `.env.sodexo` is excluded by `.dockerignore`;
`OPENAI_API_KEY` and `AUTH_SECRET_KEY` are injected as Container App secrets. The
deployment uses a generated `AUTH_SECRET_KEY`, not the repo default. Rotate a secret with:

```bash
az containerapp secret set -g rg-sodexo-demo-ci -n ca-sodexo-demo --secrets openai-key=<new-key>
```

**Storage is ephemeral.** The seeded database and its trained models ship inside the image,
so the built-in series work immediately — but datasets uploaded or trained on the live site
are lost when the container restarts. Mount Azure Files on `/app/data` to change that.

Rebuild and redeploy after a code change:

```bash
az acr build --registry acrsodexodemoci --image sodexo-forecast:v2 --platform linux/amd64 . && az containerapp update -g rg-sodexo-demo-ci -n ca-sodexo-demo --image acrsodexodemoci.azurecr.io/sodexo-forecast:v2
```

### First run, in order

1. **Data Quality → Run Ingestion.** Nothing works before this; it pulls all 16 source
   endpoints into the canonical database. (The app opens on Data Quality — there is no
   Overview page.)
2. **Labor Forecasting → Model Training → Train All Models.** Expect roughly 6–10 minutes
   at the 180-day default. Until this finishes, every forecasting page will say there is
   no trained model yet.

   To forecast your own data instead, skip to **Data Quality → Upload Data**; an uploaded
   dataset is trained from there and needs no ingestion run.
3. Everything else is then live.

Restart the backend after any Python change — it does not always hot-reload cleanly, and a
stale process serving old code has been the cause of most "why hasn't this updated"
moments.

---

## 2. What the data looks like

**Four forecast series** — two locations × two departments:

| Series | Demand signal |
|---|---|
| Community Hospital / Food & Nutrition | `occupied_beds` |
| Regional Medical Center / Food & Nutrition | `occupied_beds` |
| Community Hospital / Retail & Café | `transaction_count` |
| Regional Medical Center / Retail & Café | `transaction_count` |

**What is being predicted:** `required_manpower` — staff-hours needed.

**Grain matters, and it differs by page.** This is the single most important thing to
understand about the app:

| Source table | Native grain | What that allows |
|---|---|---|
| `fact_census`, `fact_cafe_sales` | hourly | hour-of-day analysis, forecasting |
| `fact_punch` | timestamped | hour-of-day punch analysis |
| `fact_timecard`, `fact_attendance` | **daily** | no hourly cost or attendance is possible |

Labor cost therefore does not exist at hourly resolution in the source, which is why the
forecasting grain is built on `fact_census` / `fact_cafe_sales` rather than the timecard
tables. (The Labor Analytics and Time Punch Analytics pages that used to read these have
been removed from the UI; the underlying services remain, because AI Recommendations and
the AI Assistant still query them.)

---

## 3. Page by page

### Data Quality

Per-source health for all 16 endpoints: records received, loaded, rejected, and freshness.

**What to look for:** any non-zero *rejected* count. A healthy sync shows zero rejects
across every source.

---

### Upload Data — forecast your own dataset

Data Quality → **Upload Data** takes any CSV or Excel file, not just the Sodexo
schema. On upload the file is parsed and profiled, and the app proposes a
mapping, which you confirm or change:

| Field | What it does |
| --- | --- |
| **Date column** | The time axis. Columns that parse as dates are marked ✓. |
| **Forecast** | The numeric column to predict. |
| **Group by** | Optional. Splits the file into one series per value (e.g. per region), up to 40. |
| **Frequency** | Hourly, Daily, Weekly or Monthly. Inferred from the gaps between dates; rows are resampled to it. |
| **Combine duplicates by** | Sum or average, when several rows fall in the same period. |
| **Horizon** | How many periods to forecast ahead. |
| **Drivers** | Optional extra numeric columns fed to the models as exogenous inputs. |

Saving the mapping validates it immediately — a column that yields no usable
rows is reported there, not after a training run. **Train** then runs the *same*
13-model portfolio, the same rolling-origin cross-validation, the same metrics
and the same champion-by-MAPE rule used for the built-in series.

Uploaded series then appear in the series picker everywhere alongside the
built-in ones, and drive Data Analysis, Data Pre-processing, Model Training,
Forecast Explorer, Drift Monitoring and Model Performance.

**What an uploaded series does not get.** Demand Drivers & Feature Mapping,
What-if sensitivity and Staffing Impact depend on the canonical schema — the
holiday calendar, promotion/event flags and staffing standards, none of which an
arbitrary file has. Selecting an uploaded series on those tabs says so plainly
rather than inventing numbers.

**Seasonality and folds adapt to the frequency.** Grain is no longer a global
constant: a daily upload tests a weekly cycle, a monthly one tests a yearly
cycle, and the CV fold sizes, XGBoost lag windows and LSTM sequence length are
all expressed in that series' own periods.

**Two independent training entry points.** "Train All Models" trains the
built-in series; **Train** on a dataset trains that dataset. Results are read
per series, from that series' own most recent run, so training one never blanks
out the other's forecasts.

### Labor Forecasting

Eight sub-pages in the sidebar, in workflow order.

#### Demand Drivers & Feature Mapping

The target column and every input column, per series. **Input columns are clickable** —
turning one off persists to the database and changes what the next training run uses. The
badge shows "10 of 11 inputs selected".

#### Data Analysis

| Chart | What it shows |
|---|---|
| **Historical demand trend** | daily average with a 7-day smoothed line over it |
| **Demand curve by hour of day** | the meal-service shape — peaks at 07:00, 12:00, 17:00 |
| **Demand by day of week** | weekday vs weekend |
| **Seasonal demand** | by season |
| **Demand drivers at a glance** | holiday / promotion / event lift as plain percentages |

Every chart carries a one-line finding computed from the same data, e.g. *"Busiest at 7:00
(6.86) · quietest at 1:00 (2)."*

#### Data Pre-processing

What happens between raw data and training: total history, training window, resolved
seasonality, forecast horizon, input matrix shape, the validation plan, and **a preview of
the first 10 rows actually fed to the models** — with `required_manpower` last, as the
target column.

#### Model Training

Pick a **training window** (120 / 180 / 365 / 730 days, default 180) and hit **Train All
Models**. While it runs:

- a pulsing **LIVE** badge and a progress bar (`n / 52 model runs`)
- a **live activity feed** of models as they complete, newest first
- the just-finished row glows briefly in the comparison table
- **champion cards** per series once selection completes

The leaderboard shows all 13 models per series with status, runtime, train/test rows, and
every metric. Models that cannot run are marked `not_applicable` **with a reason** rather
than silently dropped.

If you navigate to this page mid-run, it detects the running job and resumes live polling.

#### Forecast Explorer

**The main chart — this is what the lines mean:**

| Line | Meaning |
|---|---|
| **Grey, solid — "Actual"** | what actually happened, across the full history |
| **Orange, solid — "Backtest prediction"** | what the model predicted for periods where the answer was already known. Drawn only over the most recent third, so it reads as "here is the window we validated against" rather than two lines fighting end to end |
| **Green, dashed — "Future forecast"** | the 7 days beyond the last actual. Dashed because nothing here is verified yet |
| **Vertical dashed line — "Forecast begins"** | the boundary between known and predicted |

Controls: **Model** (defaults to the current champion, named explicitly, with every other
model selectable), **View** (Full timeline / Backtest focus / Forecast focus), and
**Granularity** (Half-day / Daily total / Weekly average).

**How to read it honestly:** compare orange against grey in the overlap. If the orange line
is flatter than the grey — hugging the middle while grey swings above and below — the model
is predicting near the average rather than tracking the pattern, and a high accuracy number
is coming from a stable series rather than a skilful model. This is measurably the case for
the Food & Nutrition series; see [section 5](#5-what-this-build-does-not-do).

#### Drift Monitoring

Answers "can I still trust this model?"

**Demand signal chart** — the real-world driver, day by day. Its key:

| Element | Meaning |
|---|---|
| Solid black line | the signal each day (e.g. occupied beds) |
| Grey dashed | previous 30-day average |
| Orange dashed | last 7-day average |
| Shaded band, right | the last 7 days |

When the two dashed averages sit on top of each other, demand has not moved and the models
are still trained on the right pattern. **Regular dips are weekends** — verified: every dip
in the sample window falls on a Saturday or Sunday, dropping from 86–87 beds midweek to
80–82 at weekends.

The card header states how strongly the signal actually tracks staffing, measured at the
model's own grain — and warns you when it is weak rather than asserting it is *the* driver.

**"Has the shape of the day changed?"** — staff needed per hour, last 7 days vs previous 30.
Lines on top of each other means the daily rhythm is unchanged. Peaks moving or changing
height means busy periods are shifting and the roster *shape* needs revisiting, not just
headcount.

Status thresholds are stated plainly: within 15% = **Stable**, 15–30% = **Watch**, beyond
30% = **Drift Detected** (retrain).

#### Staffing Impact

**Required vs scheduled headcount** — lines for the two curves, plus colour-coded bars for
the gap itself (red = understaffed, gold = overstaffed, green = balanced).

**Click any point to drill down.** The panel shows Required / Scheduled / Gap / Status for
that hour, the typical value for that hour across the window, and a plain reading —
*"Short 3 staff this hour. Across the window this hour ran short 9 times out of 14 — a
recurring gap worth fixing in the roster template."* That distinction between a one-off and
a pattern is what changes the decision.

**Seasonality signal** — average required headcount by hour of day, the recurring shape
behind the gap.

Note the window: it uses the most recent period where **both** real demand and real schedule
data exist. Future schedules do not exist yet, which is precisely the gap forecasting fills.

#### Model Performance

**MAPE by model**, as vertical columns, champion in green, on a 0-based auto-scaled axis.

Why MAPE and not accuracy: accuracy values (91.8–94.3%) plotted on a fixed 0–100 axis made
every bar look identical. MAPE sits near zero, so the same differences render as genuinely
different bar heights — the champion is visibly the *shortest* bar.

The table below shows Accuracy, MAPE, MAE, RMSE and Bias per model.

---

### AI Recommendations

Same dashboard treatment as the two analytics pages: filter bar, KPI tiles, a row of
clickable visuals, then the recommendation cards.

#### KPI tiles

**Actions To Take** (accent tile — how many actions the current selection holds), **Do These
First** (the High-priority count), **Sites Affected**, and **Biggest Theme** (the category
generating the most actions). Label and number only, no sparklines: a recommendation set is a
snapshot of the present, not a series over time.

#### The visual grid

| Visual | Type | Click behaviour |
|---|---|---|
| **By Priority** | donut with the action count in the centre | keeps only that priority |
| **By Category** | vertical columns with value labels | keeps only that category |
| **Actions by Site** | vertical columns, busiest highlighted | filters to that site and department |

Cross-filtering follows the same convention as the other pages — a visual never filters
itself, clicking the selected item clears it, non-selected columns dim to 28%.

#### The cards

Sorted **High → Low priority**, each with a left spine coloured by priority. Every card
answers four questions in plain language, then shows its working:

> **Short of staff on Friday 24 Jul morning** — Regional Medical Center / Retail & Cafe
> **What's happening** — About 19 people are needed on shift that morning, but only 11 are rostered — short by 8 people.
> **What to do** — Move 4 people across from Community Hospital / Retail & Cafe Services, then find cover for 4 people.
> **Why it matters** — The shift is fully covered instead of running short by 8 people.
>
> **Why this is flagged**
> - Forecast says 19 people needed on shift.
> - Roster currently has 11 people on.
> - Community Hospital / Retail & Cafe Services has 4 people spare at the same time.
> - Compared over the last 14 days of forecast demand against the published roster.

#### What was removed from the card, and why

**The savings figure (`~$242`) is gone.** For a staffing gap it was
`shortfall × avg_hourly_rate × 1.5`, presented as *"saved"*. That is not a saving — it is a
rough guess at what covering the gap would **cost**, and rostering the missing people spends
that money rather than saving it. A number that confidently wrong is worse than no number.

**The confidence bar is gone, and it was broken.** The API returns `confidence` as an integer
percent (`70`), but the card computed `Math.round(rec.confidence * 100)` — so every card read
**7000%**. Beyond the bug, the values were mostly fixed constants (`70` for staffing, `60`
for cost) rather than a measured quantity, so the bar implied a rigour that was not there.

Both fields are still in the API response, so either can come back — the savings figure would
need relabelling as a cost first.

**Evidence was always empty.** An earlier note in this file claimed the evidence list "was in
the API response all along and was never rendered". That was wrong: the engine set
`"evidence": []` on every rule, so the block never appeared. Each rule now populates it with
the actual figures it fired on, phrased for a manager rather than named after a database
field.

**Overtime actions are capped at the top 3.** Every High-risk employee produced a near
identical card; five in a row pushed the staffing, cost and attendance actions off the page.
The mix is now 4 staffing / 3 overtime / 2 adherence rather than 5 near-duplicates first.

---

### AI Assistant

Chat over the application's own data. Uses OpenAI when `OPENAI_API_KEY` is set in
`.env.sodexo`, and falls back to a template summariser over the same retrieved facts when it
is not — the answers stay grounded either way.

Six deliberate behaviours:

- **On-topic only.** Off-topic questions are declined and redirected *before* any retrieval,
  so out-of-scope questions cost almost nothing.
- **Routed by meaning, with a keyword safety net.** A cheap model call picks which services
  to query, so *"who might quit?"* reaches the risk models even though it contains none of
  their keywords. If that call returns nothing — out of scope, or a transient failure — the
  deterministic keyword router still answers, so a routing hiccup degrades the answer instead
  of breaking it. The response reports which path ran, in `routed_by`.
- **Scoped to what you asked about.** "the cafe" returns the two café series, not all four;
  *"the cafe at Regional Medical"* narrows to exactly one. Employee-level questions scope the
  same way, so *"who is highest risk at the cafe?"* ranks café staff rather than filtering the
  global top five and hoping a café employee is in it.
- **Follow-ups keep their context.** *"why?"*, *"is it over budget?"*, *"who is highest risk
  there?"* inherit the site and subject from the previous turns. A self-contained question
  such as *"which department is over budget?"* deliberately does **not** inherit scope — it is
  asking about all of them.
- **Structured output.** One direct opening sentence carrying the deciding number, then up to
  4 bullets that each carry a real figure with its unit, then a `**Next step:**` line only
  where the facts support a specific action. Under 140 words. The page renders the bold and
  bullets rather than showing one wall of text, and lists the services each answer was built
  from underneath it.
- **Grounded, and checked in code where the prompt is not enough.** Facts go to the model as
  real JSON at `temperature=0`. A "next step" that only says *monitor / continue / maintain*
  is stripped after generation rather than trusted to the instruction, because a closing line
  that recommends nothing is worse than no closing line.

It answers from structured facts pulled from the same services the pages use — never raw
database access, and never numbers it made up.

#### Three routing bugs that were fixed

1. **`is_greeting` used `startswith`.** So "**hi**ghest cost department" was answered with
   the greeting, and anything opening with "help" was too. Greeting detection now requires
   the *whole* message to be pleasantries (or a genuine "what can you do").
2. **Topic keywords were substring matches with no ranking.** Every topic whose keyword
   appeared anywhere was retrieved, so "overtime" alone pulled in both cost and risk. Terms
   are now matched as whole words against a tokenised question, each topic is scored by how
   many terms it hits (a multi-word phrase counting double, since "over budget" is stronger
   evidence than a bare "budget"), and only the topics genuinely competitive with the best
   match run — at most three.
3. **A bare date word routed off-topic questions in.** "tomorrow" was a forecast keyword, so
   *"what's the weather tomorrow?"* retrieved forecast facts. Date words on their own no
   longer trigger a topic.

#### The staffing-gap answer said "staff-hours" when it meant people

`required_headcount` and `scheduled_headcount` are **people on shift**. The recommendation
copy described them as "staff-hours", so a perfectly ordinary 10-vs-20 headcount gap read
as nonsense — *"you need about 20 staff-hours but only 10 are rostered"*. The wording is now
*"Forecast demand for that morning is 20 people on shift against 10 rostered — a shortfall of
10 people"*, and the LLM system prompt carries an explicit rule never to call those fields
hours, alongside one forbidding it from restating any of the three figures as another.

---

## 4. How the forecasting works

### Grain: half-day blocks

Models forecast per **12-hour block** (AM 00:00–11:59, PM 12:00–23:59), not per hour. This
was measured, not assumed:

| Grain | Champions | Weakest model | Spread |
|---|---|---|---|
| Daily (24h) | 92.1–97.7% | 89.0% | 1.6–3.3pt |
| **Half-day (12h)** | **90.5–97.5%** | **85.2%** | **1.4–5.6pt** |
| Shift (8h) | 86.4–95.0% | 77.9% | up to 8.5pt |
| Hourly (1h) | 50.6–82.9% | — | — |

Hour-to-hour variation is dominated by irreducible noise on tiny integer targets — a quarter
of Community Hospital's hours sit at exactly 1 required staff, where being off by one is a
100% error. Half-day is the finest grain at which every series' champion still clears 90%.

Hour-of-day detail is still available throughout Data Analysis, Drift Monitoring and
Staffing Impact, which read the raw hourly facts directly.

### Leak-free exogenous inputs

Census and sales columns are strong demand signals, but the **same-period** value is
contemporaneous with the target — using it would leak the answer. Every such column is used
**lagged by exactly one week**, which equals the forecast horizon, so the lag always resolves
to an already-observed actual for every step of the horizon.

### Validation

3-fold rolling-origin cross-validation with expanding windows and 28-day validation folds.
Auto ARIMA and LSTM use a single chronological holdout instead — and because the holdout is
sized to 3× one fold, they are scored on exactly the same 84 test rows, so the leaderboard
stays comparable.

Champion = lowest MAPE among models that completed with valid metrics.

### The 13 models

SARIMAX · SARIMAX+exog · Auto ARIMA · Auto ARIMA+exog · XGBoost · XGBoost+exog ·
Exponential Smoothing (additive, additive-damped, multiplicative, multiplicative-damped) ·
VAR · VAR+exog · LSTM

---

## 5. What this build does *not* do

Read this section before presenting the numbers.

### The models barely beat a flat average on two of four series

Champion accuracy against simply predicting the training average:

| Series | Champion | Flat average | Real gain |
|---|---|---|---|
| Community Hospital / Food & Nutrition | 94.25% | 93.87% | **+0.38pt** |
| Regional Medical Center / Food & Nutrition | 97.67% | 97.30% | **+0.37pt** |
| Community Hospital / Café | 92.11% | 87.54% | **+4.57pt** |
| Regional Medical Center / Café | 96.63% | 88.21% | **+8.42pt** |

The **café** models earn their keep. The **Food & Nutrition** models add almost nothing — their
94–98% accuracy comes from the series being stable, not from the model being skilful. For
Community Hospital / Food & Nutrition the champion captures only 43% of the actual variation
(predicted std 2.02 vs actual 4.68) and correlates 0.387 with the actuals. That is the flat
orange line visible in the Forecast Explorer.

A high accuracy percentage on a stable series is not evidence of a good model. Quote these
numbers with that context.

### The demand signal is weak for Food & Nutrition

Correlation between the demand signal and required staffing, at the model's grain:

| Series | Signal | Correlation |
|---|---|---|
| Community Hospital / Food & Nutrition | occupied_beds | **0.34** (weak) |
| Regional Medical Center / Food & Nutrition | occupied_beds | 0.61 (moderate) |
| Community Hospital / Café | transaction_count | 0.95 (strong) |
| Regional Medical Center / Café | transaction_count | 0.99 (strong) |

Kitchen staffing is set by fixed meal-service windows, not by how full the wards are in a
given block. Bed count drives the day's meal *volume*; it does not drive staff *timing*.

### Not available in this dataset

- **No region dimension** — cost groups by location, department and cost centre only.
- **No hourly labor cost / attendance** — those come from daily timecards.
- **No weather data** anywhere in the source.
- **Promotion and event exist only as booleans** — no campaign name, spend or channel.
- **Employee availability** is an explicit "not available" stub in the mock API.
- **Turnover risk is not modelled** — there is no termination or tenure-end data, so it is
  reported as unsupported rather than faked.

### Deliberately simple, and disclosed as such

- **Drift detection** is a percentage-change threshold, not a statistical test.
- **The punch watchlist** is a trend rule, not a trained classifier.
- **Recommendations** are rule-based. No optimiser.
- **Scorecard anchors and targets** are POC defaults set in this app, not Sodexo policy.

### Synthetic data

All data is generated by the mock API. Patterns are realistic in shape — weekly cycles,
meal-time peaks, weekend dips — but this is not real Sodexo operational data, and accuracy
figures on real data would differ.

---

## 6. Actual output, page by page

Every block below is **real output captured from this application** against the ingested
database, followed by an explanation of what it means. Nothing here is illustrative.

> **One caveat on the forecasting figures.** The stored leaderboard comes from training job
> `JOB-4ea5d6d60b06`, which ran **before** the switch to half-day grain — it has 365 training
> rows (daily), whereas the engine is now configured for 364 half-day rows. Those accuracy
> numbers are therefore *daily-grain* results. Re-run **Train All Models** to refresh them.
> Separately measured half-day results are in [section 4](#4-how-the-forecasting-works).

---

### 6.1 Data Quality — ingestion

```
sources=160  loaded=839,890  rejected=0

ERP        /mock/erp/locations            COMPLETED  recv=2    loaded=2    rej=0
ERP        /mock/erp/departments          COMPLETED  recv=2    loaded=2    rej=0
Workday    /mock/workday/jobs             COMPLETED  recv=4    loaded=4    rej=0
Workday    /mock/workday/job-skills       COMPLETED  recv=12   loaded=12   rej=0
ERP        /mock/erp/staffing-standards   COMPLETED  recv=2    loaded=2    rej=0
Workday    /mock/workday/workers          COMPLETED  recv=150  loaded=150  rej=0
```

**Reading it:** 839,890 records loaded with **zero rejects** — every row the source offered
was accepted, so nothing silently fell out of the pipeline. `recv` equalling `loaded` on
every line is the check that matters. 160 batch rows means ingestion has been run ten times
over 16 endpoints; the pipeline is idempotent, so repeat syncs do not duplicate facts.

---

### 6.2 Labor Forecasting

#### Demand Drivers & Feature Mapping

```
target: required_manpower

Food & Nutrition (11 inputs)
  is_holiday, demand_multiplier, season_Spring, season_Summer, season_Fall, season_Winter,
  occupied_beds, admissions_count, discharges_count, transfers_count, meals_served

Retail & Cafe (10 inputs)
  is_holiday, demand_multiplier, season_Spring, season_Summer, season_Fall, season_Winter,
  promotion_flag, event_flag, sales_amount, transaction_count

not in this dataset
  Weather · Marketing campaign detail · Local event detail · Employee availability
```

**Reading it:** census columns are shown by plain name but fed to the models **lagged one
week** — the same-period value would leak the answer. The "not in this dataset" list is
deliberate: it states what the forecast *cannot* see, so nobody assumes weather is factored in.

#### Data Analysis

```
daily trend      90 points   first {2026-05-03, 2.62}   last {2026-07-31, 2.58, 7d-avg 2.46}
by hour of day   peak 07:00 (6.86)     quietest 01:00 (2.0)
by day of week   Mon 2.71  Tue 2.71  Wed 2.73  Thu 2.73  Fri 2.72  Sat 2.64  Sun 2.66
by season        Winter 2.90   Spring 2.73   Fall 2.69   Summer 2.49
holiday lift     -7.4%
cafe flags       promotion +14.0%    event +13.0%
```

**Reading it:** the hour-of-day peak of **6.86 at 07:00 against 2.0 overnight is a 3.4x
swing** — breakfast service. That intraday spike is exactly why hourly forecasting is hard and
why the engine works at half-day blocks. Day-of-week barely moves (2.64 to 2.73), so weekday
effects are weak *for staffing*. Winter is 16% busier than Summer. Holidays *reduce* demand by
7.4%. Cafe promotions genuinely lift demand **+14%**, which is why promotion_flag is a
worthwhile input there.

#### Data Pre-processing

```
total_history_days        730
training_window_days      182     (364 half-day rows)
resolved_seasonal_period   14 slots  -> Weekly
validation                rolling_origin, 3 folds, 22-day validation window each
input_dataset             364 rows x 13 columns
forecast_horizon            7 days
block_hours                12

preview row 1
  date 2026-01-31 00:00   is_holiday 0   demand_multiplier 1.0   season_Winter 1
  occupied_beds 1248.2   admissions 4   discharges 4   transfers 1   meals_served 126
  required_manpower 33.0
```

**Reading it:** 730 days available, 182 used. The engine picked a **weekly** cycle (14
half-day blocks) over a daily one. `required_manpower` sits **last** in the preview because it
is the target, not an input. `occupied_beds 1248.2` is a 12-hour sum, not a headcount — the
per-hour figure is around 85–87.

#### Model Training — leaderboard (Community Hospital / Food & Nutrition)

```
XGBoost with exogenous variables        94.25%  mape 5.75  mae 3.43  rmse 4.33  bias  0.77   0.53s  CHAMPION
SARIMAX with exogenous variables        94.18%  mape 5.82  mae 3.50  rmse 4.35  bias -0.11   2.34s
VAR with exogenous variables            94.18%  mape 5.82  mae 3.50  rmse 4.38  bias -0.33   0.04s
Auto ARIMA with exogenous variables     94.15%  mape 5.85  mae 3.51  rmse 4.42  bias  0.07  10.48s
Exp Smoothing Additive Damped           93.86%  mape 6.14  mae 3.71  rmse 4.56  bias -0.70   0.30s
Exp Smoothing Multiplicative Damped     93.84%  mape 6.16  mae 3.73  rmse 4.57  bias -0.92   0.28s
Exp Smoothing Additive                  93.76%  mape 6.24  mae 3.80  rmse 4.64  bias -1.53   0.29s
Exp Smoothing Multiplicative            93.76%  mape 6.24  mae 3.80  rmse 4.63  bias -1.54   0.26s
VAR                                     93.62%  mape 6.38  mae 3.75  rmse 4.74  bias  2.62   0.01s
SARIMAX                                 93.51%  mape 6.49  mae 3.83  rmse 4.78  bias  2.47   0.49s
XGBoost                                 92.70%  mape 7.30  mae 4.26  rmse 5.29  bias  4.55   0.39s
LSTM                                    92.69%  mape 7.31  mae 4.25  rmse 5.41  bias  4.47   9.51s
Auto ARIMA                              91.81%  mape 8.19  mae 4.75  rmse 5.89  bias  5.88   2.84s

13/13 completed    train/test rows 337/84   (281/84 for the holdout-evaluated models)
```

Champions across all four series:

| Series | Champion | Accuracy | Range across 13 models |
|---|---|---|---|
| CH / Food & Nutrition | XGBoost + exog | 94.25% | 91.81 – 94.25 |
| RMC / Food & Nutrition | VAR + exog | 97.67% | 96.45 – 97.67 |
| CH / Cafe | XGBoost + exog | 92.11% | 90.53 – 92.11 |
| RMC / Cafe | XGBoost + exog | 96.63% | 93.30 – 96.63 |

**Reading it:** **all 52 combinations completed** — nothing failed or was skipped. Every
champion clears 90%.

Two patterns worth noting. First, the **top four models are all `+exog` variants**, so the
census and sales drivers genuinely help. Second, **look at the bias column** — the leaders sit
at −0.11 to +0.77 (near-unbiased) while the bottom four run +2.47 to +5.88, meaning they
systematically *over*-forecast. Ranking by MAPE happened to pick a low-bias model too.

**Runtimes span 0.01s to 61s.** VAR is effectively instant; Auto ARIMA on the cafe took 61s.
The whole run was **5m 09s for 52 model runs**.

`train_rows` differs by evaluation mode — 337 for the ten CV-evaluated models, 281 for Auto
ARIMA and LSTM on holdout — but **`test_rows` is 84 for every single one**, which is what
makes the comparison fair.

#### Forecast Explorer

```
model        XGBoost with exogenous variables
evaluation   rolling_origin, 3 folds    train 337 / test 84
chart data   84 backtest points    7 future points
boundary     last actual 2026-07-31 00:00  ->  forecast 2026-08-01 to 2026-08-07

future    2026-08-01   56.4   (50.1 to 61.0)
          2026-08-02   59.2   (52.9 to 63.8)
          2026-08-03   60.9   (54.6 to 65.5)
          2026-08-04   57.3   (51.0 to 61.9)
```

**Reading it:** the grey line covers full history, orange covers only the 84 validated points,
green covers 7 future days. The bracketed range is an **80% prediction interval built from
actual backtest residuals** — not a theoretical assumption — so the roughly plus-or-minus 5
staff-hours reflects how wrong this model really was.

Compare the forecast spread (56.4 to 60.9) against the actual spread over the same backtest
window (49 to 74). The forecast is **much narrower than reality** — see
[section 5](#5-what-this-build-does-not-do).

#### Drift Monitoring

```
LOC01_FOOD_NUTRITION  Stable  occupied_beds      r=0.34 (weak)      recent 85.22   prior 85.20    0.0%   age 0d
LOC02_FOOD_NUTRITION  Stable  occupied_beds      r=0.61 (moderate)  recent 249.09  prior 248.99   0.0%   age 0d
LOC01_CAFE            Stable  transaction_count  r=0.95 (strong)    recent 15.59   prior 15.70   -0.7%   age 0d
LOC02_CAFE            Stable  transaction_count  r=0.99 (strong)    recent 44.89   prior 44.33   +1.3%   age 0d
```

**Reading it:** all four are **Stable** — every change is within 1.3%, far inside the 15%
threshold, so the two dashed average lines sit on top of each other on the chart. `age 0d`
means the models were trained today.

The `r` column is the honest part: **occupied_beds correlates only 0.34** with staffing at
Community Hospital, so a shift in it would barely move that forecast. The cafe signals at 0.95
and 0.99 genuinely drive their forecasts. The weak-signal warning banner appears only for the
two Food & Nutrition series.

#### Staffing Impact

```
LOC01_FOOD_NUTRITION   336 pts   understaffed 103   overstaffed 160   balanced  73
LOC02_FOOD_NUTRITION   336 pts   understaffed  97   overstaffed 232   balanced   7
LOC01_CAFE             196 pts   understaffed  42   overstaffed  68   balanced  86
window 2026-07-18 to 2026-07-31
```

**Reading it:** the striking number is **LOC02 Food & Nutrition: only 7 of 336 hours balanced,
with 232 overstaffed** — that site is persistently over-rostered, which lines up with it being
28.6% under budget on cheap hours. LOC01 Cafe is the healthiest at 86 of 196 balanced. The
cafe has 196 points rather than 336 because it is not open 24 hours.

Understaffed and overstaffed hours in the *same* window is the useful insight — it is not a
headcount problem, it is a **distribution** problem, which is exactly what clicking through to
the drill-down exposes.

---

### 6.3 AI Recommendations

```
11 recommendations    Staffing Adjustment 4    Overtime Reduction 5    Schedule Adherence 2

[High] Short of staff on Monday 20 Jul morning
       Regional Medical Center / Food & Nutrition Services
  What is happening   About 20 people are needed on shift that morning, but only 10 are
                      rostered - short by 10 people
                      - a shortfall of 10 hours.
  What to do          Roster 10 more people. No other site has spare staff then, so
                      this needs extra cover.
  Why it matters      Covers the 10-hour gap so the shift is fully staffed.
  ~$242 saved         what you would otherwise pay in last-minute overtime cover

[High] Worker0090 is heading into overtime
       Regional Medical Center A / Food & Nutrition Services
  What is happening   Worker0090 has already worked 6.3 overtime hours, and their recent
                      pattern makes more overtime very likely (98% chance).
  What to do          Hand some of their upcoming shifts to a colleague with spare capacity.
  Why it matters      Avoids up to 6.3 hours paid at the overtime rate.
  ~$44 saved          the extra premium above their normal hourly rate
```

**Reading it:** each card answers three questions in order. Every savings figure states its
own basis — $242 is avoided overtime *cover*, $44 is the overtime *premium* over base rate.
They are computed (hours x rate x multiplier), not invented.

Note the first card says *"No other site has spare people then"* — the engine checked the
paired site for surplus at that exact hour before recommending added cover rather than a
transfer.

---

### 6.4 AI Assistant

One real conversation, three turns, nothing re-stated by the user:

```
Q: what is the demand trend for the cafe at Community Hospital
A: The demand trend for the cafe at Community Hospital is flat with a -3.5% change.
   - Average Recent Demand - 60.58 people/hour.
   - Forecast for Next 7 Days - 58.44 people/hour.
   - Staffing Gap - 0 people, with a required headcount of 3 and scheduled headcount of 3.
   [tools: forecast]

Q: is it over budget
A: No, the labor cost at Community Hospital is not over budget; it is 26% under budget.
   - Total Labor Cost - $711,238.93.
   - Total Budget - $960,697.00.
   - Department Labor Cost (Retail & Cafe Services) - $80,477.91, which is 23.4% under budget.
   [tools: labor_cost]

Q: who is highest risk there
A: The highest risk employees at Community Hospital in the Retail & Cafe Services department are:
   - Worker0025 - 96% risk, High level.
   - Worker0026 - 90% risk, High level.
   - Worker0038 - 88% risk, High level.
   [tools: risk]

Q: what is the weather today
A: That one is outside what I can see - I only read this application's own workforce data.
```

**Reading it:** turn 1 scoped to **one** series out of four, because it named both a
department and a site. Turn 2 said only *"it"* — the site came from the conversation, and the
question was routed to cost without sharing a single keyword with turn 1. Turn 3 said only
*"there"*, and returned **café staff at Community Hospital** rather than the highest-risk
employees company-wide (who are at Regional Medical Center) — employee rows are filtered to
the scope before ranking, so the right people are in the answer by construction rather than by
luck. The weather question was **declined before any retrieval**, so out-of-scope questions
cost almost nothing.
