import { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { LoadingState, ErrorState, EmptyState } from '../layout/PageContainer'
import { Card } from '../ui/Card'
import { fetchEda, type EdaResponse } from '../../services/api'

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function TrendChart({ data }: { data: EdaResponse['daily_trend'] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} minTickGap={30} />
        <YAxis tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} />
        <Tooltip contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }} />
        <Line type="monotone" dataKey="actual" name="Daily average" stroke="#d6b199" strokeWidth={1} dot={false} />
        <Line type="monotone" dataKey="trend_7d" name="7-day trend" stroke="#c2410c" strokeWidth={2.5} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}


function CompareBarChart({ data }: { data: { label: string; value: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} />
        <YAxis tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} />
        <Tooltip contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }} />
        <Bar dataKey="value" fill="#c2410c" radius={[3, 3, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function Insight({ children }: { children: string }) {
  if (!children) return null
  return <p className="mt-2 text-xs text-[var(--color-text-muted)]">{children}</p>
}

function trendInsight(daily: EdaResponse['daily_trend']): string {
  if (daily.length < 2) return ''
  const first = daily[0].trend_7d
  const last = daily[daily.length - 1].trend_7d
  if (!first) return ''
  const pct = ((last - first) / first) * 100
  if (Math.abs(pct) < 2) return `Demand has stayed roughly flat over this period (${first} → ${last}).`
  return `Demand has ${pct > 0 ? 'risen' : 'fallen'} ${Math.abs(pct).toFixed(0)}% over this period (${first} → ${last}).`
}

function extremes(data: { label: string; value: number }[]) {
  const max = data.reduce((a, b) => (b.value > a.value ? b : a))
  const min = data.reduce((a, b) => (b.value < a.value ? b : a))
  return { max, min }
}


function dowInsight(data: { label: string; value: number }[]): string {
  if (!data.length) return ''
  const { max, min } = extremes(data)
  return `Busiest on ${max.label} (${max.value}) · quietest on ${min.label} (${min.value}).`
}

function seasonInsight(data: { label: string; value: number }[]): string {
  if (!data.length) return ''
  const { max, min } = extremes(data)
  return `Highest demand in ${max.label} (${max.value}) · lowest in ${min.label} (${min.value}).`
}

function LiftChip({ label, pct }: { label: string; pct: number | null }) {
  if (pct === null) return null
  const up = pct >= 0
  return (
    <div className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-3">
      <span className="text-xs font-medium text-[var(--color-text)]">{label}</span>
      <span className={`text-sm font-semibold ${up ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'}`}>
        {up ? '+' : ''}{pct}%
      </span>
    </div>
  )
}

export function DataAnalysisTab({ seriesKey, seriesLoading }: { seriesKey: string; seriesLoading?: boolean }) {
  const [eda, setEda] = useState<EdaResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!seriesKey) return
    let stale = false
    setEda(null)
    // Cleared here, not just on success. Leaving a previous series' error in
    // place made it permanent: a key that had gone stale failed once, and the
    // message stayed on screen through every later series that loaded fine.
    setError(null)
    fetchEda(seriesKey)
      .then((d) => !stale && setEda(d))
      .catch((err) => !stale && setError(err?.response?.data?.detail ?? String(err)))
    return () => {
      stale = true
    }
  }, [seriesKey])

  // Three states, not two. An empty picker is not the same as a request in
  // flight, and folding them together left this page on "Loading data..."
  // forever whenever there was no series to pick.
  if (seriesLoading) return <LoadingState />
  if (!seriesKey) return <EmptyState message="No data yet. Upload a CSV or Excel file on the Data Quality page - this page reads it as soon as it is mapped." />
  if (error) return <ErrorState message={error} />
  if (!eda) return <LoadingState />
  const byDow = Object.entries(eda.by_day_of_week).map(([d, v]) => ({ label: DOW_LABELS[Number(d)] ?? d, value: v }))
  const season = Object.entries(eda.season_effect).map(([k, v]) => ({ label: k, value: v }))

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <h3 className="mb-2 text-xs font-semibold text-[var(--color-text)]">Historical demand trend (last 90 days)</h3>
        <TrendChart data={eda.daily_trend} />
        <Insight>{trendInsight(eda.daily_trend)}</Insight>
      </Card>

      {/* The hour-of-day curve is gone. It only says something when the file
          is hourly; on daily data every row falls in hour zero, so the chart
          was a single dot above "busiest at 0:00, quietest at 0:00" - which
          reads as a broken chart rather than as "this grain has no hours". */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <h3 className="mb-2 text-xs font-semibold text-[var(--color-text)]">Average demand by day of week</h3>
          <CompareBarChart data={byDow} />
          <Insight>{dowInsight(byDow)}</Insight>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <h3 className="mb-2 text-xs font-semibold text-[var(--color-text)]">Seasonal demand</h3>
          <CompareBarChart data={season} />
          <Insight>{seasonInsight(season)}</Insight>
        </Card>
        <Card>
          <h3 className="mb-3 text-xs font-semibold text-[var(--color-text)]">Demand drivers at a glance</h3>
          <div className="flex flex-col gap-2">
            <LiftChip label="Holiday vs. non-holiday" pct={eda.holiday_lift_pct} />
            {Object.entries(eda.flag_effects).map(([flag, effect]) => (
              <LiftChip key={flag} label={`${flag.replace(/_/g, ' ')} on vs. off`} pct={effect.lift_pct} />
            ))}
            {/* Every chip hides itself when its lift cannot be computed, so
                without this the card renders as a bare heading over nothing. */}
            {eda.holiday_lift_pct === null && Object.keys(eda.flag_effects).length === 0 && (
              <p className="text-xs text-[var(--color-text-muted)]">
                This series has no holiday or flag column with both states present, so there is nothing to compare.
              </p>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
