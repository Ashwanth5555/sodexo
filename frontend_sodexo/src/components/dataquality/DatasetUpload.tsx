import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, FileSpreadsheet, Loader2, X } from 'lucide-react'
import { Card } from '../ui/Card'
import { UploadGuidance } from './UploadGuidance'
import { DatasetPreview } from './DatasetPreview'
import {
  fetchDataset,
  fetchDatasets,
  uploadDatasetFile,
  type DatasetUploadResponse,
} from '../../services/api'
import { LABOR_FORECASTING_HOME } from '../layout/Sidebar'

/** Upload a CSV/Excel file and show what came through.
 *
 *  Upload and inspection only. The columns are mapped and checked server-side
 *  as the file lands - the target has to be a number in every series, the
 *  frequency has to match the observed spacing - so there is nothing to
 *  confirm here. Correcting a mapping, analysing a series and training all
 *  happen under Labor Forecasting, which is where those decisions belong.
 */
export function DatasetUpload({ onChanged }: { onChanged?: () => void }) {
  const [pending, setPending] = useState<DatasetUploadResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)
  const [restoring, setRestoring] = useState(true)
  const inputRef = useRef<HTMLInputElement>(null)

  // Show what is already on the server. The upload response used to be the
  // only thing that ever filled this panel, so a refresh emptied it and a file
  // that was still uploaded looked like it had never arrived - people then
  // uploaded it a second time.
  useEffect(() => {
    let stale = false
    fetchDatasets()
      .then(async (data) => {
        const latest = data.datasets[data.datasets.length - 1]
        if (!latest || stale) return
        const detail = await fetchDataset(latest.dataset_id, true)
        if (stale) return
        setPending(detail as DatasetUploadResponse)
        setWarnings((detail as DatasetUploadResponse).warnings ?? [])
      })
      .catch(() => {
        /* Nothing to restore is not an error worth showing. */
      })
      .finally(() => !stale && setRestoring(false))
    return () => {
      stale = true
    }
  }, [])

  const handleFile = async (file: File | null) => {
    if (!file) return
    setBusy(true)
    setError(null)
    setWarnings([])
    try {
      const data = await uploadDatasetFile(file)
      setPending(data)
      setWarnings(data.warnings ?? [])
      onChanged?.()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <Card>
      <h2 className="text-sm font-semibold text-[var(--color-text)]">Upload Data</h2>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
        Upload any CSV or Excel file. We read it, map its columns, and show you what came through. Check the mapping next, then train.
      </p>

      {!pending && restoring && (
        <div className="mt-4 rounded-lg border border-dashed border-[var(--color-border)] px-4 py-6 text-center text-xs text-[var(--color-text-muted)]">
          Checking for an uploaded file…
        </div>
      )}

      {!pending && !restoring && (
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
          {busy ? (
            <Loader2 size={18} className="animate-spin text-[var(--color-primary)]" />
          ) : (
            <FileSpreadsheet size={18} className="text-[var(--color-text-muted)]" />
          )}
          <span className="text-xs text-[var(--color-text)]">
            {busy ? 'Reading file…' : 'Drop a file here, or click to browse'}
          </span>
          <span className="text-[10px] text-[var(--color-text-muted)]">Any CSV or Excel file</span>
        </label>
      )}

      {!pending && !restoring && <UploadGuidance />}

      {pending && (
        <div className="mt-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="text-xs font-semibold text-[var(--color-text)]">{pending.original_filename}</div>
            <button
              onClick={() => setPending(null)}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-white hover:text-[var(--color-text)]"
              title="Upload a different file — this one stays until you clear it on this page"
            >
              <X size={14} />
            </button>
          </div>

          <div className="mt-3">
            <DatasetPreview dataset={pending} />
          </div>

          {/* The file is mapped and ready the moment it lands, so the only
              thing left on this page is to leave it. Offered as a link rather
              than an automatic redirect: the preview above is the reason to
              be here, and jumping away before it can be read would waste it.
              The label names the mapping rather than the section - this went
              to the right page all along, but "Continue to Labor Forecasting"
              did not tell anyone that checking the columns was what waited
              there. */}
          {pending.status === 'configured' && (
            <Link
              to={`/labor-forecasting/${LABOR_FORECASTING_HOME}`}
              className="mt-3 inline-flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white hover:bg-[var(--color-primary-dark)]"
            >
              Check the column mapping
              <ArrowRight size={14} />
            </Link>
          )}
        </div>
      )}

      {error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</div>}
      {/* Amber, not red: the file loaded and will train. These are things
          about it that will disappoint later if nobody says them now. */}
      {warnings.length > 0 && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-800">
          {warnings.map((w) => <div key={w} className="mb-1 last:mb-0">{w}</div>)}
        </div>
      )}
    </Card>
  )
}
