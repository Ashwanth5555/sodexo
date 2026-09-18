import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { LoadingState, ErrorState } from '../layout/PageContainer'
import { Card, KpiCard, Badge } from '../ui/Card'
import { fetchDrift, type DriftResponse } from '../../services/api'

const STATUS_BADGE: Record<string, string> = { Stable: 'completed', Watch: 'RUNNING', 'Drift Detected': 'failed', Unknown: 'not_applicable' }
const CHART_TICK = { fontSize: 10, fill: 'var(--color-text-muted)' }

export function DriftTab({ seriesKey }: { seriesKey: string }) {
  const [drift, setDrift] = useState<DriftResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!seriesKey) return
    setDrift(null)
    setError(null)
    fetchDrift(seriesKey).then(setDrift).catch((err) => setError(err?.response?.data?.detail ?? String(err)))
  }, [seriesKey])

  if (error) return <ErrorState message={error} />
  if (!drift) return <LoadingState />

  // ONE continuous line across both windows. Splitting it into a `prior` and a
  // `recent` series left a visual break at the boundary and made the first
  // recent point look like a vertical spike, when the signal is in fact
  // unchanged - the two windows are shown instead by shading the recent period
  // and drawing each window's average as a reference line.
  const signalData = drift.demand_signal_trend.map((p) => ({ date: p.date.slice(5), value: p.value }))
  const recentDates = drift.demand_signal_trend.filter((p) => p.window === 'recent').map((p) => p.date.slice(5))
  const recentStart = recentDates[0]
  const recentEnd = recentDates[recentDates.length - 1]
  const priorAvg = drift.feature_distribution.prior_30d_mean
  const recentAvg = drift.feature_distribution.recent_7d_mean
  // Pad the axis so ordinary day-to-day movement doesn't fill the full height
  // and read as a dramatic swing.
  const values = signalData.map((p) => p.value)
  const padding = values.length ? Math.max(1, (Math.max(...values) - Math.min(...values)) * 0.35) : 1
  const yDomain: [number, number] = values.length
    ? [Math.floor(Math.min(...values) - padding), Math.ceil(Math.max(...values) + padding)]
    : [0, 1]

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">{drift.model_label}</h2>
        <Badge status={STATUS_BADGE[drift.status] ?? 'not_applicable'}>{drift.status}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Model Age" value={drift.model_age_days != null ? `${drift.model_age_days}d` : '—'} />
        <KpiCard label="Forecast Accuracy" value={drift.historical_backtest_mape != null ? `${(100 - drift.historical_backtest_mape).toFixed(1)}%` : '—'} sublabel="when last tested" />
        <KpiCard label="Over/Under Forecasting" value={drift.historical_backtest_bias != null ? `${drift.historical_backtest_bias > 0 ? '+' : ''}${drift.historical_backtest_bias}%` : '—'} sublabel="+ = forecasts too high" />
        <KpiCard
          label={`Demand Signal Change (${drift.demand_signal_name})`}
          value={drift.feature_distribution.pct_change != null ? `${drift.feature_distribution.pct_change > 0 ? '+' : ''}${drift.feature_distribution.pct_change}%` : '—'}
          sublabel="last 7 days vs. previous 30"
        />
      </div>

      <Card>
        <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-xs font-semibold text-[var(--color-text)]">
            Demand signal - {drift.demand_signal_name.replace(/_/g, ' ')}, day by day
          </h3>
          {drift.demand_signal_correlation != null && (
            <span className="text-[11px] text-[var(--color-text-muted)]">
              Tracks staffing{' '}
              <strong className={drift.demand_signal_strength === 'weak' ? 'text-[var(--color-danger)]' : 'text-[var(--color-text)]'}>
                {drift.demand_signal_strength}ly
              </strong>{' '}
              (correlation {drift.demand_signal_correlation})
            </span>
          )}
        </div>
        <p className="mb-2 text-xs text-[var(--color-text-muted)]">
          A real-world demand driver the models watch. If it shifts to a new level, they were trained on the old one and
          will start drifting. Currently averaging {drift.feature_distribution.recent_7d_mean ?? '—'} over the last 7 days,
          against {drift.feature_distribution.prior_30d_mean ?? '—'} over the 30 days before that.
        </p>
        {drift.demand_signal_strength === 'weak' && (
          <p className="mb-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
            Staffing here is set mainly by fixed meal-service windows, so this signal moves the forecast less than it
            does for the caf&eacute; series. Monitor it alongside the other drivers.
          </p>
        )}
        {signalData.length > 0 ? (
          <>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={signalData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis dataKey="date" tick={CHART_TICK} minTickGap={28} />
                <YAxis tick={CHART_TICK} domain={yDomain} />
                <Tooltip
                  formatter={(v: any) => [v, drift.demand_signal_name.replace(/_/g, ' ')]}
                  contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }}
                />
                {recentStart && recentEnd && (
                  <ReferenceArea x1={recentStart} x2={recentEnd} fill="#c2410c" fillOpacity={0.08} />
                )}
                {priorAvg != null && (
                  <ReferenceLine y={priorAvg} stroke="#9aa7c2" strokeDasharray="5 4"
                    label={{ value: `Previous 30-day avg ${priorAvg}`, position: 'insideBottomLeft', fill: '#7a8699', fontSize: 10 }} />
                )}
                {recentAvg != null && (
                  <ReferenceLine y={recentAvg} stroke="#c2410c" strokeDasharray="5 4"
                    label={{ value: `Last 7-day avg ${recentAvg}`, position: 'insideTopRight', fill: '#c2410c', fontSize: 10 }} />
                )}
                <Line type="monotone" dataKey="value" stroke="#16181d" strokeWidth={1.8} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
            {/* Explicit key: the chart uses a solid line, two dashed reference
                lines and a shaded band, none of which recharts' own legend
                covers, so each is spelled out here. */}
            <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1.5 border-t border-[var(--color-border)] pt-2 text-[11px] text-[var(--color-text-muted)]">
              <span className="flex items-center gap-1.5">
                <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke="#16181d" strokeWidth="2" /></svg>
                {drift.demand_signal_name.replace(/_/g, ' ')}, each day
              </span>
              <span className="flex items-center gap-1.5">
                <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke="#9aa7c2" strokeWidth="2" strokeDasharray="5 4" /></svg>
                Previous 30-day average ({drift.feature_distribution.prior_30d_mean ?? '—'})
              </span>
              <span className="flex items-center gap-1.5">
                <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke="#c2410c" strokeWidth="2" strokeDasharray="5 4" /></svg>
                Last 7-day average ({drift.feature_distribution.recent_7d_mean ?? '—'})
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-3 w-5 rounded-sm border border-[var(--color-border)]" style={{ background: 'rgba(194,65,12,0.08)' }} />
                Shaded = the last 7 days
              </span>
            </div>
            <p className="mt-1.5 text-[11px] text-[var(--color-text-muted)]">
              When the two dashed averages sit on top of each other, demand has not moved and the models are still trained
              on the right pattern. Regular dips are usually weekends.
            </p>
          </>
        ) : (
          <p className="text-xs text-[var(--color-text-muted)]">No demand-signal history available for this window.</p>
        )}
      </Card>

      <Card>
        <h3 className="mb-2 text-xs font-semibold text-[var(--color-text)]">How the status is decided</h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          If the demand signal moves more than 15% against the previous month the status becomes <strong>Watch</strong>;
          more than 30% and it becomes <strong>Drift Detected</strong>, meaning the models should be retrained on the
          newer pattern. Below 15% it stays <strong>Stable</strong>.
        </p>
      </Card>
    </div>
  )
}
