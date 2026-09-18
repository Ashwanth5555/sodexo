import { useEffect, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, Cell } from 'recharts'
import { LoadingState, ErrorState, EmptyState } from '../layout/PageContainer'
import { Card, Badge, KpiCard } from '../ui/Card'
import { fetchStaffingImpact, type StaffingImpactResponse } from '../../services/api'

const STATUS_BADGE: Record<string, string> = { Understaffed: 'failed', Balanced: 'completed', Overstaffed: 'RUNNING' }
const CHART_TICK = { fontSize: 10, fill: 'var(--color-text-muted)' }

function gapColor(gap: number): string {
  if (gap < 0) return '#b91c1c'
  if (gap > 2) return '#a16207'
  return '#15803d'
}

export function StaffingImpactTab({ seriesKey }: { seriesKey: string }) {
  const [data, setData] = useState<StaffingImpactResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [drill, setDrill] = useState<string | null>(null)

  useEffect(() => {
    if (!seriesKey) return
    setData(null)
    setError(null)
    setDrill(null)
    fetchStaffingImpact(seriesKey).then(setData).catch((err) => setError(err?.response?.data?.detail ?? String(err)))
  }, [seriesKey])

  if (error) return <ErrorState message={error} />
  if (!data) return <LoadingState />
  if (!data.points.length) return <EmptyState message={data.note} />

  const chartData = data.points.slice(-72).map((p) => ({
    date: p.date.slice(5, 16).replace('T', ' '),
    required: p.required_headcount,
    scheduled: p.scheduled_headcount,
    gap: p.gap,
  }))

  // A per-hour-of-day view of required headcount - the recurring shape
  // behind the gap, same idea as the Data Analysis hour-of-day curve, so
  // it's clear *when* the gap tends to happen rather than just that it does.
  const byHour = new Map<number, number[]>()
  for (const p of data.points) {
    const hour = Number(p.date.slice(11, 13))
    if (!byHour.has(hour)) byHour.set(hour, [])
    byHour.get(hour)!.push(p.required_headcount)
  }
  const seasonality = Array.from(byHour.entries())
    .map(([hour, values]) => ({ hour, avg: Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 100) / 100 }))
    .sort((a, b) => a.hour - b.hour)
  const peakHour = seasonality.length ? seasonality.reduce((a, b) => (b.avg > a.avg ? b : a)) : null

  const understaffedCount = data.points.filter((p) => p.status === 'Understaffed').length
  const overstaffedCount = data.points.filter((p) => p.status === 'Overstaffed').length
  const balancedPct = Math.round(((data.points.length - understaffedCount - overstaffedCount) / data.points.length) * 100)

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <p className="text-xs text-[var(--color-text-muted)]">{data.note} Window: {data.window_start} to {data.window_end}.</p>
      </Card>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Understaffed Hours" value={String(understaffedCount)} sublabel="scheduled below required" />
        <KpiCard label="Overstaffed Hours" value={String(overstaffedCount)} sublabel="scheduled well above required" />
        <KpiCard label="Balanced" value={`${balancedPct}%`} sublabel="of all hours in window" />
        <KpiCard label="Seasonality Peak" value={peakHour ? `${peakHour.hour}:00` : '—'} sublabel={peakHour ? `avg ${peakHour.avg} required` : 'Not available'} />
      </div>

      <Card>
        <h3 className="mb-1 text-xs font-semibold text-[var(--color-text)]">Required vs. scheduled headcount (most recent 72 hours)</h3>
        <p className="mb-2 text-xs text-[var(--color-text-muted)]">Lines show the two headcount curves; bars show the gap itself (scheduled minus required) - red bars are understaffed hours, gold is overstaffed, green is balanced.</p>
        <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">Click any point on the chart to see that hour in full below.</p>
        <ResponsiveContainer width="100%" height={340}>
          <ComposedChart
            data={chartData}
            onClick={(state: any) => {
              const label = state?.activeLabel
              if (label) setDrill((current) => (current === label ? null : label))
            }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
            <XAxis dataKey="date" tick={CHART_TICK} minTickGap={40} />
            <YAxis tick={CHART_TICK} />
            <Tooltip contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {drill && <ReferenceLine x={drill} stroke="#1d4ed8" strokeDasharray="3 3" />}
            <Bar dataKey="gap" name="Gap (scheduled - required)" radius={[2, 2, 0, 0]} isAnimationActive={false}>
              {chartData.map((d, i) => <Cell key={i} fill={gapColor(d.gap)} fillOpacity={0.35} />)}
            </Bar>
            <Line type="monotone" dataKey="required" name="Required" stroke="#c2410c" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="scheduled" name="Scheduled" stroke="#16181d" strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
          </ComposedChart>
        </ResponsiveContainer>

        {drill && (() => {
          const point = data.points.find((p) => p.date.slice(5, 16).replace('T', ' ') === drill)
          if (!point) return null
          const sameHour = data.points.filter((p) => p.date.slice(11, 13) === point.date.slice(11, 13))
          const avgRequired = sameHour.reduce((s, p) => s + p.required_headcount, 0) / sameHour.length
          const shortfalls = sameHour.filter((p) => p.gap < 0).length
          return (
            <div className="mt-3 rounded-lg border border-[var(--color-primary)] bg-[var(--color-surface-2)] p-3">
              <div className="mb-2 flex items-center justify-between">
                <h4 className="text-xs font-semibold text-[var(--color-text)]">{point.date.slice(0, 16).replace('T', ' ')}</h4>
                <button onClick={() => setDrill(null)} className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Close ✕</button>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-5">
                <Detail label="Required" value={`${point.required_headcount}`} />
                <Detail label="Scheduled" value={`${point.scheduled_headcount}`} />
                <Detail label="Gap" value={`${point.gap > 0 ? '+' : ''}${point.gap}`} tone={point.gap < 0 ? 'bad' : point.gap > 2 ? 'warn' : 'good'} />
                <Detail label="Status" value={point.status} />
                <Detail label={`Typical at ${point.date.slice(11, 13)}:00`} value={`${avgRequired.toFixed(1)} required`} />
              </div>
              <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
                {point.gap < 0
                  ? `Short ${-point.gap} staff this hour. Across the window this hour ran short ${shortfalls} time(s) out of ${sameHour.length} - ${shortfalls > sameHour.length / 3 ? 'a recurring gap worth fixing in the roster template.' : 'a one-off rather than a pattern.'}`
                  : point.gap > 2
                    ? `Overstaffed by ${point.gap} this hour - hours that could cover a short shift elsewhere.`
                    : 'Staffing matched demand this hour.'}
              </p>
            </div>
          )
        })()}
      </Card>

      <Card>
        <h3 className="mb-1 text-xs font-semibold text-[var(--color-text)]">Seasonality signal - average required headcount by hour of day</h3>
        <p className="mb-2 text-xs text-[var(--color-text-muted)]">The recurring daily shape behind the gap above - staffing shortfalls cluster around the peak hour shown in the KPI strip.</p>
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={seasonality.map((s) => ({ hour: `${s.hour}:00`, avg: s.avg }))}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
            <XAxis dataKey="hour" tick={CHART_TICK} />
            <YAxis tick={CHART_TICK} />
            <Tooltip contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }} />
            <Line type="monotone" dataKey="avg" name="Average required" stroke="#c2410c" strokeWidth={2} dot={{ r: 2 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </Card>

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-[var(--color-text-muted)]">
                <th className="pb-2">Hour</th>
                <th className="pb-2 text-right">Required</th>
                <th className="pb-2 text-right">Scheduled</th>
                <th className="pb-2 text-right">Gap</th>
                <th className="pb-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.points.slice(-24).map((p) => (
                <tr key={p.date} className="border-t border-[var(--color-border)]">
                  <td className="py-2 text-[var(--color-text)]">{p.date.slice(0, 16).replace('T', ' ')}</td>
                  <td className="py-2 text-right text-[var(--color-text)]">{p.required_headcount}</td>
                  <td className="py-2 text-right text-[var(--color-text)]">{p.scheduled_headcount}</td>
                  <td className="py-2 text-right text-[var(--color-text)]">{p.gap}</td>
                  <td className="py-2"><Badge status={STATUS_BADGE[p.status] ?? 'not_applicable'}>{p.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}

function Detail({ label, value, tone }: { label: string; value: string; tone?: 'good' | 'bad' | 'warn' }) {
  const color = tone === 'bad' ? 'text-[var(--color-danger)]' : tone === 'warn' ? 'text-[var(--color-accent)]' : tone === 'good' ? 'text-[var(--color-success)]' : 'text-[var(--color-text)]'
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">{label}</div>
      <div className={`text-sm font-semibold ${color}`}>{value}</div>
    </div>
  )
}
