import { useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { LoadingState, ErrorState, EmptyState } from '../layout/PageContainer'
import { Card, Badge, KpiCard } from '../ui/Card'
import {
  configureDataset,
  fetchDataset,
  fetchStaffingImpact,
  type Dataset,
  type StaffingImpactPoint,
  type StaffingImpactResponse,
} from '../../services/api'

/** What the periods ahead need, and what that means for the roster.
 *
 *  This page used to do exactly one thing - set a recorded roster against a
 *  recorded requirement - and refuse to render at all without a roster column,
 *  which most exports do not have. A client who had just trained a forecast
 *  was shown an empty page and a request for a column.
 *
 *  So it now answers the question in whatever depth the file supports:
 *    - every file gets the requirement ahead, how that compares with what this
 *      series has actually been running, and a roster figure that absorbs the
 *      model's own measured error;
 *    - a file that also records what was planned gets the gap per period on
 *      top of that, which is the strongest version of the page.
 *
 *  Nothing here is invented client-side: every figure is a field the server
 *  computed from the client's own file and their own trained champion.
 */

const STATUS_BADGE: Record<string, string> = { Understaffed: 'failed', Balanced: 'completed', Overstaffed: 'RUNNING' }
const CHART_TICK = { fontSize: 10, fill: 'var(--color-text-muted)' }

const REQUIRED_COLOR = '#c2410c'
const BASELINE_COLOR = '#9ca3af'
const RECOMMENDED_COLOR = '#0f766e'
const PLANNED_COLOR = '#16181d'

function num(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function signed(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${num(value, digits)}`
}

/** Dates are shown at the grain the file actually has - printing 00:00 beside
 *  every row of a daily file suggests an hour that was never recorded. */
function periodLabel(iso: string, hourly: boolean): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso.slice(0, 16).replace('T', ' ')
  const day = date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
  return hourly ? `${day} ${String(date.getHours()).padStart(2, '0')}:00` : day
}

function shortLabel(iso: string, hourly: boolean): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso.slice(5, 10)
  const day = date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  return hourly ? `${day} ${String(date.getHours()).padStart(2, '0')}h` : day
}

export function StaffingImpactTab({ seriesKey, seriesLoading }: { seriesKey: string; seriesLoading?: boolean }) {
  const [data, setData] = useState<StaffingImpactResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!seriesKey) return
    let stale = false
    setData(null)
    setError(null)
    fetchStaffingImpact(seriesKey)
      .then((d) => !stale && setData(d))
      .catch((err) => !stale && setError(err?.response?.data?.detail ?? String(err)))
    return () => {
      stale = true
    }
  }, [seriesKey])

  // Three states, not two. An empty picker is not the same as a request in
  // flight, and folding them together left this page on "Loading data..."
  // forever whenever there was no series to pick.
  if (seriesLoading) return <LoadingState />
  if (!seriesKey) return <EmptyState message="No trained models yet. Run Model Training, then this page has something to show." />
  if (error) return <ErrorState message={error} />
  if (!data) return <LoadingState />
  if (!data.points.length) return <Card><p className="text-xs text-[var(--color-text-muted)]">{data.note}</p></Card>

  return <Impact key={data.series_key} data={data} />
}

function Impact({ data }: { data: StaffingImpactResponse }) {
  const [showHistory, setShowHistory] = useState(false)

  const hourly = data.period_label === 'Hourly'
  const unit = data.period_unit ?? 'period'
  const forecast = useMemo(() => data.points.filter((p) => p.is_forecast), [data.points])
  const history = useMemo(() => data.points.filter((p) => !p.is_forecast), [data.points])
  const outlook = data.outlook
  const planned = data.mode === 'plan'
  const rows = forecast.length ? forecast : history.slice(-14)

  // History is context, not the point - enough of it to see where the
  // forecast picks up from, and no more.
  const chartData = [...history.slice(hourly ? -48 : -21), ...forecast].map((p) => ({
    label: shortLabel(p.date, hourly),
    required: p.required_headcount,
    baseline: p.baseline,
    recommended: p.recommended_headcount,
    planned: p.scheduled_headcount,
  }))
  const forecastStart = forecast.length ? shortLabel(forecast[0].date, hourly) : null

  return (
    <div className="flex flex-col gap-5">
      <div className="px-1">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">
          {forecast.length
            ? `Staffing outlook - the next ${forecast.length} ${unit}${forecast.length === 1 ? '' : 's'}`
            : `Staffing on record - the last ${history.length} ${unit}${history.length === 1 ? '' : 's'}`}
        </h2>
        <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">{data.note}</p>
      </div>

      {outlook && forecast.length > 0 ? (
        <OutlookKpis outlook={outlook} planned={planned} unit={unit} hourly={hourly} />
      ) : (
        <HistoryKpis history={history} unit={unit} hourly={hourly} />
      )}

      {planned && outlook && outlook.short_periods > 0 && outlook.largest_shortfall && (
        <Card className="border-l-4 border-l-[var(--color-danger)]">
          <p className="text-xs text-[var(--color-text)]">
            <span className="font-semibold">
              {outlook.short_periods} of {outlook.planned_periods} planned {unit}s fall short
            </span>{' '}
            - {num(outlook.total_shortfall)} of cover to find, worst on{' '}
            {periodLabel(outlook.largest_shortfall.date, hourly)} at {num(Math.abs(outlook.largest_shortfall.gap))}.
          </p>
        </Card>
      )}

      <Card>
        <h3 className="mb-3 text-xs font-semibold text-[var(--color-text)]">
          Requirement against {planned ? 'the plan' : 'the recent norm'}
        </h3>
        <ResponsiveContainer width="100%" height={320}>
          <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
            <XAxis dataKey="label" tick={CHART_TICK} minTickGap={24} />
            <YAxis tick={CHART_TICK} width={44} />
            <Tooltip contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {forecastStart && (
              <ReferenceLine
                x={forecastStart}
                stroke={REQUIRED_COLOR}
                strokeDasharray="4 3"
                label={{ value: 'forecast from here', position: 'insideTopRight', fontSize: 10, fill: REQUIRED_COLOR }}
              />
            )}
            <Line type="monotone" dataKey="baseline" name="Recent norm" stroke={BASELINE_COLOR} strokeWidth={1.5} strokeDasharray="2 3" dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="required" name="Required" stroke={REQUIRED_COLOR} strokeWidth={2.5} dot={false} isAnimationActive={false} />
            {planned
              ? <Line type="monotone" dataKey="planned" name="Planned" stroke={PLANNED_COLOR} strokeWidth={1.8} strokeDasharray="5 3" dot={false} connectNulls={false} isAnimationActive={false} />
              : <Line type="monotone" dataKey="recommended" name="Recommended roster" stroke={RECOMMENDED_COLOR} strokeWidth={1.8} strokeDasharray="5 3" dot={false} connectNulls={false} isAnimationActive={false} />}
          </ComposedChart>
        </ResponsiveContainer>
      </Card>

      <Card>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold text-[var(--color-text)]">
            {forecast.length
              ? `${unit.charAt(0).toUpperCase()}${unit.slice(1)} by ${unit} - what to staff`
              : 'What was required'}
          </h3>
          {history.length > 0 && forecast.length > 0 && (
            <button
              onClick={() => setShowHistory((v) => !v)}
              className="rounded-lg border border-[var(--color-border)] px-2.5 py-1 text-[11px] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
            >
              {showHistory ? 'Hide' : 'Show'} the {history.length} {unit}s on record
            </button>
          )}
        </div>
        <ImpactTable
          rows={showHistory ? [...history, ...forecast] : rows}
          planned={planned}
          hourly={hourly}
          plannedLabel={data.scheduled_label ?? null}
        />
      </Card>

      {data.needs_capacity_column && <AddPlanFootnote seriesKey={data.series_key} />}
    </div>
  )
}

function OutlookKpis({
  outlook, planned, unit, hourly,
}: {
  outlook: NonNullable<StaffingImpactResponse['outlook']>
  planned: boolean
  unit: string
  hourly: boolean
}) {
  const range =
    outlook.start && outlook.end
      ? `${periodLabel(outlook.start, hourly)} to ${periodLabel(outlook.end, hourly)}`
      : `${outlook.periods} ${unit}s ahead`
  const change = outlook.change_pct
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      <KpiCard label="Required ahead" value={num(outlook.total_required)} sublabel={range} />
      <KpiCard
        label="Against recent norm"
        value={change == null ? '—' : `${signed(change)}%`}
        sublabel={`compared with ${outlook.baseline_basis}`}
        tone={change == null ? 'default' : change > 5 ? 'warn' : change < -5 ? 'good' : 'default'}
      />
      {planned ? (
        <>
          <KpiCard
            label="Cover to find"
            value={num(outlook.total_shortfall)}
            sublabel={`across ${outlook.short_periods} of ${outlook.planned_periods} planned ${unit}s`}
            tone={outlook.total_shortfall > 0 ? 'bad' : 'good'}
          />
          <KpiCard
            label="Surplus periods"
            value={String(outlook.surplus_periods)}
            sublabel="planned well above requirement"
            tone={outlook.surplus_periods > 0 ? 'warn' : 'default'}
          />
        </>
      ) : (
        <>
          <KpiCard
            label="Busiest period"
            value={outlook.peak ? num(outlook.peak.required) : '—'}
            sublabel={outlook.peak ? periodLabel(outlook.peak.date, hourly) : 'Not available'}
            tone="warn"
          />
          <KpiCard
            label="Roster to"
            value={num(outlook.recommended_total)}
            sublabel={
              outlook.buffer.value == null
                ? 'requirement as forecast'
                : `includes ${num(outlook.buffer.value, 2)} cover per ${unit}`
            }
          />
        </>
      )}
    </div>
  )
}

/** Nothing trained yet, so there is no outlook - but the file still says what
 *  this series has been running, which is worth showing rather than leaving a
 *  row of dashes above an instruction to go and train. */
function HistoryKpis({ history, unit, hourly }: { history: StaffingImpactPoint[]; unit: string; hourly: boolean }) {
  if (!history.length) return null
  const values = history.map((p) => p.required_headcount)
  const mean = values.reduce((a, b) => a + b, 0) / values.length
  const peak = history.reduce((a, b) => (b.required_headcount > a.required_headcount ? b : a))
  const quiet = history.reduce((a, b) => (b.required_headcount < a.required_headcount ? b : a))
  const cap = (word: string) => `${word.charAt(0).toUpperCase()}${word.slice(1)}`
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      <KpiCard label={`${cap(unit)}s on record`} value={String(history.length)} sublabel="in the window below" />
      <KpiCard label="Typical requirement" value={num(mean)} sublabel={`average per ${unit}`} />
      <KpiCard label="Busiest" value={num(peak.required_headcount)} sublabel={periodLabel(peak.date, hourly)} tone="warn" />
      <KpiCard label="Quietest" value={num(quiet.required_headcount)} sublabel={periodLabel(quiet.date, hourly)} />
    </div>
  )
}

function ImpactTable({
  rows, planned, hourly, plannedLabel,
}: {
  rows: StaffingImpactPoint[]
  planned: boolean
  hourly: boolean
  plannedLabel: string | null
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="text-[var(--color-text-muted)]">
            <th className="pb-2 font-medium">Period</th>
            <th className="pb-2 text-right font-medium">Required</th>
            <th className="pb-2 text-right font-medium">Recent norm</th>
            <th className="pb-2 text-right font-medium">vs norm</th>
            {planned ? (
              <>
                <th className="pb-2 text-right font-medium">{plannedLabel ?? 'Planned'}</th>
                <th className="pb-2 pr-6 text-right font-medium">Gap</th>
                <th className="pb-2 font-medium">Status</th>
              </>
            ) : (
              <th className="pb-2 text-right font-medium">Roster to</th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr
              key={p.date}
              className={`border-t border-[var(--color-border)] ${p.is_forecast ? 'bg-[var(--color-primary)]/5' : ''}`}
            >
              <td className="py-2 text-[var(--color-text)]">
                {periodLabel(p.date, hourly)}
                {p.is_forecast && (
                  <span className="ml-2 rounded-full bg-[var(--color-primary)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--color-primary)]">
                    ahead
                  </span>
                )}
              </td>
              <td className="py-2 text-right font-medium text-[var(--color-text)]">{num(p.required_headcount)}</td>
              <td className="py-2 text-right text-[var(--color-text-muted)]">{num(p.baseline)}</td>
              <td
                className={`py-2 text-right ${
                  p.vs_baseline_pct == null
                    ? 'text-[var(--color-text-muted)]'
                    : p.vs_baseline_pct > 5
                      ? 'text-[#a16207]'
                      : p.vs_baseline_pct < -5
                        ? 'text-[#15803d]'
                        : 'text-[var(--color-text-muted)]'
                }`}
              >
                {p.vs_baseline_pct == null ? '—' : `${signed(p.vs_baseline_pct)}%`}
              </td>
              {planned ? (
                <>
                  <td className="py-2 text-right text-[var(--color-text)]">
                    {p.scheduled_headcount == null
                      ? <span className="text-[var(--color-text-muted)]">not planned yet</span>
                      : num(p.scheduled_headcount)}
                  </td>
                  <td
                    className={`py-2 pr-6 text-right font-medium ${
                      p.gap == null ? 'text-[var(--color-text-muted)]' : p.gap < 0 ? 'text-[#b91c1c]' : 'text-[#15803d]'
                    }`}
                  >
                    {p.gap == null ? '—' : signed(p.gap)}
                  </td>
                  <td className="py-2">
                    {p.status
                      ? <Badge status={STATUS_BADGE[p.status] ?? 'not_applicable'}>{p.status}</Badge>
                      : <span className="text-[11px] text-[var(--color-text-muted)]">no plan</span>}
                  </td>
                </>
              ) : (
                <td className="py-2 text-right">
                  {p.recommended_headcount == null
                    ? <span className="text-[var(--color-text-muted)]">—</span>
                    : <span className="font-medium text-[#0f766e]">{num(p.recommended_headcount)}</span>}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The file records no plan, so the gap columns are missing - said once, at
 *  the bottom, as an upgrade rather than as a blocker. The picker only appears
 *  when the file genuinely carries a candidate column; an empty dropdown is
 *  worse than none, which is how this page read before.
 */
function AddPlanFootnote({ seriesKey }: { seriesKey: string }) {
  const [dataset, setDataset] = useState<Dataset | null>(null)
  const [choice, setChoice] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)

  const datasetId = seriesKey.split('__', 1)[0]
  useEffect(() => {
    let stale = false
    fetchDataset(datasetId)
      .then((d) => !stale && setDataset(d))
      .catch(() => !stale && setDataset(null))
    return () => {
      stale = true
    }
  }, [datasetId])

  // A roster is a planned measure and its name nearly always says so. Only
  // those are offered: letting any numeric column through is how `dayofweek`
  // and `admissions_count` ended up being compared against person-hours.
  const PLANNED = /(roster|schedul|planned|budget|allocat|staff|capacity|available|fte|headcount|shift|manpower)/i
  const candidates = (dataset?.column_profile ?? []).filter(
    (c) =>
      c.is_numeric &&
      c.name !== dataset?.target_column &&
      c.name !== dataset?.date_column &&
      PLANNED.test(c.name),
  )

  const save = async () => {
    if (!dataset || !choice) return
    setBusy(true)
    setFailed(null)
    try {
      await configureDataset(dataset.dataset_id, {
        name: dataset.name,
        date_column: dataset.date_column ?? '',
        target_column: dataset.target_column ?? '',
        group_columns: dataset.group_columns,
        capacity_column: choice,
        frequency: dataset.frequency ?? 'D',
        aggregation: dataset.aggregation,
        horizon_periods: dataset.horizon_periods,
        // The chosen column must not also be a model input - a roster is built
        // in response to demand, so feeding it in lets a model predict demand
        // from the plan made for it.
        exog_columns: dataset.exog_columns.filter((c) => c !== choice),
      })
      window.location.reload()
    } catch (err: any) {
      setFailed(err?.response?.data?.detail ?? String(err))
      setBusy(false)
    }
  }

  return (
    <Card>
      <p className="text-[11px] text-[var(--color-text-muted)]">
        To compare against your roster, add a column of planned figures per period - in the same unit as{' '}
        <span className="font-medium text-[var(--color-text)]">{dataset?.target_column ?? 'the target'}</span> - and
        upload again. It is picked up automatically.
      </p>

      {candidates.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[var(--color-border)] pt-3">
          <span className="text-[11px] text-[var(--color-text-muted)]">Or use a column this file already has:</span>
          <select
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="rounded-lg border border-[var(--color-border)] bg-white px-2.5 py-1.5 text-xs"
          >
            <option value="">Choose a column…</option>
            {candidates.map((c) => (
              <option key={c.name} value={c.name}>{c.name}</option>
            ))}
          </select>
          <button
            onClick={save}
            disabled={!choice || busy}
            className="rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--color-primary-dark)] disabled:opacity-50"
          >
            {busy ? 'Saving…' : 'Use this column'}
          </button>
          <span className="text-[10px] text-[var(--color-text-muted)]">Retrain afterwards.</span>
        </div>
      )}

      {failed && (
        <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{failed}</div>
      )}
    </Card>
  )
}
