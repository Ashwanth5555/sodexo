/**
 * Renders whichever chart the assistant's `facts` produced for the question
 * just asked. Reuses the same style tokens the dashboard pages use
 * (components/ui/Dashboard.tsx) so a chat chart looks like part of the app,
 * not a bolted-on extra.
 */
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { SERIES_COLORS, TICK, TOOLTIP } from '../ui/Dashboard'
import type { AssistantChart } from '../../services/api'

const fmtNumber = (v: unknown) => (typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(1)) : String(v ?? ''))

export function ChatChart({ chart }: { chart: AssistantChart }) {
  return (
    <div className="mt-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2.5">
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
        {chart.title}
      </div>
      <ResponsiveContainer width="100%" height={180}>
        {chart.type === 'line' ? (
          <LineChart data={chart.data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--color-border)" />
            <XAxis dataKey={chart.x_key ?? 'date'} tick={{ ...TICK, fontSize: 9 }} tickLine={false} minTickGap={24} />
            <YAxis tick={TICK} width={36} />
            <Tooltip formatter={(v: any) => fmtNumber(v)} contentStyle={TOOLTIP} />
            {chart.series.map((s, i) => (
              <Line
                key={s.key} type="monotone" dataKey={s.key} name={s.label}
                stroke={s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]}
                strokeWidth={1.75} dot={false} isAnimationActive={false}
              />
            ))}
          </LineChart>
        ) : chart.type === 'bar' ? (
          // Below ~15 categories (issue types, employees, series names) every
          // label fits angled; above that (a daily trend redrawn as bars) all
          // 90-odd labels would collide, so fall back to an auto-thinned axis
          // the same way the line chart's does.
          chart.data.length <= 15 ? (
            <BarChart data={chart.data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }} barCategoryGap="28%">
              <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--color-border)" />
              <XAxis dataKey={chart.x_key ?? 'name'} tick={{ ...TICK, fontSize: 8 }} tickLine={false} interval={0} angle={-20} textAnchor="end" height={38} />
              <YAxis tick={TICK} width={32} />
              <Tooltip formatter={(v: any) => fmtNumber(v)} contentStyle={TOOLTIP} />
              {chart.series.map((s, i) => (
                <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
              ))}
            </BarChart>
          ) : (
            <BarChart data={chart.data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--color-border)" />
              <XAxis dataKey={chart.x_key ?? 'name'} tick={{ ...TICK, fontSize: 9 }} tickLine={false} minTickGap={24} />
              <YAxis tick={TICK} width={32} />
              <Tooltip formatter={(v: any) => fmtNumber(v)} contentStyle={TOOLTIP} />
              {chart.series.map((s, i) => (
                <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]} isAnimationActive={false} />
              ))}
            </BarChart>
          )
        ) : (
          <PieChart>
            <Pie data={chart.data} dataKey={chart.series[0]?.key ?? 'value'} nameKey="name" cx="50%" cy="50%" innerRadius={40} outerRadius={68} paddingAngle={2} stroke="none" isAnimationActive={false}>
              {chart.data.map((_, i) => <Cell key={i} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />)}
            </Pie>
            <Tooltip formatter={(v: any) => fmtNumber(v)} contentStyle={TOOLTIP} />
          </PieChart>
        )}
      </ResponsiveContainer>
    </div>
  )
}
