# Labor Forecasting & Workforce Intelligence

A working proof of concept that takes **a data file you upload**, forecasts
it with a 13-model portfolio, and turns the result into decisions a manager
can act on.

There is no built-in dataset. Every series comes from a file the user
uploads; the application reads its columns, proposes what each one means,
and forecasts whatever they confirm. Everything on screen is computed from
that file at request time - no hardcoded numbers, no mocked charts, no
invented AI answers.

**All data is wiped on every restart.** This is a single-tenant POC handed
to one client at a time, and a page still holding the previous session's
numbers reads as the client's own data until someone checks the series name.

---

## Documentation

The client-facing guides live **outside this repository**, in
`D:\demo\Sodexo\docs` - they are deliverables rather than source, and
regenerating them should not show up as a diff in a code checkout.

| | |
|---|---|
| `Navigation-Guide.pdf` | How to run it, and what every screen is for |
| `Input-Data-Template.pdf` | What to put in the file you upload |

Both are generated from the Markdown beside them by `build_pdfs.py` in the
same folder, so the Markdown stays the single source.

A working example file is available in the app itself - **Data Quality →
Upload Data → Download example file** - or from `GET /api/datasets/template`.
It is generated from the application's own constants, so it cannot fall out
of step with what the code accepts.

---

## 1. How to run it

Two processes.

**Backend** (uploads, forecasting engine, recommendations, assistant):

```bash
cd backend_sodexo && ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8300 --reload
```

**Frontend**:

```bash
cd frontend_sodexo && npm run dev
```

Then open **http://localhost:5183**. Use `localhost`, not `127.0.0.1` -
Vite binds IPv6 loopback.

**First time only:**

```bash
python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r backend_sodexo/requirements.txt
```

```bash
cd frontend_sodexo && npm install
```

### Configuration

Copy [`.env.example`](.env.example) to `.env.sodexo`. The two settings that
matter most:

| Setting | Effect |
|---|---|
| `RESET_ON_STARTUP` | Default **true** - wipes all data on startup. Set `false` for local development. |
| `OPENAI_API_KEY` | Enables AI column mapping and the AI assistant. Both degrade gracefully without it. |

### Signing in

| Username  | Password        | Role               |
| --------- | --------------- | ------------------ |
| `admin`   | `Admin@2026`    | Administrator      |
| `manager` | `Manager@2026`  | Operations Manager |
| `analyst` | `Analyst@2026`  | Data Analyst       |

Roles are shown in the header but gate nothing yet. Override the passwords
and `AUTH_SECRET_KEY` in `.env.sodexo` for any non-local deployment.

---

## 2. How a file becomes a forecast

1. **Upload.** Any CSV or Excel file, up to 50MB / 500,000 rows.
2. **Mapping.** The column names, types and a few sample values go to an
   LLM, which returns which column is the date, which is the target, which
   splits the file into series, which records planned capacity, and which
   are drivers. Every answer is validated against the real columns before
   it is offered; anything that fails falls back to detection from names
   and types. The user confirms before anything is trained.
3. **Train.** The full 13-model portfolio per series, with rolling-origin
   cross-validation and champion-by-MAPE selection.
4. **Read.** Eight forecasting tabs, recommendations and a chat assistant,
   all reading the same trained results.

---

## 3. How the forecasting works

### Grain comes from the data

Each dataset resolves its own grain, seasonality, fold sizes and horizon
from the frequency the user confirmed:

| Frequency | Seasonal cycle tested | Default horizon |
|---|---|---|
| Hourly | daily (24) and weekly (168) | 48 periods |
| Daily | weekly (7) | 14 periods |
| Weekly | yearly (52) | 8 periods |
| Monthly | yearly (12) | 6 periods |

XGBoost's lag and rolling windows, and the LSTM sequence length, are
expressed in that series' own periods rather than a global constant.

### Leak-free inputs

Driver columns are used only in already-observed form. A same-period driver
value is contemporaneous with the target and would leak the answer, so
anything that would not be known at forecast time is lagged.

The **planned-capacity column is deliberately not a driver**. A roster is
built in response to forecast demand, so feeding it to the models lets them
predict demand from the plan made for it.

A driver that is flat once the data is aggregated is dropped automatically.
It carries no information, and it can make the exogenous matrix
rank-deficient for the models that take one.

### Validation

3-fold rolling-origin cross-validation with expanding windows. Auto ARIMA
and LSTM use a single chronological holdout instead - and because the
holdout is sized to 3x one fold, they are scored on the same test rows as
every CV-evaluated model, so the leaderboard stays comparable.

Champion = lowest MAPE among models that completed with valid metrics.

### The 13 models

SARIMAX · SARIMAX+exog · Auto ARIMA · Auto ARIMA+exog · XGBoost ·
XGBoost+exog · Exponential Smoothing (additive, additive-damped,
multiplicative, multiplicative-damped) · VAR · VAR+exog · LSTM

All 13 run by default. A model that cannot run for a given series is
reported as `not_applicable` **with its reason**, never dropped silently.

`TRAINING_PROFILE=fast` skips LSTM and both Auto ARIMA variants, which
together measured at 88% of training time. Worth it only when a large
multi-series upload would otherwise take hours.

### Performance

Series are fitted in parallel. The worker count comes from the container's
**cgroup CPU quota and memory limit**, not `os.cpu_count()` - inside a
container the latter reports the host's cores, so on a 2 vCPU deployment it
would start far too many workers and run slower than serial.

SQLite runs in WAL mode so the status endpoint keeps answering while
training writes.

---

## 4. What this build does *not* do

Read this before presenting the numbers.

### A high accuracy figure is not automatically a good model

On a stable series, predicting the training average scores well. Always
compare the backtest line against the actual in the Forecast Explorer
overlap: if the prediction is flatter than reality - hugging the middle
while the actual swings - the model is tracking the average, not the
pattern, and the accuracy number is coming from the series being stable.

### Deliberately simple, and disclosed as such

- **Drift detection** is a percentage-change threshold, not a statistical test.
- **Recommendations** are rule-based. No optimiser.
- **Column mapping** is AI-proposed, code-validated and user-confirmed - it
  is not guaranteed correct, which is why the user confirms it.

### Not available

- **No cost or savings figures.** An uploaded file has no pay rates, and a
  guessed number is worse than none.
- **No employee-level analysis.** Overtime, absenteeism and schedule
  adherence need per-worker punch and timecard data that a forecasting file
  does not carry.
- **Staffing Impact and gap recommendations need a planned-capacity
  column.** Without one they say so rather than inventing a second curve.
- **Roles do not gate features.** All three logins see the same thing.
- **One timestamp column only.** A file with separate date and hour columns
  is forecast at the date's grain; combine them for hourly.
