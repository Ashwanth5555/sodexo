import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { PageContainer, LoadingState, ErrorState, EmptyState } from '../components/layout/PageContainer'
import { Card, Badge } from '../components/ui/Card'
import {
  AMBER, BLUE, GREEN, LABEL, ORANGE, Panel, RED, SERIES_COLORS, StatTile, TEAL, TICK, TOOLTIP, VIOLET,
  ClearChip, type TintName,
} from '../components/ui/Dashboard'
import { fetchRecommendations, type Recommendation } from '../services/api'

const PRIORITY_BADGE: Record<string, string> = { High: 'failed', Medium: 'RUNNING', Low: 'completed' }
const PRIORITY_COLOR: Record<string, string> = { High: RED, Medium: ORANGE, Low: TEAL }
const PRIORITY_ORDER = ['High', 'Medium', 'Low']

const prettyCategory = (c: string) => c.replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase())

function RecommendationCard({ rec }: { rec: Recommendation }) {
  const spine = PRIORITY_COLOR[rec.priority] ?? '#94a3b8'
  return (
    <div className="relative overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-[var(--shadow-sm)]">
      <span className="absolute inset-y-0 left-0 w-1" style={{ background: spine }} />
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge status={PRIORITY_BADGE[rec.priority] ?? 'not_applicable'}>{rec.priority}</Badge>
        <span className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface-2)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)]">
          {prettyCategory(rec.category)}
        </span>
        <span className="text-[11px] text-[var(--color-text-muted)]">{rec.location_department}</span>
      </div>

      <h3 className="text-sm font-semibold text-[var(--color-text)]">{rec.headline}</h3>

      <div className="mt-2 space-y-2 text-xs">
        <Block label="Situation" text={rec.problem} />
        <Block label="Recommended action" text={rec.recommended_action} />
        <Block label="Expected impact" text={rec.expected_impact} />
      </div>

      {rec.evidence.length > 0 && (
        <div className="mt-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2">
          <div className="text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
            Supporting evidence
          </div>
          <ul className="mt-1 space-y-0.5">
            {rec.evidence.map((e, i) => (
              <li key={i} className="flex gap-1.5 text-[11px] text-[var(--color-text)]">
                <span className="mt-[3px] h-1 w-1 shrink-0 rounded-full" style={{ background: spine }} />
                <span>{e}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Block({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">{label}</div>
      <p className="text-[var(--color-text)]">{text}</p>
    </div>
  )
}

export default function AIRecommendations() {
  const [recs, setRecs] = useState<Recommendation[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [priorityFilter, setPriorityFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [siteFilter, setSiteFilter] = useState('')

  useEffect(() => {
    fetchRecommendations().then(setRecs).catch((err) => setError(err?.response?.data?.detail ?? String(err)))
  }, [])

  const all = recs ?? []

  // Same cross-filter convention as the other dashboards: a visual never
  // filters itself, so you can click straight from one slice to another.
  const filtered = useMemo(() => all.filter((r) =>
    (!priorityFilter || r.priority === priorityFilter) &&
    (!categoryFilter || r.category === categoryFilter) &&
    (!siteFilter || r.location_department === siteFilter)), [all, priorityFilter, categoryFilter, siteFilter])

  const tally = (rows: Recommendation[], key: (r: Recommendation) => string) => {
    const m = new Map<string, number>()
    rows.forEach((r) => m.set(key(r), (m.get(key(r)) ?? 0) + 1))
    return Array.from(m.entries()).map(([name, count]) => ({ name, count }))
  }

  const byPriority = useMemo(() => {
    const rows = tally(all.filter((r) => (!categoryFilter || r.category === categoryFilter) && (!siteFilter || r.location_department === siteFilter)), (r) => r.priority)
    return PRIORITY_ORDER.map((p) => rows.find((r) => r.name === p) ?? { name: p, count: 0 }).filter((r) => r.count > 0)
  }, [all, categoryFilter, siteFilter])

  const byCategory = useMemo(
    () => tally(all.filter((r) => (!priorityFilter || r.priority === priorityFilter) && (!siteFilter || r.location_department === siteFilter)), (r) => r.category)
      .map((r) => ({ ...r, short: prettyCategory(r.name) }))
      .sort((a, b) => b.count - a.count),
    [all, priorityFilter, siteFilter],
  )

  const bySite = useMemo(
    () => tally(filtered, (r) => r.location_department).sort((a, b) => b.count - a.count),
    [filtered],
  )

  const highCount = useMemo(() => filtered.filter((r) => r.priority === 'High').length, [filtered])
  const siteCount = useMemo(() => new Set(filtered.map((r) => r.location_department)).size, [filtered])
  const topTheme = useMemo(() => {
    const rows = tally(filtered, (r) => r.category).sort((a, b) => b.count - a.count)
    return rows.length ? prettyCategory(rows[0].name) : '—'
  }, [filtered])

  const sorted = useMemo(
    () => [...filtered].sort((a, b) => PRIORITY_ORDER.indexOf(a.priority) - PRIORITY_ORDER.indexOf(b.priority)),
    [filtered],
  )

  const toggle = (set: (v: string) => void, cur: string, v: string) => set(cur === v ? '' : v)
  const anySelection = priorityFilter || categoryFilter || siteFilter
  const selectClass = 'rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-text)]'

  const tiles: { label: string; value: string; tint: TintName; accent?: boolean }[] = [
    { label: 'Open Actions', value: String(filtered.length), tint: 'blue', accent: true },
    { label: 'High Priority', value: String(highCount), tint: 'red' },
    { label: 'Sites Affected', value: String(siteCount), tint: 'violet' },
    { label: 'Leading Category', value: topTheme, tint: 'teal' },
  ]

  return (
    <PageContainer title="AI Recommendations">
      <Card className="!py-2.5">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)} className={selectClass}>
            <option value="">All priorities</option>
            {PRIORITY_ORDER.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} className={selectClass}>
            <option value="">All categories</option>
            {Array.from(new Set(all.map((r) => r.category))).map((c) => <option key={c} value={c}>{prettyCategory(c)}</option>)}
          </select>
          <select value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)} className={selectClass}>
            <option value="">All sites</option>
            {Array.from(new Set(all.map((r) => r.location_department))).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          {priorityFilter && <ClearChip label={priorityFilter} onClear={() => setPriorityFilter('')} />}
          {categoryFilter && <ClearChip label={prettyCategory(categoryFilter)} onClear={() => setCategoryFilter('')} />}
          {siteFilter && <ClearChip label={siteFilter} onClear={() => setSiteFilter('')} />}
          {anySelection && (
            <button onClick={() => { setPriorityFilter(''); setCategoryFilter(''); setSiteFilter('') }}
              className="text-[10px] text-[var(--color-text-muted)] underline">clear all</button>
          )}
        </div>
        <p className="mt-1.5 text-[10px] text-[var(--color-text-muted)]">
          Click any column or slice to filter the page. Each action is derived from the current staffing gap,
          employee risk scores, and labor cost against budget — with the supporting figures shown on the card.
        </p>
      </Card>

      {error && <ErrorState message={error} />}
      {!recs && !error && <LoadingState />}

      {recs && !error && (all.length === 0 ? (
        <EmptyState message="No recommendations currently flagged — all monitored metrics are within tolerance." />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {tiles.map((t) => (
              <StatTile key={t.label} label={t.label} value={t.value} tint={t.tint} accent={t.accent} />
            ))}
          </div>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            <Panel
              title="By Priority" accent={RED} note="Click a ring segment to keep only that priority."
              action={priorityFilter ? <ClearChip label="filtered" onClear={() => setPriorityFilter('')} /> : undefined}
            >
              <div className="relative">
                <ResponsiveContainer width="100%" height={196}>
                  <PieChart>
                    <Pie
                      data={byPriority} dataKey="count" nameKey="name"
                      cx="50%" cy="46%" innerRadius={52} outerRadius={78} paddingAngle={2} stroke="none"
                      isAnimationActive={false} className="cursor-pointer"
                      onClick={(e: any) => e?.name && toggle(setPriorityFilter, priorityFilter, e.name)}
                    >
                      {byPriority.map((r) => (
                        <Cell key={r.name} fill={PRIORITY_COLOR[r.name] ?? '#94a3b8'}
                          opacity={priorityFilter && r.name !== priorityFilter ? 0.28 : 1} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={TOOLTIP} />
                    <Legend wrapperStyle={{ fontSize: 10, cursor: 'pointer' }}
                      onClick={(e: any) => e?.value && toggle(setPriorityFilter, priorityFilter, e.value)} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="pointer-events-none absolute inset-x-0 top-[26%] flex flex-col items-center">
                  <span className="text-base font-bold leading-none text-[var(--color-text)]">
                    {byPriority.reduce((t, r) => t + r.count, 0)}
                  </span>
                  <span className="text-[9px] uppercase tracking-wide text-[var(--color-text-muted)]">actions</span>
                </div>
              </div>
            </Panel>

            <Panel
              title="By Category" accent={ORANGE} note="Which kind of problem is generating the most actions."
              action={categoryFilter ? <ClearChip label="filtered" onClear={() => setCategoryFilter('')} /> : undefined}
            >
              <ResponsiveContainer width="100%" height={196}>
                <BarChart data={byCategory} margin={{ top: 18, right: 6, left: -14, bottom: 0 }} barCategoryGap="24%">
                  <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--color-border)" />
                  <XAxis dataKey="short" tick={{ ...TICK, fontSize: 8.5 }} tickLine={false} interval={0} angle={-22} textAnchor="end" height={46} />
                  <YAxis tick={TICK} allowDecimals={false} />
                  <Tooltip contentStyle={TOOLTIP} cursor={{ fill: 'rgba(234,88,12,0.06)' }} />
                  <Bar dataKey="count" radius={[5, 5, 0, 0]} isAnimationActive={false} className="cursor-pointer"
                    onClick={(e: any) => e?.name && toggle(setCategoryFilter, categoryFilter, e.name)}>
                    {byCategory.map((r, i) => (
                      <Cell key={r.name} fill={SERIES_COLORS[i % SERIES_COLORS.length]}
                        opacity={categoryFilter && r.name !== categoryFilter ? 0.28 : 1} />
                    ))}
                    <LabelList dataKey="count" position="top" style={LABEL} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Panel>

            <Panel
              title="Actions by Site" accent={GREEN} note="Click a column to filter to that site and department."
              action={siteFilter ? <ClearChip label="filtered" onClear={() => setSiteFilter('')} /> : undefined}
            >
              <ResponsiveContainer width="100%" height={196}>
                <BarChart data={bySite} margin={{ top: 18, right: 6, left: -14, bottom: 0 }} barCategoryGap="26%">
                  <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--color-border)" />
                  <XAxis dataKey="name" tick={{ ...TICK, fontSize: 8 }} tickLine={false} interval={0} angle={-22} textAnchor="end" height={46} />
                  <YAxis tick={TICK} allowDecimals={false} />
                  <Tooltip contentStyle={TOOLTIP} cursor={{ fill: 'rgba(22,163,74,0.06)' }} />
                  <Bar dataKey="count" radius={[5, 5, 0, 0]} isAnimationActive={false} className="cursor-pointer"
                    onClick={(e: any) => e?.name && toggle(setSiteFilter, siteFilter, e.name)}>
                    {bySite.map((r, i) => (
                      <Cell key={r.name} fill={i === 0 ? GREEN : [TEAL, BLUE, VIOLET, AMBER][i % 4]}
                        opacity={siteFilter && r.name !== siteFilter ? 0.28 : 1} />
                    ))}
                    <LabelList dataKey="count" position="top" style={LABEL} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Panel>
          </div>

          {sorted.length === 0 ? (
            <EmptyState message="Nothing matches this selection. Clear a filter to see the rest." />
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {sorted.map((r) => <RecommendationCard key={r.id} rec={r} />)}
            </div>
          )}
        </>
      ))}
    </PageContainer>
  )
}
