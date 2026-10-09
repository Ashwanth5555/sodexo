import type { DatasetColumn, DatasetUploadResponse } from '../../services/api'

/** What the uploaded file actually contains: the first rows laid out the way
 *  they look in Excel, and a short read on whether the sheet is clean.
 *
 *  This is the first thing shown after an upload because it answers the
 *  question people actually have at that moment - "did it read my file
 *  correctly?" - which a form full of dropdowns does not.
 */
export function DatasetPreview({ dataset }: { dataset: DatasetUploadResponse }) {
  const columns: DatasetColumn[] = dataset.column_profile ?? []
  const rows = dataset.preview ?? []
  const names = columns.map((c) => c.name)

  // One number for "is this sheet full of holes". Averaging per-column blank
  // rates rather than counting cells keeps one very empty column visible
  // instead of diluting it across a wide file.
  const blankPct = columns.length
    ? columns.reduce((sum, c) => sum + c.null_pct, 0) / columns.length
    : 0
  const worst = [...columns].sort((a, b) => b.null_pct - a.null_pct)[0]

  // Pandas hands back full ISO timestamps. A spreadsheet view showing
  // "2024-08-01T00:00:00" in every date cell reads as machine output, so the
  // time is dropped on columns where every row sits at midnight.
  const timed = new Set(
    names.filter((name) =>
      rows.some((row) => {
        const m = ISO.exec(String(row[name] ?? ''))
        return m !== null && m[2] !== '00:00:00'
      }),
    ),
  )
  const display = (name: string, value: unknown) => {
    const text = String(value)
    const m = ISO.exec(text)
    if (!m) return text
    return timed.has(name) ? `${m[1]} ${m[2].slice(0, 5)}` : m[1]
  }

  const quality =
    blankPct >= 20 ? 'Poor' : blankPct >= 5 ? 'Check it' : 'Good'
  const tone =
    quality === 'Good'
      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
      : quality === 'Check it'
        ? 'bg-amber-50 text-amber-800 border-amber-200'
        : 'bg-red-50 text-red-700 border-red-200'

  return (
    <div>
      {/* Overview - the file as a spreadsheet. */}
      <div className="flex items-center justify-between gap-3">
        <div className="text-[11px] font-semibold text-[var(--color-text)]">
          Your data
        </div>
        <div className="text-[10px] text-[var(--color-text-muted)]">
          {dataset.row_count.toLocaleString()} rows · {names.length} columns · first{' '}
          {rows.length} shown
        </div>
      </div>

      <div className="mt-1.5 overflow-x-auto rounded-lg border border-[var(--color-border)] bg-white">
        <table className="min-w-full border-collapse text-[10px]">
          <thead>
            <tr className="bg-[var(--color-surface-2)]">
              {names.map((name) => (
                <th
                  key={name}
                  className="whitespace-nowrap border-b border-r border-[var(--color-border)] px-2 py-1.5 text-left font-semibold text-[var(--color-text)] last:border-r-0"
                >
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="even:bg-[var(--color-surface-2)]/40">
                {names.map((name) => {
                  const value = row[name]
                  const blank = value === null || value === undefined || value === ''
                  return (
                    <td
                      key={name}
                      className="whitespace-nowrap border-b border-r border-[var(--color-border)] px-2 py-1 text-[var(--color-text-muted)] last:border-r-0"
                    >
                      {blank ? (
                        <span className="text-[var(--color-text-muted)]/50">—</span>
                      ) : (
                        display(name, value)
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Quality - three plain facts, not a report. */}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <span
          className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${tone}`}
        >
          {quality}
        </span>
        <span className="text-[10px] text-[var(--color-text-muted)]">
          {blankPct < 0.5
            ? 'No empty cells'
            : `${blankPct.toFixed(0)}% of cells empty${
                worst && worst.null_pct >= 20 ? ` · mostly in ${worst.name}` : ''
              }`}
        </span>
      </div>

      {/* Anything we changed to make the file readable. People need to know
          when a total row was dropped or a cross-tab was reshaped. */}
      {dataset.cleaning_notes?.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {dataset.cleaning_notes.map((note) => (
            <li
              key={note}
              className="flex gap-1.5 text-[10px] leading-relaxed text-[var(--color-text-muted)]"
            >
              <span className="text-[var(--color-primary)]">•</span>
              <span>{note}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

const ISO = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/
