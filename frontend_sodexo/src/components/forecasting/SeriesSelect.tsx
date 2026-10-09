import { useEffect, useState } from 'react'
import { fetchSeriesOptions, type SeriesOption } from '../../services/api'

/** The series picker's options.
 *
 *  `scope` decides which series a page may offer: "trained" for anything that
 *  reads a forecast, "mapped" for the pages that read history and so work
 *  before a single model has been fitted. `loading` is returned because an
 *  empty list means two different things - still fetching, or nothing there -
 *  and pages that conflated them sat on "Loading data..." forever.
 */
export function useSeriesOptions(scope: 'trained' | 'mapped' = 'trained', refreshKey?: string) {
  const [options, setOptions] = useState<SeriesOption[]>([])
  const [seriesKey, setSeriesKey] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let stale = false
    setLoading(true)
    fetchSeriesOptions(scope)
      .then((opts) => {
        if (stale) return
        setOptions(opts)
        setSeriesKey((current) =>
          current && opts.some((o) => o.series_key === current) ? current : (opts[0]?.series_key ?? ''),
        )
      })
      .finally(() => !stale && setLoading(false))
    return () => {
      stale = true
    }
    // refreshKey is the section being viewed. Without it the list is fetched
    // once per scope and never again, so the sequence that matters most -
    // open the app, train, then go look at the forecast - left every page
    // after training with an empty picker and a blank body: the fetch had
    // happened before the models existed, and switching section does not
    // change the scope, so nothing ever asked again.
  }, [scope, refreshKey])

  return { options, seriesKey, setSeriesKey, loading }
}

export function SeriesSelect({ options, value, onChange }: { options: SeriesOption[]; value: string; onChange: (key: string) => void }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-1.5 text-xs text-[var(--color-text)]"
    >
      {options.map((s) => (
        <option key={s.series_key} value={s.series_key}>{s.series_label}</option>
      ))}
    </select>
  )
}
