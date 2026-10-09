import { useEffect, useState } from 'react'
import { CheckCircle2, Loader2 } from 'lucide-react'
import { Card } from '../ui/Card'
import {
  configureDataset,
  fetchDataset,
  fetchDatasets,
  type Dataset,
  type DatasetColumn,
  type Option,
} from '../../services/api'

/** What each column is used for, and which combinations that splits the file
 *  into.
 *
 *  Mostly a statement, not a form. The date, the value being forecast, the
 *  frequency and the horizon are read from the file and checked against it on
 *  upload - the target has to be a number in every series, the frequency has
 *  to match the observed spacing - so offering them as dropdowns asked for a
 *  confirmation nobody could usefully give, and five controls stood between
 *  the reader and the one thing they came to see: the columns.
 *
 *  Grouping stays editable because it is the one choice the file cannot
 *  settle. Planned/capacity is not here at all: it is a comparison Staffing
 *  Impact draws afterwards, and sitting it beside the target implied the
 *  models read it, which is what the mapping works hardest to prevent.
 */
export function DatasetMappingCard() {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [datasetId, setDatasetId] = useState('')
  // The list endpoint omits the column profile to stay small, so the dataset
  // is fetched again on its own - without it every dropdown here is empty,
  // which is exactly how this card first shipped.
  const [detail, setDetail] = useState<Dataset | null>(null)
  const [open, setOpen] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  const [dateColumn, setDateColumn] = useState('')
  const [targetColumn, setTargetColumn] = useState('')
  const [groupColumns, setGroupColumns] = useState<string[]>([])
  // Sum or average. Editable because the two answer different questions and
  // the file cannot say which was meant: summing hourly staffing gives
  // person-hours for the day, averaging gives the typical number on shift.
  const [aggregation, setAggregation] = useState('sum')
  const [aggregations, setAggregations] = useState<Option[]>([])

  const load = () =>
    fetchDatasets()
      .then((data) => {
        setDatasets(data.datasets)
        setAggregations(data.aggregations)
        setDatasetId((current) =>
          current && data.datasets.some((d) => d.dataset_id === current)
            ? current
            : (data.datasets[0]?.dataset_id ?? ''),
        )
      })
      .catch((err) => setError(err?.response?.data?.detail ?? String(err)))

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    if (!datasetId) {
      setDetail(null)
      return
    }
    let stale = false
    fetchDataset(datasetId)
      .then((d) => {
        if (stale) return
        setDetail(d)
        setDateColumn(d.date_column ?? '')
        setTargetColumn(d.target_column ?? '')
        setGroupColumns(d.group_columns ?? [])
        setAggregation(d.aggregation ?? 'sum')
        setNotice(null)
        setWarnings([])
      })
      .catch((err) => {
        if (!stale) setError(err?.response?.data?.detail ?? String(err))
      })
    return () => {
      stale = true
    }
  }, [datasetId])

  const columns: DatasetColumn[] = detail?.column_profile ?? []
  // A dimension names a part of the business - a region, a department, a
  // season - so it is text. Offering every low-cardinality numeric column put
  // hour_of_day, month, a holiday flag and the forecast target itself up as
  // things to group by, and grouping on the target is nonsense. A column the
  // server already grouped on stays listed either way, so a numeric grouping
  // it worked out can still be switched off here.
  const groupable = columns.filter(
    (c) =>
      groupColumns.includes(c.name) ||
      (!c.is_date_like &&
        !c.is_numeric &&
        c.name !== targetColumn &&
        c.name !== dateColumn &&
        (c.distinct ?? 0) >= 2 &&
        (c.distinct ?? 0) <= 40),
  )
  // A text column naming a handful of repeating states - a season, a shift
  // pattern - is usable once expanded into one indicator column per value, so
  // it counts as an input too. Above a dozen values it is an identifier, and
  // a column with nearly as many values as rows is one however few there are.
  const isCategory = (c: DatasetColumn) =>
    !c.is_numeric &&
    !c.is_date_like &&
    (c.distinct ?? 0) >= 2 &&
    (c.distinct ?? 0) <= 12 &&
    (c.distinct ?? 0) <= Math.max(2, Math.floor((detail?.row_count ?? 0) / 10))

  // Whatever is left over is what the models read. Listing it is the point:
  // a column nobody named still ends up in the training inputs.
  const inputs = columns.filter(
    (c) =>
      (c.is_numeric || isCategory(c)) &&
      c.name !== targetColumn &&
      c.name !== dateColumn &&
      !groupColumns.includes(c.name),
  )

  const roleOf = (c: DatasetColumn): Role => {
    if (c.name === dateColumn) return 'Date'
    if (c.name === targetColumn) return 'Forecast'
    if (groupColumns.includes(c.name)) return 'Group by'
    if (c.is_numeric || isCategory(c)) return 'Training input'
    return 'Not used'
  }

  const save = async () => {
    if (!detail) return
    setBusy(true)
    setError(null)
    setNotice(null)
    setWarnings([])
    try {
      const result = await configureDataset(detail.dataset_id, {
        name: detail.name,
        date_column: dateColumn,
        target_column: targetColumn,
        group_columns: groupColumns,
        // Passed through exactly as the server worked it out. Not editable
        // here, but not discarded either - dropping it would silently switch
        // off Staffing Impact for files that do record a roster.
        capacity_column: detail.capacity_column,
        // Frequency and horizon are read from the file and checked against it,
        // so they go back as they came. Aggregation is the reader's choice.
        frequency: detail.frequency ?? 'D',
        aggregation,
        horizon_periods: detail.horizon_periods,
        exog_columns: inputs.map((c) => c.name),
      })
      setWarnings(result.warnings ?? [])
      setNotice(`Saved - ${result.series_count} combinations. Run Model Training to use it.`)
      await load()
      setDetail(await fetchDataset(detail.dataset_id))
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? String(err))
      if (err?.response?.status === 404) {
        setDatasetId('')
        await load()
      }
    } finally {
      setBusy(false)
    }
  }

  if (datasets.length === 0) {
    return (
      <Card>
        <h2 className="text-sm font-semibold text-[var(--color-text)]">Column mapping</h2>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          Nothing uploaded yet. Add a CSV or Excel file on the Data Quality page and its columns will be mapped here.
        </p>
      </Card>
    )
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-[var(--color-text)]">Column mapping</h2>
          <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
            {detail?.target_column ? (
              <>
                Forecasting <span className="font-medium">{detail.target_column}</span> by{' '}
                <span className="font-medium">{detail.date_column}</span>
                {detail.group_columns.length > 0 && (
                  <> · one per <span className="font-medium">{detail.group_columns.join(' + ')}</span></>
                )}
              </>
            ) : (
              'This file has no usable mapping yet.'
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {datasets.length > 1 && (
            <select
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
              className="rounded-lg border border-[var(--color-border)] bg-white px-2 py-1 text-xs"
            >
              {datasets.map((d) => (
                <option key={d.dataset_id} value={d.dataset_id}>{d.name}</option>
              ))}
            </select>
          )}
          <button
            onClick={() => setOpen((o) => !o)}
            className="rounded-lg border border-[var(--color-border)] bg-white px-3 py-1.5 text-[11px] font-medium text-[var(--color-text-muted)] hover:border-[var(--color-primary)] hover:text-[var(--color-text)]"
          >
            {open ? 'Hide' : 'Change'}
          </button>
        </div>
      </div>

      {open && !detail && (
        <div className="mt-3 text-xs text-[var(--color-text-muted)]">Reading columns…</div>
      )}

      {open && detail && (
        <div className="mt-3 border-t border-[var(--color-border)] pt-3">
          {/* Sum or average is not a detail. On hourly staffing, summing a day
              gives person-hours and averaging gives the number on shift -
              different questions, and the file cannot say which was meant. It
              sits above the columns because it changes what every figure in
              the product means. */}
          <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-3">
            <div className="min-w-0">
              <div className="text-[11px] font-medium text-[var(--color-text)]">Combine rows in the same period by</div>
              <div className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
                Rows are rolled up to {(detail.frequency && FREQUENCY_WORD[detail.frequency]) || 'each'} periods before
                training. {aggregation === 'sum'
                  ? `Summing gives the total ${targetColumn || 'value'} across the period.`
                  : `Averaging gives the typical ${targetColumn || 'value'} within the period.`}
              </div>
            </div>
            <select
              value={aggregation}
              onChange={(e) => setAggregation(e.target.value)}
              className="ml-auto rounded-lg border border-[var(--color-border)] bg-white px-2.5 py-1.5 text-xs text-[var(--color-text)]"
            >
              {(aggregations.length ? aggregations : [{ value: 'sum', label: 'Sum' }, { value: 'mean', label: 'Average' }]).map((a) => (
                <option key={a.value} value={a.value}>{a.label}</option>
              ))}
            </select>
          </div>

          {/* The columns, and what each one is for. Date, forecast, frequency
              and horizon were dropdowns here; they are worked out from the file
              and checked against it on upload, so presenting them as decisions
              asked for confirmation nobody could usefully give. Grouping stays
              editable because it is the one the file cannot settle. */}
          <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-white">
            <table className="min-w-full border-collapse text-[11px]">
              <thead>
                <tr className="bg-[var(--color-surface-2)] text-left">
                  <th className="border-b border-[var(--color-border)] px-3 py-1.5 font-semibold text-[var(--color-text)]">Column</th>
                  <th className="border-b border-[var(--color-border)] px-3 py-1.5 font-semibold text-[var(--color-text)]">Holds</th>
                  <th className="border-b border-[var(--color-border)] px-3 py-1.5 font-semibold text-[var(--color-text)]">Used as</th>
                </tr>
              </thead>
              <tbody>
                {columns.map((c) => {
                  const role = roleOf(c)
                  return (
                    <tr key={c.name} className="even:bg-[var(--color-surface-2)]/40">
                      <td className="border-b border-[var(--color-border)] px-3 py-1.5 font-medium text-[var(--color-text)]">
                        {c.name}
                      </td>
                      <td className="max-w-[18rem] truncate border-b border-[var(--color-border)] px-3 py-1.5 text-[var(--color-text-muted)]">
                        {c.samples.slice(0, 3).join(', ') || '—'}
                      </td>
                      <td className="border-b border-[var(--color-border)] px-3 py-1.5">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${ROLE_STYLES[role]}`}>
                          {role}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* The grouping gets its own block rather than a cell in the grid.
              It is the decision that multiplies everything downstream, and the
              number of series it produces is what people get wrong. */}
          <div className="mt-4 border-t border-[var(--color-border)] pt-3">
            <div className="text-[11px] font-medium text-[var(--color-text)]">Group by</div>
            <div className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
              Each combination of these columns is trained as its own forecast. The number beside a column is how many
              distinct values it has.
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {groupable.length === 0 && (
                <span className="text-[10px] text-[var(--color-text-muted)]">
                  No column in this file repeats often enough to split it into series.
                </span>
              )}
              {groupable.map((c) => {
                const on = groupColumns.includes(c.name)
                return (
                  <button
                    key={c.name}
                    type="button"
                    onClick={() => setGroupColumns((g) => (on ? g.filter((x) => x !== c.name) : [...g, c.name]))}
                    className={`rounded-full border px-2.5 py-1 text-[10px] transition-colors ${
                      on
                        ? 'border-[var(--color-primary)] bg-[var(--color-primary)] text-white'
                        : 'border-[var(--color-border)] bg-white text-[var(--color-text-muted)] hover:border-[var(--color-primary)]'
                    }`}
                  >
                    {c.name}
                    <span className="ml-1 opacity-60">{c.distinct}</span>
                  </button>
                )
              })}
            </div>
            <div className="mt-1.5 text-[10px] text-[var(--color-text-muted)]">
              {groupColumns.length === 0
                ? 'One forecast for the whole file.'
                : `One forecast per combination of ${groupColumns.join(' + ')}.`}
            </div>
          </div>

          {/* The combinations that will actually train, named. Read from the
              file rather than multiplied out, so it shows the pairings that
              occur - and it reflects the SAVED mapping, not unsaved chips. */}
          {detail.series.length > 0 && (
            <div className="mt-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] p-2.5">
              <div className="text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                {detail.series.length} combination{detail.series.length > 1 ? 's' : ''} will be trained
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {detail.series.map((s) => (
                  <span
                    key={s.series_key}
                    className="rounded border border-[var(--color-border)] bg-white px-2 py-0.5 text-[10px] text-[var(--color-text)]"
                  >
                    {s.series_label.replace(`${detail.name} / `, '')}
                  </span>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={save}
            disabled={!dateColumn || !targetColumn || busy}
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white hover:bg-[var(--color-primary-dark)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
            {busy ? 'Checking…' : 'Save mapping'}
          </button>
        </div>
      )}

      {error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</div>}
      {notice && (
        <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-700">
          {notice}
        </div>
      )}
      {warnings.length > 0 && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-800">
          {warnings.map((w) => <div key={w} className="mb-1 last:mb-0">{w}</div>)}
        </div>
      )}
    </Card>
  )
}

const FREQUENCY_WORD: Record<string, string> = {
  H: 'hourly', D: 'daily', W: 'weekly', MS: 'monthly',
}

type Role = 'Date' | 'Forecast' | 'Group by' | 'Training input' | 'Not used'

const ROLE_STYLES: Record<Role, string> = {
  Date: 'bg-blue-50 text-blue-700',
  Forecast: 'bg-emerald-50 text-emerald-700',
  'Group by': 'bg-[var(--color-primary)]/10 text-[var(--color-primary)]',
  'Training input': 'bg-[var(--color-surface-2)] text-[var(--color-text-muted)]',
  'Not used': 'bg-[var(--color-surface-2)] text-[var(--color-text-muted)]/70',
}
