import { useEffect, useRef, useState } from 'react'
import { LoadingState, EmptyState } from '../layout/PageContainer'
import { Card, Badge } from '../ui/Card'
import { fetchLeaderboard, fetchTrainingStatus, startTraining, type ModelRunSummary, type SeriesLeaderboard, type TrainingJobStatus } from '../../services/api'

function LiveDot() {
  return (
    <span className="relative flex h-2 w-2">
      <span className="absolute inline-flex h-full w-full rounded-full bg-[var(--color-danger)] opacity-75" style={{ animation: 'live-pulse 1.3s ease-in-out infinite' }} />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--color-danger)]" />
    </span>
  )
}

export function ModelTrainingTab() {
  const [job, setJob] = useState<TrainingJobStatus | null>(null)
  const [leaderboard, setLeaderboard] = useState<SeriesLeaderboard[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const loadLeaderboard = () => fetchLeaderboard().then((r) => setLeaderboard(r.series))

  const startPolling = () => {
    if (pollRef.current) return
    pollRef.current = window.setInterval(refreshStatus, 2000)
  }

  const refreshStatus = async () => {
    const status = await fetchTrainingStatus()
    setJob(status)
    // Refresh the table every tick while running too, so pending rows flip
    // to completed/failed live instead of only appearing once the whole job finishes.
    loadLeaderboard()
    if (status?.status === 'RUNNING') {
      startPolling()
    } else if (pollRef.current) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => {
    // If a run is already in progress when this page mounts (e.g. the
    // viewer navigated here mid-run), pick up live polling immediately
    // instead of showing one static snapshot.
    refreshStatus()
    loadLeaderboard()
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
    }
  }, [])

  const [windowDays, setWindowDays] = useState(180)

  const handleTrain = async () => {
    setError(null)
    try {
      await startTraining(windowDays)
      await refreshStatus()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    }
  }

  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const isRunning = job?.status === 'RUNNING'
  const progressPct = job && job.total_steps > 0 ? Math.round((job.completed_steps / job.total_steps) * 100) : 0

  useEffect(() => {
    if (!job?.started_at) return
    // Backend timestamps are naive UTC (no offset suffix) - force UTC parsing
    // so this doesn't drift by the viewer's timezone offset.
    const parseUtc = (s: string) => new Date(s.endsWith('Z') ? s : `${s}Z`).getTime()
    const startedAt = parseUtc(job.started_at)
    const end = job.completed_at ? parseUtc(job.completed_at) : Date.now()
    setElapsedSeconds(Math.max(0, Math.round((end - startedAt) / 1000)))
    if (!isRunning) return
    const interval = window.setInterval(() => {
      setElapsedSeconds(Math.max(0, Math.round((Date.now() - startedAt) / 1000)))
    }, 1000)
    return () => window.clearInterval(interval)
  }, [job?.started_at, job?.completed_at, isRunning])

  const formatDuration = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return m > 0 ? `${m}m ${s}s` : `${s}s`
  }

  const recentActivity: (ModelRunSummary & { series_label: string })[] = leaderboard
    ? leaderboard
        .flatMap((series) => series.models.filter((m) => m.trained_at).map((m) => ({ ...m, series_label: series.series_label })))
        .sort((a, b) => (a.trained_at! < b.trained_at! ? 1 : -1))
        .slice(0, 8)
    : []

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-[var(--color-text)]">Full model portfolio training</h2>
              {isRunning && (
                <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-danger)]">
                  <LiveDot /> Live
                </span>
              )}
            </div>
            {job && (
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                {isRunning ? `Training ${job.current_series ?? ''} · just finished: ${job.current_model ?? '—'}` : job.status === 'COMPLETED' ? 'Last run completed.' : job.status === 'FAILED' ? `Last run failed: ${job.error_message}` : ''}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-[var(--color-text-muted)]">Training window</label>
            <select
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
              disabled={isRunning}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-2 py-1.5 text-xs text-[var(--color-text)] disabled:opacity-50"
            >
              <option value={120}>120 days (fastest)</option>
              <option value={180}>180 days (default)</option>
              <option value={365}>365 days</option>
              <option value={730}>730 days (slowest, most history)</option>
            </select>
            <button
              onClick={handleTrain}
              disabled={isRunning}
              className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white hover:bg-[var(--color-primary-dark)] disabled:opacity-50"
            >
              {isRunning ? 'Training…' : 'Train All Models'}
            </button>
          </div>
        </div>
        {error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</div>}
        {job && (
          <div className="mt-4">
            <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--color-surface-2)]">
              <div className="h-full bg-[var(--color-primary)] transition-all duration-700 ease-out" style={{ width: `${progressPct}%` }} />
            </div>
            <div className="mt-1 flex items-center justify-between text-[11px] text-[var(--color-text-muted)]">
              <span>{job.completed_steps} / {job.total_steps} model runs · {progressPct}%</span>
              <span>{isRunning ? 'Elapsed' : job.status === 'COMPLETED' ? 'Total time' : 'Ran for'}: {formatDuration(elapsedSeconds)}</span>
            </div>
          </div>
        )}
      </Card>

      {recentActivity.length > 0 && (
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-xs font-semibold text-[var(--color-text)]">{isRunning ? 'Live activity' : 'Recent activity'}</h3>
            {isRunning && <LiveDot />}
          </div>
          <div className="flex flex-col gap-1.5">
            {recentActivity.map((m) => (
              <div
                key={`${m.series_label}-${m.model_id}-${m.trained_at}`}
                className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-xs"
                style={{ animation: 'feed-in 0.4s ease-out' }}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Badge status={m.status}>{m.status.replace('_', ' ')}</Badge>
                    <span className="font-medium text-[var(--color-text)]">{m.model_label}</span>
                    <span className="text-[var(--color-text-muted)]">{m.series_label}</span>
                  </div>
                  <span className="shrink-0 text-[var(--color-text-muted)]">{m.mape != null ? `MAPE ${m.mape}` : ''} {m.runtime_seconds != null ? `· ${m.runtime_seconds}s` : ''}</span>
                </div>
                {/* A bare "not applicable" reads as arbitrary - always say why,
                    and which series it applies to. */}
                {m.failure_reason && (
                  <div className="mt-1.5 border-t border-[var(--color-border)] pt-1.5 text-[11px] leading-snug text-[var(--color-text-muted)]">
                    {m.failure_reason}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {leaderboard && leaderboard.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {leaderboard.map((series) => {
            const champion = series.models.find((m) => m.is_champion)
            if (!champion) return null
            return (
              <Card key={series.series_key} className="border-[var(--color-primary)]">
                <div className="flex items-center gap-2">
                  <Badge status="champion">Champion Model</Badge>
                  <span className="text-xs text-[var(--color-text-muted)]">{series.series_label}</span>
                </div>
                <div className="mt-1 text-base font-semibold text-[var(--color-text)]">{champion.model_label}</div>
                <div className="mt-2 flex flex-wrap gap-4 text-xs text-[var(--color-text-muted)]">
                  <span>MAPE <span className="text-[var(--color-text)]">{champion.mape}</span></span>
                  <span>Accuracy <span className="text-[var(--color-text)]">{champion.accuracy}%</span></span>
                  <span>MAE <span className="text-[var(--color-text)]">{champion.mae}</span></span>
                  <span>RMSE <span className="text-[var(--color-text)]">{champion.rmse}</span></span>
                  <span>WAPE <span className="text-[var(--color-text)]">{champion.wape}</span></span>
                  <span>Bias <span className="text-[var(--color-text)]">{champion.bias}</span></span>
                  <span>Runtime <span className="text-[var(--color-text)]">{champion.runtime_seconds}s</span></span>
                </div>
              </Card>
            )
          })}
        </div>
      )}

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-[var(--color-text)]">Model comparison - full portfolio</h2>
        {!leaderboard ? (
          <LoadingState />
        ) : leaderboard.length === 0 ? (
          <EmptyState message="No training run yet - click Train All Models." />
        ) : (
          <div className="flex flex-col gap-5">
            {leaderboard.map((series) => {
              const totalRuntime = series.models.reduce((sum, m) => sum + (m.runtime_seconds ?? 0), 0)
              return (
                <div key={series.series_key}>
                  <div className="mb-2 flex items-center justify-between">
                    <div className="text-xs font-semibold text-[var(--color-text)]">{series.series_label}</div>
                    <div className="text-[11px] text-[var(--color-text-muted)]">Total: {formatDuration(Math.round(totalRuntime))}</div>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead>
                        <tr className="whitespace-nowrap text-[var(--color-text-muted)]">
                          <th className="pb-2 pr-4">Model</th>
                          <th className="pb-2 pr-4">Status</th>
                          <th className="pb-2 pr-4 text-right">Runtime</th>
                          <th className="pb-2 pr-4 text-right">Train Rows</th>
                          <th className="pb-2 pr-4 text-right">Test Rows</th>
                          <th className="pb-2 pr-4 text-right">MAPE</th>
                          <th className="pb-2 pr-4 text-right">RMSE</th>
                          <th className="pb-2 pr-4 text-right">MAE</th>
                          <th className="pb-2 pr-4 text-right">WAPE</th>
                          <th className="pb-2 pr-4 text-right">Bias</th>
                          <th className="pb-2">Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {series.models.map((m) => {
                          const justFinished = isRunning && series.series_key === job?.current_series && m.model_label === job?.current_model
                          return (
                            <tr
                              key={justFinished ? `${m.model_id}-${job?.completed_steps}` : m.model_id}
                              className={`whitespace-nowrap border-t border-[var(--color-border)] ${m.status === 'pending' ? 'opacity-50' : ''}`}
                              style={justFinished ? { animation: 'row-glow 1.6s ease-out' } : undefined}
                            >
                              <td className="py-2 pr-4 text-[var(--color-text)]">
                                {m.model_label} {m.is_champion && <Badge status="champion">Champion</Badge>}
                              </td>
                              <td className="py-2 pr-4"><Badge status={m.status}>{m.status.replace('_', ' ')}</Badge></td>
                              <td className="py-2 pr-4 text-right text-[var(--color-text)]">{m.runtime_seconds != null ? `${m.runtime_seconds}s` : '—'}</td>
                              <td className="py-2 pr-4 text-right text-[var(--color-text)]">{m.train_rows ?? '—'}</td>
                              <td className="py-2 pr-4 text-right text-[var(--color-text)]">{m.test_rows ?? '—'}</td>
                              <td className="py-2 pr-4 text-right text-[var(--color-text)]">{m.mape ?? '—'}</td>
                              <td className="py-2 pr-4 text-right text-[var(--color-text)]">{m.rmse ?? '—'}</td>
                              <td className="py-2 pr-4 text-right text-[var(--color-text)]">{m.mae ?? '—'}</td>
                              <td className="py-2 pr-4 text-right text-[var(--color-text)]">{m.wape ?? '—'}</td>
                              <td className="py-2 pr-4 text-right text-[var(--color-text)]">{m.bias ?? '—'}</td>
                              <td className="py-2 whitespace-normal text-[var(--color-text-muted)]">{m.failure_reason ?? ''}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>
    </div>
  )
}
