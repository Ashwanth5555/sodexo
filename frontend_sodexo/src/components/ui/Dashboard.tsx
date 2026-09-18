/**
 * Shared building blocks for the dashboard-style pages (Labor Analytics, Time
 * Punch Analytics, AI Recommendations).
 *
 * These three pages had drifted into keeping their own copies of the same tile
 * and panel, which is how one page ends up with a different corner radius or a
 * different blue. One definition, three importers.
 */
import type { ReactNode } from 'react'
import { Area, AreaChart, ResponsiveContainer } from 'recharts'
import { Card } from './Card'

export const BLUE = '#2563eb'
export const TEAL = '#0d9488'
export const ORANGE = '#ea580c'
export const VIOLET = '#7c3aed'
export const GREEN = '#16a34a'
export const AMBER = '#ca8a04'
export const RED = '#dc2626'
export const SERIES_COLORS = [BLUE, TEAL, ORANGE, VIOLET, AMBER, GREEN, RED, '#0369a1']

export const TICK = { fontSize: 10, fill: 'var(--color-text-muted)' }
export const TOOLTIP = { background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 12 }
export const LABEL = { fontSize: 10, fill: 'var(--color-text-muted)', fontWeight: 600 }

export type TintName = 'blue' | 'orange' | 'green' | 'teal' | 'violet' | 'amber' | 'red'

/** Soft tinted tile backgrounds, one per metric family, so the eye can find a
 *  metric without reading every label. */
export const TINTS: Record<TintName, { bg: string; line: string; fill: string }> = {
  blue: { bg: '#eff6ff', line: BLUE, fill: 'rgba(37,99,235,0.16)' },
  orange: { bg: '#fff7ed', line: ORANGE, fill: 'rgba(234,88,12,0.16)' },
  green: { bg: '#f0fdf4', line: GREEN, fill: 'rgba(22,163,74,0.16)' },
  teal: { bg: '#f0fdfa', line: TEAL, fill: 'rgba(13,148,136,0.16)' },
  violet: { bg: '#f5f3ff', line: VIOLET, fill: 'rgba(124,58,237,0.16)' },
  amber: { bg: '#fefce8', line: AMBER, fill: 'rgba(202,138,4,0.16)' },
  red: { bg: '#fef2f2', line: RED, fill: 'rgba(220,38,38,0.14)' },
}

/**
 * KPI tile: one label, one number, and a sparkline of that number's own trend.
 *
 * Nothing else. Period-over-period deltas and secondary figures used to sit
 * here and made the strip busy; the trends and breakdowns below the tiles carry
 * that detail. `kpi_deltas` is still returned by the API for anyone who wants
 * it back.
 */
export function StatTile({
  label, value, spark, sparkKey, tint = 'teal', accent = false,
}: {
  label: string
  value: string
  spark?: any[]
  sparkKey?: string
  tint?: TintName
  accent?: boolean
}) {
  const t = TINTS[tint]
  const stroke = accent ? 'rgba(255,255,255,0.9)' : t.line
  const fill = accent ? 'rgba(255,255,255,0.28)' : t.fill

  return (
    <div
      className={`relative flex flex-col overflow-hidden rounded-xl border p-3 shadow-[var(--shadow-sm)] ${accent ? 'border-transparent' : 'border-[var(--color-border)]'}`}
      style={accent ? { background: `linear-gradient(135deg,${BLUE} 0%,#1e3a8a 100%)` } : { background: t.bg }}
    >
      <span className={`text-[10px] font-semibold uppercase tracking-[0.08em] ${accent ? 'text-white/70' : 'text-[var(--color-text-muted)]'}`}>
        {label}
      </span>
      <span className={`mt-1 text-2xl font-bold leading-none ${accent ? 'text-white' : 'text-[var(--color-text)]'}`}>{value}</span>
      {spark && sparkKey && spark.length > 1 && (
        <div className="-mx-3 -mb-3 mt-3 h-11">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={spark} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <Area type="monotone" dataKey={sparkKey} stroke={stroke} fill={fill} strokeWidth={1.5} dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}

/** Chart panel: colour spine, uppercase title, optional note and action slot. */
export function Panel({
  title, note, accent = BLUE, action, children, className = '',
}: {
  title: string
  note?: string
  accent?: string
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={className}>
      <div className="mb-1 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="h-3.5 w-[3px] rounded-full" style={{ background: accent }} />
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text)]">{title}</h3>
        </div>
        {action}
      </div>
      {note ? <p className="mb-2 pl-[11px] text-[10px] text-[var(--color-text-muted)]">{note}</p> : <div className="mb-2" />}
      {children}
    </Card>
  )
}

/** Removable filter pill. Most filters get set by clicking a chart, so there
 *  has to be a visible way to take one back off. */
export function ClearChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <button
      onClick={onClear}
      className="rounded-full border border-[var(--color-primary)] bg-[var(--color-primary)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--color-primary)]"
      title="Clear this filter"
    >
      {label} ✕
    </button>
  )
}

export function MiniTable({ rows }: { rows: { label: string; value: string }[] }) {
  return (
    <div className="flex flex-col gap-1 text-xs">
      {rows.map((r) => (
        <div key={r.label} className="flex justify-between border-b border-[var(--color-border)] py-1 last:border-0">
          <span className="text-[var(--color-text-muted)]">{r.label}</span>
          <span className="font-medium text-[var(--color-text)]">{r.value}</span>
        </div>
      ))}
    </div>
  )
}

export const money = (n: number) =>
  n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(2)}M` : n >= 1000 ? `$${Math.round(n / 1000)}K` : `$${Math.round(n)}`
export const num = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : `${Math.round(n)}`)
