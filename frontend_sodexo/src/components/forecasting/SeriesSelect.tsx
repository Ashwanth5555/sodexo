import { useEffect, useState } from 'react'
import { fetchSeriesOptions, type SeriesOption } from '../../services/api'

export function useSeriesOptions() {
  const [options, setOptions] = useState<SeriesOption[]>([])
  const [seriesKey, setSeriesKey] = useState('')

  useEffect(() => {
    fetchSeriesOptions().then((opts) => {
      setOptions(opts)
      if (opts.length) setSeriesKey(opts[0].series_key)
    })
  }, [])

  return { options, seriesKey, setSeriesKey }
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
