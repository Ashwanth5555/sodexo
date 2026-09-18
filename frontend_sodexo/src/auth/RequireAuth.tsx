import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  // Still verifying a stored token - render nothing rather than flashing the
  // login screen at a user who is in fact already signed in.
  if (loading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-[var(--color-bg)] text-sm text-[var(--color-text-muted)]">
        <span className="animate-pulse">Loading…</span>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  return <>{children}</>
}
