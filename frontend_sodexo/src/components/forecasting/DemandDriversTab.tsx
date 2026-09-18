import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
import { LoadingState, ErrorState } from '../layout/PageContainer'
import { Card, Badge } from '../ui/Card'
import { fetchDrivers, setInputColumnEnabled, type DriversResponse } from '../../services/api'

export function DemandDriversTab({ seriesKey }: { seriesKey: string }) {
  const [drivers, setDrivers] = useState<DriversResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)

  useEffect(() => {
    fetchDrivers().then(setDrivers).catch((err) => setError(String(err)))
  }, [])

  const series = drivers?.series.find((s) => s.series_key === seriesKey) ?? drivers?.series[0]

  const toggle = async (column: string, enabled: boolean) => {
    if (!series) return
    setSaving(column)
    setError(null)
    try {
      setDrivers(await setInputColumnEnabled(series.series_key, column, enabled))
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    } finally {
      setSaving(null)
    }
  }

  if (error) return <ErrorState message={error} />
  if (!drivers || !series) return <LoadingState />

  const enabledCount = series.available_input_columns.filter((c) => c.enabled).length

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">{series.series_label}</h2>
          <Badge status={enabledCount > 0 ? 'completed' : 'failed'}>{enabledCount} of {series.available_input_columns.length} inputs selected</Badge>
        </div>

        <div className="mb-4">
          <div className="mb-2 text-xs font-semibold text-[var(--color-text)]">Target column</div>
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-2 text-xs font-medium text-[var(--color-text)]">
            {series.mapping.target_column}
          </div>
          <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">Fixed for this dataset - this is the value every model predicts.</p>
        </div>

        <div>
          <div className="mb-1 text-xs font-semibold text-[var(--color-text)]">Input columns</div>
          <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">Click a column to include or exclude it. Changes apply to the next training run.</p>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {series.available_input_columns.map((c) => (
              <button
                key={c.raw}
                onClick={() => toggle(c.raw, !c.enabled)}
                disabled={saving === c.raw}
                className={`flex items-center gap-2 rounded-lg border p-2 text-left text-xs font-medium transition-colors disabled:opacity-50 ${
                  c.enabled
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/5 text-[var(--color-text)]'
                    : 'border-[var(--color-border)] bg-[var(--color-surface-2)] text-[var(--color-text-muted)]'
                }`}
              >
                <span
                  className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                    c.enabled ? 'border-[var(--color-primary)] bg-[var(--color-primary)] text-white' : 'border-[var(--color-border)] bg-[var(--color-surface)]'
                  }`}
                >
                  {c.enabled && <Check size={11} strokeWidth={3} />}
                </span>
                {c.column}
              </button>
            ))}
          </div>
          {enabledCount === 0 && (
            <p className="mt-2 text-[11px] text-[var(--color-danger)]">
              All inputs are excluded - the exogenous model variants will be skipped on the next run. Select at least one to use them.
            </p>
          )}
        </div>
      </Card>

      <Card>
        <div className="mb-2 text-xs font-semibold text-[var(--color-text)]">Primary demand signal</div>
        <div className="w-fit rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-xs font-medium text-[var(--color-text)]">
          {series.primary_demand_signal}
        </div>
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">{drivers.note}</p>
      </Card>
    </div>
  )
}
