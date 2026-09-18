import { useEffect, useState } from 'react'
import { PageContainer, LoadingState, ErrorState } from '../components/layout/PageContainer'
import { Card, KpiCard, Badge } from '../components/ui/Card'
import { DatasetUpload } from '../components/dataquality/DatasetUpload'
import { fetchDataQualitySummary, runIngestion, type DataQualitySummary } from '../services/api'

const STATUS_BADGE: Record<string, string> = { Good: 'completed', Warning: 'RUNNING', Poor: 'failed', 'No Data': 'not_applicable' }

export default function DataQuality() {
  const [data, setData] = useState<DataQualitySummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  const load = () => fetchDataQualitySummary().then(setData).catch((err) => setError(err?.response?.data?.detail ?? String(err)))

  useEffect(() => {
    load()
  }, [])

  const handleRun = async () => {
    setRunning(true)
    setError(null)
    try {
      await runIngestion()
      await load()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
    } finally {
      setRunning(false)
    }
  }

  return (
    <PageContainer title="Data Quality" subtitle="Source health for every enterprise system feeding this application - UKG, Workday, POS, Payroll, ERP, External.">
      <Card>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">Sync all sources</h2>
          <button onClick={handleRun} disabled={running} className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white hover:bg-[var(--color-primary-dark)] disabled:opacity-50">
            {running ? 'Running…' : 'Run Ingestion'}
          </button>
        </div>
      </Card>

      <DatasetUpload />

      {error && <ErrorState message={error} />}
      {!data ? <LoadingState /> : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KpiCard label="Sources" value={String(data.summary.total_sources)} />
            <KpiCard label="Records Received" value={data.summary.total_records_received.toLocaleString()} />
            <KpiCard label="Records Rejected" value={data.summary.total_records_rejected.toLocaleString()} />
            <KpiCard label="Overall Quality" value={data.summary.overall_quality_status} />
          </div>
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-[var(--color-text-muted)]">
                    <th className="pb-2">Source System</th>
                    <th className="pb-2">Dataset</th>
                    <th className="pb-2">Latest Sync</th>
                    <th className="pb-2 text-right">Received</th>
                    <th className="pb-2 text-right">Loaded</th>
                    <th className="pb-2 text-right">Rejected</th>
                    <th className="pb-2">Freshness</th>
                    <th className="pb-2">Quality</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sources.map((s) => (
                    <tr key={s.source_endpoint} className="border-t border-[var(--color-border)]">
                      <td className="py-2 text-[var(--color-text)]">{s.source_system}</td>
                      <td className="py-2 text-[var(--color-text-muted)]">{s.dataset_label}</td>
                      <td className="py-2 text-[var(--color-text-muted)]">{s.completed_at?.slice(0, 19) ?? '—'}</td>
                      <td className="py-2 text-right text-[var(--color-text)]">{s.records_received.toLocaleString()}</td>
                      <td className="py-2 text-right text-[var(--color-text)]">{s.records_loaded.toLocaleString()}</td>
                      <td className="py-2 text-right">{s.records_rejected > 0 ? <span className="text-red-600">{s.records_rejected}</span> : s.records_rejected}</td>
                      <td className="py-2 text-[var(--color-text-muted)]">{s.freshness_hours != null ? `${s.freshness_hours}h ago` : '—'}</td>
                      <td className="py-2"><Badge status={STATUS_BADGE[s.quality_status] ?? 'not_applicable'}>{s.quality_status}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </PageContainer>
  )
}
