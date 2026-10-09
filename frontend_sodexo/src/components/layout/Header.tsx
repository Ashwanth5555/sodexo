import { LogOut } from 'lucide-react'
import { ROLE_LABELS, useAuth } from '../../auth/AuthContext'

export function Header({ title, subtitle }: { title: string; subtitle?: string }) {
  const { user, logout } = useAuth()

  const initials = user
    ? user.full_name
        .split(' ')
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0]?.toUpperCase() ?? '')
        .join('')
    : ''

  return (
    <header className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-6 py-4 shadow-[var(--shadow-sm)]">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-[var(--color-text)]">{title}</h1>
        {subtitle && <p className="text-xs text-[var(--color-text-muted)]">{subtitle}</p>}
      </div>
      {user && (
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary)] text-[11px] font-semibold text-white">
            {initials}
          </div>
          <div className="text-right leading-tight">
            <div className="text-xs font-medium text-[var(--color-text)]">{user.full_name}</div>
            <div className="text-[10px] text-[var(--color-text-muted)]">{user.job_title || ROLE_LABELS[user.role] || user.role}</div>
          </div>
          <button
            onClick={logout}
            title="Sign out"
            className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-2.5 py-1.5 text-xs text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
          >
            <LogOut size={14} />
            Sign out
          </button>
        </div>
      )}
    </header>
  )
}
