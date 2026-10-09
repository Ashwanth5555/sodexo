import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Download, FileQuestion } from 'lucide-react'
import { fetchUploadRequirements, templateDownloadUrl, type UploadRequirements } from '../../services/api'

/** "What can I upload?" — answered from the server's own limits.
 *
 *  Every number here is fetched rather than written into the component. A
 *  limit hardcoded in the UI drifts the moment someone changes the one the
 *  code enforces, and a stated limit that turns out to be wrong is worse
 *  than none: the user believes it, hits the real one, and the error
 *  contradicts the page they just read.
 *
 *  Collapsed by default. Someone who already knows their file is fine
 *  should not have to scroll past an explanation to reach the drop zone.
 */
export function UploadGuidance() {
  const [open, setOpen] = useState(false)
  const [reqs, setReqs] = useState<UploadRequirements | null>(null)

  useEffect(() => {
    // Only fetched once expanded - the drop zone works without it, so this
    // should not be a request every visitor pays for.
    if (open && !reqs) fetchUploadRequirements().then(setReqs).catch(() => setReqs(null))
  }, [open, reqs])

  return (
    <div className="mt-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)]">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 text-[11px] font-semibold text-[var(--color-text)]"
        >
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <FileQuestion size={13} />
          What can I upload?
        </button>
        <a
          href={templateDownloadUrl()}
          download
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-white px-2.5 py-1.5 text-[10px] font-semibold text-[var(--color-text)] hover:border-[var(--color-primary)]"
        >
          <Download size={11} />
          Download example file
        </a>
      </div>

      {open && (
        <div className="border-t border-[var(--color-border)] px-3 py-3">
          {!reqs ? (
            <p className="text-[10px] text-[var(--color-text-muted)]">Loading…</p>
          ) : (
            <>
              <p className="text-[11px] font-medium text-[var(--color-text)]">
                Only two columns are required: a date, and a number to forecast. Everything else is
                optional and unlocks more of the application.
              </p>

              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-left text-[10px]">
                  <thead>
                    <tr className="text-[var(--color-text-muted)]">
                      <th className="pb-1 pr-3">Example column</th>
                      <th className="pb-1 pr-3">Role</th>
                      <th className="pb-1">What it is for</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reqs.columns.map((c) => (
                      <tr key={c.name} className="border-t border-[var(--color-border)]">
                        <td className="py-1 pr-3 font-mono text-[var(--color-text)]">{c.name}</td>
                        <td className="whitespace-nowrap py-1 pr-3">
                          <span className={c.required ? 'font-semibold text-[var(--color-primary)]' : 'text-[var(--color-text-muted)]'}>
                            {c.role_label}
                          </span>
                        </td>
                        <td className="py-1 text-[var(--color-text-muted)]">{c.purpose}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p className="mt-2 text-[10px] text-[var(--color-text-muted)]">
                Those names are only an example — yours can be anything.
              </p>

              {/* Combinations are the part people misjudge: two grouping
                  columns is not two forecasts, it is one per pairing. */}
              <div className="mt-3 rounded border border-[var(--color-border)] bg-white p-2">
                <div className="text-[10px] font-semibold text-[var(--color-text)]">
                  Splitting into several forecasts
                </div>
                <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
                  Pick one or more grouping columns and each <span className="font-medium">combination
                  that appears in your data</span> becomes its own forecast, with its own models and its
                  own champion. The example file has {reqs.example_combination}.
                </p>
                <p className="mt-1 text-[10px] text-[var(--color-text-muted)]">
                  Add <span className="font-mono">site</span> as a third level and it multiplies again — up
                  to {reqs.max_series} series per file. Only pairings that actually occur are created, so a
                  department that exists in one region only does not produce an empty forecast elsewhere.
                </p>
              </div>

              <div className="mt-3 grid gap-x-6 gap-y-1 text-[10px] text-[var(--color-text-muted)] sm:grid-cols-2">
                <div>
                  <span className="font-medium text-[var(--color-text)]">File types</span>{' '}
                  {reqs.accepted_extensions.join(' · ')}
                </div>
                <div>
                  <span className="font-medium text-[var(--color-text)]">Size</span> up to {reqs.max_file_mb} MB,{' '}
                  {reqs.max_rows.toLocaleString()} rows
                </div>
                <div>
                  <span className="font-medium text-[var(--color-text)]">History</span> at least{' '}
                  {reqs.min_rows_per_series} periods per series
                </div>
                <div>
                  <span className="font-medium text-[var(--color-text)]">Frequencies</span>{' '}
                  {reqs.frequencies.map((f) => f.label).join(' · ')}
                </div>
              </div>

              <ul className="mt-3 list-inside list-disc space-y-0.5 text-[10px] text-[var(--color-text-muted)]">
                {reqs.notes.map((n) => <li key={n}>{n}</li>)}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  )
}
