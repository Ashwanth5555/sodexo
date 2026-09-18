import { useEffect, useState } from 'react'
import { LoadingState, ErrorState } from '../layout/PageContainer'
import { Card, Badge, KpiCard } from '../ui/Card'
import { fetchPreprocessing, type PreprocessingResponse } from '../../services/api'

function PreviewTable({ columns, rows }: { columns: string[]; rows: Record<string, string | number>[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="bg-[var(--color-surface-2)]">
            {columns.map((column) => (
              <th key={column} className="whitespace-nowrap px-3 py-2 font-semibold text-[var(--color-text)]">
                {column.replace(/_/g, ' ')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t border-[var(--color-border)] hover:bg-[var(--color-surface-2)]">
              {columns.map((column) => (
                <td key={column} className="whitespace-nowrap px-3 py-2 text-[var(--color-text)]">
                  {row[column]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function DataPreprocessingTab({ seriesKey }: { seriesKey: string }) {
  const [data, setData] = useState<PreprocessingResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!seriesKey) return
    setData(null)
    setError(null)
    fetchPreprocessing(seriesKey).then(setData).catch((err) => setError(err?.response?.data?.detail ?? String(err)))
  }, [seriesKey])

  if (error) return <ErrorState message={error} />
  if (!data) return <LoadingState />

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <h2 className="mb-1 text-sm font-semibold text-[var(--color-text)]">What happens before training - {data.series_label}</h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">Current values for this series.</p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <KpiCard label="Total History Available" value={`${data.total_history_days.toLocaleString()}d`} sublabel="days of history" />
          <KpiCard label="Training Window Used" value={`${data.training_window_days}d`} sublabel={`${data.training_window_rows} half-day rows`} />
          <KpiCard label="Resolved Seasonality" value={data.seasonal_period_label} />
          <KpiCard label="Forecast Horizon" value={`${data.forecast_horizon_days}d ahead`} sublabel={`${data.block_hours}-hour blocks`} />
          <KpiCard label="Input Data Used" value={`${data.input_dataset.rows} × ${data.input_dataset.columns}`} sublabel="rows × columns fed into training" />
        </div>
      </Card>

      <Card>
        <h3 className="mb-2 text-xs font-semibold text-[var(--color-text)]">Validation plan</h3>
        <div className="flex items-center gap-2">
          <Badge status={data.validation_plan.mode === 'rolling_origin' ? 'completed' : 'RUNNING'}>{data.validation_plan.mode === 'rolling_origin' ? 'Rolling-origin CV' : 'Holdout fallback'}</Badge>
          <span className="text-xs text-[var(--color-text-muted)]">{data.validation_plan.fold_count} fold(s) · {data.validation_plan.fold_validation_size_days}-day validation window each</span>
        </div>
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">{data.validation_plan.note}</p>
      </Card>

      <Card>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold text-[var(--color-text)]">Input data preview - first 10 rows (one row = one half-day block)</h3>
          <span className="text-[11px] text-[var(--color-text-muted)]">{data.input_dataset.rows} rows × {data.input_dataset.columns} columns total</span>
        </div>
        <PreviewTable columns={data.preview_columns} rows={data.preview_rows} />
      </Card>
    </div>
  )
}
