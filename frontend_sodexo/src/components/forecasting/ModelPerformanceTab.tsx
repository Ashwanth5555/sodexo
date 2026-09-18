import { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { LoadingState, ErrorState } from '../layout/PageContainer'
import { Card, Badge } from '../ui/Card'
import { fetchLeaderboard, type SeriesLeaderboard } from '../../services/api'

const CHART_TICK = { fontSize: 10, fill: 'var(--color-text-muted)' }

export function ModelPerformanceTab() {
  const [leaderboard, setLeaderboard] = useState<SeriesLeaderboard[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchLeaderboard().then((r) => setLeaderboard(r.series)).catch((err) => setError(String(err)))
  }, [])

  if (error) return <ErrorState message={error} />
  if (!leaderboard) return <LoadingState />

  return (
    <div className="flex flex-col gap-5">
      {leaderboard.map((series) => {
        const chartData = series.models
          .filter((m) => m.status === 'completed' && m.mape != null)
          .map((m) => ({ model: m.model_label, mape: m.mape as number, isChampion: m.is_champion }))
        return (
          <Card key={series.series_key}>
            <h3 className="mb-2 text-sm font-semibold text-[var(--color-text)]">{series.series_label}</h3>
            {chartData.length > 0 && (
              <div className="mb-4">
                <div className="mb-1 text-xs text-[var(--color-text-muted)]">MAPE by model - average % error, lower is better (champion in green)</div>
                {/* Space for the angled model names is reserved by the XAxis
                    `height` prop alone - also setting margin.bottom double-
                    counts it and collapses the plot area to nothing. */}
                <ResponsiveContainer width="100%" height={360}>
                  <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                    <XAxis
                      dataKey="model"
                      interval={0}
                      angle={-40}
                      textAnchor="end"
                      height={140}
                      tick={{ ...CHART_TICK, fontSize: 9 }}
                    />
                    {/* A 0-based axis with an auto top - MAPE values sit in a
                        narrow band, so a fixed 0-100 axis flattened every bar
                        to the same height and hid the real differences. */}
                    <YAxis type="number" domain={[0, 'auto']} unit="%" tick={CHART_TICK} />
                    <Tooltip
                      formatter={(value: any) => [`${value}%`, 'MAPE']}
                      contentStyle={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', fontSize: 12 }}
                    />
                    <Bar dataKey="mape" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                      {chartData.map((d) => <Cell key={d.model} fill={d.isChampion ? '#15803d' : '#c2410c'} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-[var(--color-text-muted)]">
                    <th className="pb-2">Model</th><th className="pb-2">Status</th>
                    <th className="pb-2 text-right">Accuracy</th><th className="pb-2 text-right">MAPE</th><th className="pb-2 text-right">MAE</th><th className="pb-2 text-right">RMSE</th><th className="pb-2 text-right">Bias</th>
                  </tr>
                </thead>
                <tbody>
                  {series.models.map((m) => (
                    <tr key={m.model_id} className="border-t border-[var(--color-border)]">
                      <td className="py-2 text-[var(--color-text)]">{m.model_label} {m.is_champion && <Badge status="champion">Champion</Badge>}</td>
                      <td className="py-2"><Badge status={m.status}>{m.status.replace('_', ' ')}</Badge></td>
                      <td className="py-2 text-right font-medium text-[var(--color-text)]">{m.accuracy != null ? `${m.accuracy}%` : '—'}</td>
                      <td className="py-2 text-right text-[var(--color-text)]">{m.mape ?? '—'}</td>
                      <td className="py-2 text-right text-[var(--color-text)]">{m.mae ?? '—'}</td>
                      <td className="py-2 text-right text-[var(--color-text)]">{m.rmse ?? '—'}</td>
                      <td className="py-2 text-right text-[var(--color-text)]">{m.bias ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )
      })}
    </div>
  )
}
