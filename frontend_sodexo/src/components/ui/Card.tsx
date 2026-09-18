import type { ReactNode } from 'react'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-[var(--shadow-sm)] ${className}`}>{children}</div>
}

type KpiTone = 'default' | 'good' | 'warn' | 'bad'

/** Accent pairs per tone: `spine` is the soft bar down the left edge, `value`
 *  is the darker shade used for the number so it stays readable on the light
 *  tile. Splitting them lets the spine be gentle without hurting contrast. */
const KPI_ACCENT: Record<KpiTone, { spine: string; value: string }> = {
  default: { spine: '#e2703a', value: 'var(--color-text)' },
  good: { spine: '#3f9d63', value: '#15803d' },
  warn: { spine: '#d1a03c', value: '#a16207' },
  bad: { spine: '#d4635e', value: '#b91c1c' },
}

/** A KPI tile: one flat light background shared by every tile, with a softly
 *  coloured spine on the left. `tone` colours the spine and the value when a
 *  number is genuinely good or bad. */
export function KpiCard({
  label, value, sublabel, tone = 'default',
}: {
  label: string
  value: string
  sublabel?: string
  tone?: KpiTone
}) {
  const accent = KPI_ACCENT[tone]
  return (
    <div className="relative flex flex-col gap-1 overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-kpi-bg)] py-4 pl-5 pr-4 shadow-[var(--shadow-sm)] transition-shadow hover:shadow-[var(--shadow-md)]">
      <span className="absolute inset-y-0 left-0 w-1 rounded-r-sm" style={{ background: accent.spine }} />
      <span className="text-xs uppercase tracking-wide text-[var(--color-text-muted)]">{label}</span>
      <span className="text-2xl font-semibold leading-tight" style={{ color: accent.value }}>{value}</span>
      {sublabel && <span className="text-xs text-[var(--color-text-muted)]">{sublabel}</span>}
    </div>
  )
}

const BADGE_STYLES: Record<string, string> = {
  completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  failed: 'bg-red-50 text-red-700 border-red-200',
  not_applicable: 'bg-stone-100 text-stone-500 border-stone-200',
  pending: 'bg-stone-50 text-stone-400 border-stone-200',
  RUNNING: 'bg-amber-50 text-amber-700 border-amber-200',
  COMPLETED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  FAILED: 'bg-red-50 text-red-700 border-red-200',
  champion: 'bg-[var(--color-accent)]/10 text-[var(--color-accent)] border-[var(--color-accent)]/40',
}

export function Badge({ status, children }: { status: string; children: ReactNode }) {
  const style = BADGE_STYLES[status] ?? 'bg-stone-100 text-stone-600 border-stone-200'
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${style}`}>{children}</span>
}
