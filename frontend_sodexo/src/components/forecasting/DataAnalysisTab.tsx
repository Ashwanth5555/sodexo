import { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { LoadingState, ErrorState } from '../layout/PageContainer'
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

function CurveChart({ data }: { data: { label: string; value: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} />
        <YAxis tick={{ fontSize: 10, fill: 'var(--color-text-muted)' }} />
        <Tooltip contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }} />
        <Line type="monotone" dataKey="value" name="Average demand" stroke="#c2410c" strokeWidth={2} dot={{ r: 2 }} />
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

function hourInsight(data: { label: string; value: number }[]): string {
  if (!data.length) return ''
  const { max, min } = extremes(data)
  return `Busiest at ${max.label} (${max.value}) · quietest at ${min.label} (${min.value}).`
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

export function DataAnalysisTab({ seriesKey }: { seriesKey: string }) {
  const [eda, setEda] = useState<EdaResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!seriesKey) return
    setEda(null)
    fetchEda(seriesKey).then(setEda).catch((err) => setError(err?.response?.data?.detail ?? String(err)))
  }, [seriesKey])

  if (error) return <ErrorState message={error} />
  if (!eda) return <LoadingState />

  const byHour = Object.entries(eda.by_hour_of_day).map(([h, v]) => ({ label: `${h}:00`, value: v })).sort((a, b) => Number(a.label.replace(':00', '')) - Number(b.label.replace(':00', '')))
  const byDow = Object.entries(eda.by_day_of_week).map(([d, v]) => ({ label: DOW_LABELS[Number(d)] ?? d, value: v }))
  const season = Object.entries(eda.season_effect).map(([k, v]) => ({ label: k, value: v }))

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <h3 className="mb-2 text-xs font-semibold text-[var(--color-text)]">Historical demand trend (last 90 days)</h3>
        <TrendChart data={eda.daily_trend} />
        <Insight>{trendInsight(eda.daily_trend)}</Insight>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <h3 className="mb-2 text-xs font-semibold text-[var(--color-text)]">Average demand curve by hour of day</h3>
          <CurveChart data={byHour} />
          <Insight>{hourInsight(byHour)}</Insight>
        </Card>
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
          </div>
        </Card>
      </div>
    </div>
  )
}
