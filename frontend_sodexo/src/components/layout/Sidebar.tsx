import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { ChevronDown, ChevronRight, TrendingUp, Sparkles, MessageSquare, Database } from 'lucide-react'
import logo from '../../assets/sodexo-logo.png'

const NAV_ITEMS = [
  { to: '/data-quality', label: 'Data Quality', icon: Database },
]

const LABOR_FORECASTING_SECTIONS = [
  { slug: 'demand-drivers', label: 'Demand Drivers & Feature Mapping' },
  { slug: 'data-analysis', label: 'Data Analysis' },
  { slug: 'data-preprocessing', label: 'Data Pre-processing' },
  { slug: 'model-training', label: 'Model Training' },
  { slug: 'forecast-explorer', label: 'Forecast Explorer' },
  { slug: 'drift-monitoring', label: 'Drift Monitoring' },
  { slug: 'staffing-impact', label: 'Staffing Impact' },
  { slug: 'model-performance', label: 'Model Performance' },
]

const REST_NAV_ITEMS = [
  { to: '/ai-recommendations', label: 'AI Recommendations', icon: Sparkles },
  { to: '/ai-assistant', label: 'AI Assistant', icon: MessageSquare },
]

function NavItem({ to, label, Icon }: { to: string; label: string; Icon: any }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `mb-1 flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
          isActive
            ? 'bg-[var(--color-primary)] text-white'
            : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]'
        }`
      }
    >
      <Icon size={16} />
      {label}
    </NavLink>
  )
}

export function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const isForecastingActive = location.pathname.startsWith('/labor-forecasting')

  return (
    <aside className="flex h-full w-64 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="border-b border-[var(--color-border)] px-5 py-5">
        <div className="flex items-center gap-2 rounded-xl border border-[var(--color-border)] bg-white px-3 py-2.5 shadow-[var(--shadow-sm)]">
          <img src={logo} alt="Sodexo" className="h-7 shrink-0" />
          <div className="min-w-0 border-l border-[var(--color-border)] pl-2">
            <div className="text-sm font-bold leading-tight tracking-tight text-[var(--color-text)]">Healthcare</div>
            <div className="mt-0.5 text-[9px] font-semibold uppercase leading-snug tracking-[0.03em] text-[var(--color-primary)]">Labor Forecasting &amp; Workforce Intelligence</div>
          </div>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-2">
        {NAV_ITEMS.map((item) => (
          <NavItem key={item.to} to={item.to} label={item.label} Icon={item.icon} />
        ))}

        <button
          onClick={() => navigate(isForecastingActive ? location.pathname : '/labor-forecasting/model-training')}
          className={`mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
            isForecastingActive ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]'
          }`}
        >
          <TrendingUp size={16} />
          <span className="flex-1">Labor Forecasting</span>
          {isForecastingActive ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        {isForecastingActive && (
          <div className="mb-1 flex flex-col gap-0.5 border-l border-[var(--color-border)] pl-3">
            {LABOR_FORECASTING_SECTIONS.map((section) => (
              <NavLink
                key={section.slug}
                to={`/labor-forecasting/${section.slug}`}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-xs transition-colors ${
                    isActive ? 'bg-[var(--color-surface-2)] font-medium text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]'
                  }`
                }
              >
                {section.label}
              </NavLink>
            ))}
          </div>
        )}

        {REST_NAV_ITEMS.map((item) => (
          <NavItem key={item.to} to={item.to} label={item.label} Icon={item.icon} />
        ))}
      </nav>
      <div className="px-4 py-4 text-[10px] text-[var(--color-text-muted)]">Enterprise data via UKG/Workday/POS/Payroll/ERP connectors.</div>
    </aside>
  )
}
