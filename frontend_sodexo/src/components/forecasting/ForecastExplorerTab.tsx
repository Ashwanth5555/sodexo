import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { LoadingState, ErrorState } from '../layout/PageContainer'
import { Card, KpiCard, Badge } from '../ui/Card'
import {
  fetchConfidence, fetchDrift, fetchDrivers, fetchEda, fetchForecast, fetchLeaderboard,
  type ConfidenceResponse, type DriftResponse, type DriversResponse, type EdaResponse, type ForecastResponse, type ModelRunSummary, type SeriesOption,
} from '../../services/api'

type ViewMode = 'full' | 'backtest' | 'forecast'
type Granularity = 'block' | 'daily' | 'weekly'

function isoWeekStart(dateStr: string): string {
  const d = new Date(`${dateStr.slice(0, 10)}T00:00:00Z`)
  const dow = (d.getUTCDay() + 6) % 7 // Monday = 0
  d.setUTCDate(d.getUTCDate() - dow)
  return d.toISOString().slice(0, 10)
}

function average(values: number[]): number | null {
  return values.length ? Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 100) / 100 : null
}

function aggregateByGranularity(data: { date: string; actual: number | null; backtest: number | null; future: number | null; isFuture: boolean }[], granularity: Granularity) {
  // Rows are already one-per-half-day-block, so "block" needs no aggregation.
  if (granularity === 'block') return data
  const buckets = new Map<string, { actual: number[]; backtest: number[]; future: number[]; isFuture: boolean }>()
  for (const p of data) {
    const key = granularity === 'daily' ? p.date.slice(0, 10) : isoWeekStart(p.date)
    if (!buckets.has(key)) buckets.set(key, { actual: [], backtest: [], future: [], isFuture: false })
    const bucket = buckets.get(key)!
    if (p.actual != null) bucket.actual.push(p.actual)
    if (p.backtest != null) bucket.backtest.push(p.backtest)
    if (p.future != null) bucket.future.push(p.future)
    if (p.isFuture) bucket.isFuture = true
  }
  return Array.from(buckets.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, bucket]) => ({
      date, actual: average(bucket.actual), backtest: average(bucket.backtest), future: average(bucket.future), isFuture: bucket.isFuture,
    }))
}

export function ForecastExplorerTab({ seriesOptions, seriesKey }: { seriesOptions: SeriesOption[]; seriesKey: string }) {
  const [availableModels, setAvailableModels] = useState<ModelRunSummary[]>([])
  const [modelId, setModelId] = useState<string>('')
  const [viewMode, setViewMode] = useState<ViewMode>('full')
  const [granularity, setGranularity] = useState<Granularity>('block')

  const [forecast, setForecast] = useState<ForecastResponse | null>(null)
  const [confidence, setConfidence] = useState<ConfidenceResponse | null>(null)
  const [drift, setDrift] = useState<DriftResponse | null>(null)
  const [eda, setEda] = useState<EdaResponse | null>(null)
  const [drivers, setDrivers] = useState<DriversResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!seriesKey) return
    fetchLeaderboard().then((r) => {
      const series = r.series.find((s) => s.series_key === seriesKey)
      setAvailableModels((series?.models ?? []).filter((m) => m.status === 'completed').sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999)))
    })
    setModelId('')
  }, [seriesKey])

  useEffect(() => {
    if (!seriesKey) return
    setLoading(true)
    setError(null)
    Promise.all([
      fetchForecast(seriesKey, modelId || undefined),
      fetchConfidence(seriesKey, modelId || undefined).catch(() => null),
      fetchDrift(seriesKey, modelId || undefined).catch(() => null),
      fetchEda(seriesKey).catch(() => null),
      fetchDrivers().catch(() => null),
    ])
      .then(([f, c, d, e, dr]) => {
        setForecast(f)
        setConfidence(c)
        setDrift(d)
        setEda(e)
        setDrivers(dr)
      })
      .catch((err) => setError(err?.response?.data?.detail ?? String(err)))
      .finally(() => setLoading(false))
  }, [seriesKey, modelId])

  const seriesDrivers = drivers?.series.find((s) => s.series_key === seriesKey)
  const champion = availableModels.find((m) => m.is_champion)

  const allChartData = useMemo(() => forecast?.points.map((p) => ({
    date: p.date,
    actual: p.actual,
    backtest: !p.is_future ? p.predicted : null,
    future: p.is_future ? p.predicted : null,
    isFuture: p.is_future,
  })) ?? [], [forecast])

  // "Backtest focus" shows only the more recent half of the historical
  // portion - the full backtest can span several rolling-origin folds, which
  // clutters that focused view without adding much once the pattern is
  // established. "Full timeline" genuinely means full, so it isn't halved.
  const halveHistorical = (data: typeof allChartData) => {
    const historical = data.filter((p) => !p.isFuture)
    const future = data.filter((p) => p.isFuture)
    return [...historical.slice(Math.ceil(historical.length / 2)), ...future]
  }

  // The actual line spans the whole history, but the backtest line is drawn
  // only over the most recent third of it - so the chart reads as "here is
  // the real history, and here is the window we validated the model against"
  // rather than two lines fighting for the same space end to end.
  const trimBacktestSpan = (data: typeof allChartData) => {
    const historicalCount = data.filter((p) => !p.isFuture).length
    const backtestStartIndex = Math.floor(historicalCount * (2 / 3))
    let seen = 0
    return data.map((p) => {
      if (p.isFuture) return p
      const index = seen++
      return index < backtestStartIndex ? { ...p, backtest: null } : p
    })
  }

  const rangeFiltered = useMemo(() => {
    if (viewMode === 'backtest') return halveHistorical(allChartData).filter((p) => !p.isFuture)
    if (viewMode === 'forecast') {
      const futureCount = allChartData.filter((p) => p.isFuture).length
      const contextDays = 21 // three weeks of real history for context
      return allChartData.slice(Math.max(0, allChartData.length - futureCount - contextDays))
    }
    return trimBacktestSpan(allChartData)
  }, [allChartData, viewMode])

  const chartData = useMemo(
    () => aggregateByGranularity(rangeFiltered, granularity).map((p) => ({
      ...p,
      date: granularity === 'block' ? p.date.slice(0, 16).replace('T', ' ') : p.date.slice(0, 10),
    })),
    [rangeFiltered, granularity],
  )

  const lastActual = forecast?.points.filter((p) => !p.is_future).at(-1)
  const firstFuture = forecast?.points.find((p) => p.is_future)
  const lastPoint = forecast?.points.at(-1)
  const boundaryLabel = lastActual
    ? (granularity === 'block' ? lastActual.date.slice(0, 16).replace('T', ' ') : granularity === 'daily' ? lastActual.date.slice(0, 10) : isoWeekStart(lastActual.date))
    : null

  const futureValues = forecast?.points.filter((p) => p.is_future && p.predicted != null).map((p) => p.predicted as number) ?? []
  const recentActualValues = forecast?.points.filter((p) => !p.is_future && p.actual != null).slice(-24).map((p) => p.actual as number) ?? []
  const avgFuture = futureValues.length ? futureValues.reduce((a, b) => a + b, 0) / futureValues.length : null
  const avgRecentActual = recentActualValues.length ? recentActualValues.reduce((a, b) => a + b, 0) / recentActualValues.length : null
  const trendPct = avgFuture != null && avgRecentActual ? Math.round(((avgFuture - avgRecentActual) / avgRecentActual) * 100) : null
  const trendLabel = trendPct == null ? '—' : trendPct > 5 ? `Rising (+${trendPct}%)` : trendPct < -5 ? `Falling (${trendPct}%)` : 'Flat'

  const peakHour = eda ? Object.entries(eda.by_hour_of_day).sort((a, b) => b[1] - a[1])[0] : null


  return (
    <div className="flex flex-col gap-5">
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-[var(--color-text-muted)]">Model</label>
            {/* The default option names the actual winning model rather than a
                generic "Champion (auto)". Its value stays empty so the backend
                keeps resolving whichever model currently holds the title - the
                champion is then not repeated in the list below. */}
            <select value={modelId} onChange={(e) => setModelId(e.target.value)} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-1.5 text-xs text-[var(--color-text)]">
              {champion ? (
                <option value="">{champion.model_label} - champion, MAPE {champion.mape ?? '—'}</option>
              ) : (
                <option value="">Champion</option>
              )}
              {availableModels.filter((m) => !m.is_champion).map((m) => (
                <option key={m.model_id} value={m.model_id}>{m.model_label} - MAPE {m.mape ?? '—'}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-[var(--color-text-muted)]">View</label>
            <select value={viewMode} onChange={(e) => setViewMode(e.target.value as ViewMode)} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-1.5 text-xs text-[var(--color-text)]">
              <option value="full">Full timeline</option>
              <option value="backtest">Backtest focus</option>
              <option value="forecast">Forecast focus</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-[var(--color-text-muted)]">Granularity</label>
            <select value={granularity} onChange={(e) => setGranularity(e.target.value as Granularity)} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-1.5 text-xs text-[var(--color-text)]">
              <option value="block">Half-day</option>
              <option value="daily">Daily total</option>
              <option value="weekly">Weekly average</option>
            </select>
          </div>
          {forecast && <Badge status="completed">Confidence band enabled</Badge>}
        </div>
      </Card>

      {loading && <LoadingState />}
      {error && <ErrorState message={error} />}

      {forecast && !error && (
        <>
          <Card>
            <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4 lg:grid-cols-10">
              <MetaField label="Target" value={forecast.target_label ?? 'required_manpower'} />
              <MetaField label="Frequency" value={forecast.frequency_label ?? 'Half-day'} />
              <MetaField label="Trained" value={forecast.trained_at ? forecast.trained_at.slice(0, 10) : '—'} />
              <MetaField label="Validation" value={`${forecast.evaluation_mode ?? '—'}${forecast.folds ? ` (${forecast.folds}f)` : ''}`} />
              <MetaField label="Train rows" value={forecast.train_rows != null ? String(forecast.train_rows) : '—'} />
              <MetaField label="Test rows" value={forecast.test_rows != null ? String(forecast.test_rows) : '—'} />
              <MetaField label="Drivers" value={seriesDrivers ? `${seriesDrivers.exogenous_drivers_used.length} calendar` : '—'} />
              <MetaField label="Last actual" value={lastActual?.date.slice(0, 10) ?? '—'} />
              <MetaField label="Forecast starts" value={firstFuture?.date.slice(0, 10) ?? '—'} />
              <MetaField label="Forecast ends" value={lastPoint?.date.slice(0, 10) ?? '—'} />
              <MetaField label="Selected model" value={forecast.model_label} badge={forecast.is_champion ? 'Champion' : undefined} />
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card>
              <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">Seasonality</div>
              <div className="mt-1 text-xs text-[var(--color-text)]">{peakHour ? `Peaks at ${peakHour[0]}:00` : '—'}</div>
              <div className="mt-1 text-xs text-[var(--color-text-muted)]">{peakHour ? `Average ${peakHour[1]} required manpower at the busiest hour` : 'Not available'}</div>
            </Card>
            <Card>
              <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">Forecast Explanation</div>
              <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                {forecast.model_label} selected ({forecast.metrics.mape ?? '—'}% MAPE), driven by {seriesDrivers?.exogenous_drivers_used.slice(0, 4).join(', ') ?? 'calendar drivers'}
                {seriesDrivers && seriesDrivers.exogenous_drivers_used.length > 4 ? ` and ${seriesDrivers.exogenous_drivers_used.length - 4} more` : ''}.
              </div>
            </Card>
          </div>

          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
            <KpiCard label="Selected Model" value={forecast.model_label} />
            <KpiCard label="Forecast Horizon" value={`${futureValues.length} blocks (7 days)`} />
            <KpiCard label="Historical Accuracy" value={forecast.metrics.accuracy != null ? `${forecast.metrics.accuracy}%` : '—'} />
            <KpiCard label="Forecast Trend" value={trendLabel} />
            <KpiCard label="Confidence" value={confidence?.points?.[0]?.confidence_level ?? '—'} />
            <KpiCard label="Drift Status" value={drift?.status ?? '—'} />
          </div>

          <Card>
            <div className="mb-2 text-xs text-[var(--color-text-muted)]">{seriesOptions.find((s) => s.series_key === seriesKey)?.series_label} · actual, backtest &amp; forecast</div>
            {chartData.length ? (
              <ResponsiveContainer width="100%" height={380}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} minTickGap={50} />
                  <YAxis tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} />
                  <Tooltip contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {boundaryLabel && viewMode === 'full' && (
                    <ReferenceLine x={boundaryLabel} stroke="#f59e0b" strokeDasharray="3 3" label={{ value: 'Forecast begins', position: 'insideTopRight', fill: '#f59e0b', fontSize: 10 }} />
                  )}
                  <Line type="monotone" dataKey="actual" name="Actual" stroke="#9aa7c2" dot={false} strokeWidth={1.5} connectNulls={false} />
                  <Line type="monotone" dataKey="backtest" name="Backtest prediction" stroke="#c2410c" dot={false} strokeWidth={1.5} connectNulls={false} />
                  <Line type="monotone" dataKey="future" name="Future forecast" stroke="#22c55e" dot={false} strokeWidth={2} strokeDasharray="4 3" connectNulls={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <ErrorState message="No forecast available yet - train the models first." />
            )}
          </Card>

        </>
      )}
    </div>
  )
}

function MetaField({ label, value, badge }: { label: string; value: string; badge?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">{label}</div>
      <div className="text-xs text-[var(--color-text)]">{value} {badge && <Badge status="champion">{badge}</Badge>}</div>
    </div>
  )
}
