import { useEffect, useRef, useState, type ReactNode } from 'react'
import { CheckCircle2, FileSpreadsheet, Loader2, Play, Trash2, X } from 'lucide-react'
import { Card } from '../ui/Card'
import {
  configureDataset,
  deleteDataset,
  fetchDatasets,
  trainDataset,
  uploadDatasetFile,
  type Dataset,
  type DatasetColumn,
  type DatasetUploadResponse,
  type Option,
} from '../../services/api'

/** Upload any CSV/Excel file, map its columns onto a time series, and train the
 *  full model portfolio on it. The mapping step exists because an arbitrary
 *  file has no schema we can assume - the user tells us which column is the
 *  date and which is the value to forecast. */
export function DatasetUpload({ onTrainingStarted }: { onTrainingStarted?: () => void }) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [frequencies, setFrequencies] = useState<Option[]>([])
  const [aggregations, setAggregations] = useState<Option[]>([])

  const [pending, setPending] = useState<DatasetUploadResponse | null>(null)
  const [busy, setBusy] = useState<'upload' | 'configure' | 'train' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // The mapping form, seeded from the server's suggestion on upload.
  const [dateColumn, setDateColumn] = useState('')
  const [targetColumn, setTargetColumn] = useState('')
  const [groupColumn, setGroupColumn] = useState('')
  const [frequency, setFrequency] = useState('D')
  const [aggregation, setAggregation] = useState('sum')
  const [horizon, setHorizon] = useState(14)
  const [exog, setExog] = useState<string[]>([])

  const refresh = () =>
    fetchDatasets()
      .then((data) => {
        setDatasets(data.datasets)
        setFrequencies(data.frequencies)
        setAggregations(data.aggregations)
      })
      .catch((err) => setError(err?.response?.data?.detail ?? String(err)))

  useEffect(() => {
    refresh()
  }, [])

  const columns: DatasetColumn[] = pending?.column_profile ?? []
  const numericColumns = columns.filter((c) => c.is_numeric && c.name !== targetColumn && c.name !== dateColumn)

  const handleFile = async (file: File | null) => {
    if (!file) return
    setBusy('upload')
    setError(null)
    setNotice(null)
    try {
      const data = await uploadDatasetFile(file)
      setPending(data)
      setDateColumn(data.suggestion.date_column ?? '')
      setTargetColumn(data.suggestion.target_column ?? '')
      setGroupColumn('')
      setFrequency(data.frequency ?? 'D')
      setAggregation(data.aggregation ?? 'sum')
      setHorizon(data.horizon_periods ?? 14)
      setExog([])
      setFrequencies(data.frequencies)
      setAggregations(data.aggregations)
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    } finally {
      setBusy(null)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const handleConfigure = async () => {
    if (!pending) return
    setBusy('configure')
    setError(null)
    try {
      const result = await configureDataset(pending.dataset_id, {
        name: pending.name,
        date_column: dateColumn,
        target_column: targetColumn,
        group_column: groupColumn || null,
        frequency,
        aggregation,
        horizon_periods: horizon,
        exog_columns: exog,
      })
      setNotice(
        `${result.name} is ready: ${result.series_count} series, ${result.history_periods.toLocaleString()} periods ` +
          `(${result.history_start.slice(0, 10)} to ${result.history_end.slice(0, 10)}). Train it to forecast.`,
      )
      setPending(null)
      refresh()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    } finally {
      setBusy(null)
    }
  }

  const handleTrain = async (dataset: Dataset) => {
    setBusy('train')
    setError(null)
    setNotice(null)
    try {
      await trainDataset(dataset.dataset_id)
      setNotice(`Training started for ${dataset.name}. Watch progress in Labor Forecasting → Model Training.`)
      onTrainingStarted?.()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    } finally {
      setBusy(null)
    }
  }

  const handleDelete = async (dataset: Dataset) => {
    setError(null)
    try {
      await deleteDataset(dataset.dataset_id)
      refresh()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    }
  }

  const toggleExog = (name: string) =>
    setExog((current) => (current.includes(name) ? current.filter((c) => c !== name) : [...current, name]))

  return (
    <Card>
      <h2 className="text-sm font-semibold text-[var(--color-text)]">Upload Data</h2>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
        Upload any CSV or Excel file, say which column is the date and which value to forecast, then train the full model
        portfolio on it. Your dataset then appears everywhere a series can be picked.
      </p>

      {!pending && (
        <label
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            handleFile(e.dataTransfer.files?.[0] ?? null)
          }}
          className={`mt-4 flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed px-4 py-6 text-center transition-colors ${
            dragging
              ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/5'
              : 'border-[var(--color-border)] bg-[var(--color-surface-2)] hover:border-[var(--color-primary)]'
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls"
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
          />
          {busy === 'upload' ? (
            <Loader2 size={18} className="animate-spin text-[var(--color-primary)]" />
          ) : (
            <FileSpreadsheet size={18} className="text-[var(--color-text-muted)]" />
          )}
          <span className="text-xs text-[var(--color-text)]">
            {busy === 'upload' ? 'Reading file…' : 'Drop a file here, or click to browse'}
          </span>
          <span className="text-[10px] text-[var(--color-text-muted)]">Any CSV or Excel file · up to 50MB</span>
        </label>
      )}

      {pending && (
        <div className="mt-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold text-[var(--color-text)]">{pending.original_filename}</div>
              <div className="text-[11px] text-[var(--color-text-muted)]">
                {pending.row_count.toLocaleString()} rows · {columns.length} columns · map the columns below
              </div>
            </div>
            <button
              onClick={() => setPending(null)}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-white hover:text-[var(--color-text)]"
              title="Discard"
            >
              <X size={14} />
            </button>
          </div>

          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Date column" hint="The time axis">
              <select value={dateColumn} onChange={(e) => setDateColumn(e.target.value)} className={selectClass}>
                <option value="">Choose…</option>
                {columns.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                    {c.is_date_like ? ' ✓' : ''}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Forecast" hint="The value to predict">
              <select value={targetColumn} onChange={(e) => setTargetColumn(e.target.value)} className={selectClass}>
                <option value="">Choose…</option>
                {columns.filter((c) => c.is_numeric).map((c) => (
                  <option key={c.name} value={c.name}>{c.name}</option>
                ))}
              </select>
            </Field>

            <Field label="Group by" hint="Optional: one series per value">
              <select value={groupColumn} onChange={(e) => setGroupColumn(e.target.value)} className={selectClass}>
                <option value="">No grouping — one series</option>
                {pending.suggestion.group_candidates.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </Field>

            <Field label="Frequency" hint="Rows are resampled to this">
              <select value={frequency} onChange={(e) => setFrequency(e.target.value)} className={selectClass}>
                {frequencies.map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
            </Field>

            <Field label="Combine duplicates by" hint="Many rows in one period">
              <select value={aggregation} onChange={(e) => setAggregation(e.target.value)} className={selectClass}>
                {aggregations.map((a) => (
                  <option key={a.value} value={a.value}>{a.label}</option>
                ))}
              </select>
            </Field>

            <Field label="Horizon" hint="Periods to forecast ahead">
              <input
                type="number"
                min={1}
                max={365}
                value={horizon}
                onChange={(e) => setHorizon(Math.max(1, Number(e.target.value) || 1))}
                className={selectClass}
              />
            </Field>
          </div>

          {numericColumns.length > 0 && (
            <div className="mt-3">
              <div className="text-[11px] font-medium text-[var(--color-text)]">Drivers (optional)</div>
              <div className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
                Extra numeric columns fed to the models as exogenous inputs.
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {numericColumns.map((c) => (
                  <button
                    key={c.name}
                    type="button"
                    onClick={() => toggleExog(c.name)}
                    className={`rounded-full border px-2.5 py-1 text-[10px] transition-colors ${
                      exog.includes(c.name)
                        ? 'border-[var(--color-primary)] bg-[var(--color-primary)] text-white'
                        : 'border-[var(--color-border)] bg-white text-[var(--color-text-muted)] hover:border-[var(--color-primary)]'
                    }`}
                  >
                    {c.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={handleConfigure}
            disabled={!dateColumn || !targetColumn || busy !== null}
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white hover:bg-[var(--color-primary-dark)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy === 'configure' ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
            {busy === 'configure' ? 'Checking…' : 'Save mapping'}
          </button>
        </div>
      )}

      {error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</div>}
      {notice && <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-700">{notice}</div>}

      {datasets.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-[var(--color-text-muted)]">
                <th className="pb-2">Dataset</th>
                <th className="pb-2">Forecasting</th>
                <th className="pb-2">Frequency</th>
                <th className="pb-2 text-right">Rows</th>
                <th className="pb-2 text-right">Series</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {datasets.map((d) => (
                <tr key={d.dataset_id} className="border-t border-[var(--color-border)]">
                  <td className="py-2 text-[var(--color-text)]">
                    {d.name}
                    <div className="text-[10px] text-[var(--color-text-muted)]">{d.original_filename}</div>
                  </td>
                  <td className="py-2 text-[var(--color-text-muted)]">
                    {d.target_column ? `${d.target_column} by ${d.date_column}` : 'Not mapped yet'}
                    {d.group_column && <span> · split by {d.group_column}</span>}
                  </td>
                  <td className="py-2 text-[var(--color-text-muted)]">
                    {frequencies.find((f) => f.value === d.frequency)?.label ?? d.frequency ?? '—'}
                  </td>
                  <td className="py-2 text-right text-[var(--color-text)]">{d.row_count.toLocaleString()}</td>
                  <td className="py-2 text-right text-[var(--color-text)]">{d.series.length || '—'}</td>
                  <td className="py-2">
                    <div className="flex items-center justify-end gap-1.5">
                      <button
                        onClick={() => handleTrain(d)}
                        disabled={d.status !== 'configured' || busy !== null}
                        className="inline-flex items-center gap-1 rounded-lg bg-[var(--color-primary)] px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-[var(--color-primary-dark)] disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <Play size={11} />
                        Train
                      </button>
                      <button
                        onClick={() => handleDelete(d)}
                        title="Delete dataset and everything trained from it"
                        className="rounded p-1 text-[var(--color-text-muted)] hover:bg-red-50 hover:text-red-600"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

const selectClass =
  'mt-1 w-full rounded-lg border border-[var(--color-border)] bg-white px-2.5 py-1.5 text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]'

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div>
      <label className="block text-[11px] font-medium text-[var(--color-text)]">{label}</label>
      {hint && <div className="text-[10px] text-[var(--color-text-muted)]">{hint}</div>}
      {children}
    </div>
  )
}
