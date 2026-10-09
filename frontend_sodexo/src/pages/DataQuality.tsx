import { useCallback, useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { PageContainer, ErrorState } from '../components/layout/PageContainer'
import { DatasetUpload } from '../components/dataquality/DatasetUpload'
import { deleteDataset, fetchDatasets, type Dataset } from '../services/api'

/** Upload a file, see what came through, train it.
 *
 *  There used to be a card per uploaded file below this, repeating the row
 *  count and the quality the upload panel had just shown and carrying the
 *  mapping as a sentence. It restated the panel above it, and went stale the
 *  moment a file was re-uploaded. The upload panel is the whole page now.
 *
 *  Mapping, analysis and training all happen under Labor Forecasting. What is
 *  left here is Clear all, which is the only way to drop a file without
 *  restarting the server - and which treats an already-deleted file as
 *  success, because a tab left open across a restart is holding ids the
 *  server has forgotten.
 */
export default function DataQuality() {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [error, setError] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)
  const [cleared, setCleared] = useState(0)

  const load = useCallback(
    () =>
      fetchDatasets()
        .then((d) => setDatasets(d.datasets))
        .catch((err) => setError(err?.response?.data?.detail ?? String(err))),
    [],
  )

  useEffect(() => {
    load()
  }, [load])

  const clearAll = async () => {
    setClearing(true)
    setError(null)
    try {
      // A 404 means it is already gone, which is what was asked for. This is
      // routine rather than exceptional: the POC wipes uploads on restart, so
      // any tab left open is holding ids the server has since forgotten, and
      // reporting that as a failure makes a successful clear look broken.
      await Promise.all(
        datasets.map((d) =>
          deleteDataset(d.dataset_id).catch((err) => {
            if (err?.response?.status !== 404) throw err
          }),
        ),
      )
      // Remount the upload panel so the preview of a file that no longer
      // exists goes with it.
      setCleared((c) => c + 1)
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    } finally {
      // Always, even after a failure: the footer count has to reflect what
      // the server actually holds, not what it held before the attempt.
      await load()
      setClearing(false)
    }
  }

  return (
    <PageContainer
      title="Data Quality"
      subtitle="Upload a file to forecast it. You will see what came through, then check how its columns were read."
    >
      <DatasetUpload key={cleared} onChanged={load} />

      {error && <ErrorState message={error} />}

      {datasets.length > 0 && (
        <div className="flex items-center justify-between px-1 text-[11px] text-[var(--color-text-muted)]">
          <span>
            {datasets.length} file{datasets.length > 1 ? 's' : ''} uploaded
          </span>
          <button
            onClick={clearAll}
            disabled={clearing}
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 size={11} />
            {clearing ? 'Clearing…' : 'Clear all'}
          </button>
        </div>
      )}
    </PageContainer>
  )
}
