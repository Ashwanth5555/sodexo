import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Eye, EyeOff, Loader2, Lock, User } from 'lucide-react'
import logo from '../assets/sodexo-logo.png'
import { useAuth } from '../auth/AuthContext'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Where the user was headed before the guard bounced them here.
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (submitting) return
    setError(null)
    setSubmitting(true)
    try {
      await login(username, password)
      navigate(from, { replace: true })
    } catch (err: any) {
      setError(err?.response?.status === 401 ? 'Incorrect username or password.' : 'Unable to sign in. Is the API running?')
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen w-screen items-center justify-center bg-[var(--color-bg)] p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2.5 rounded-xl border border-[var(--color-border)] bg-white px-4 py-3 shadow-[var(--shadow-sm)]">
          <img src={logo} alt="Sodexo" className="h-8 shrink-0" />
          <div className="min-w-0 border-l border-[var(--color-border)] pl-2.5">
            <div className="text-sm font-bold leading-tight tracking-tight text-[var(--color-text)]">Healthcare</div>
            <div className="mt-0.5 text-[9px] font-semibold uppercase leading-snug tracking-[0.03em] text-[var(--color-primary)]">
              Labor Forecasting &amp; Workforce Intelligence
            </div>
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-7 shadow-[var(--shadow-md)]"
        >
          <h1 className="text-lg font-semibold tracking-tight text-[var(--color-text)]">Sign in</h1>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">Use your workforce intelligence account to continue.</p>

          <label className="mt-6 block text-xs font-medium text-[var(--color-text)]" htmlFor="username">
            Username
          </label>
          <div className="relative mt-1.5">
            <User size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] py-2.5 pl-9 pr-3 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)] focus:bg-white"
              placeholder="admin"
            />
          </div>

          <label className="mt-4 block text-xs font-medium text-[var(--color-text)]" htmlFor="password">
            Password
          </label>
          <div className="relative mt-1.5">
            <Lock size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              id="password"
              name="password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-2)] py-2.5 pl-9 pr-10 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)] focus:bg-white"
              placeholder="••••••••"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-1 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
            >
              {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>

          {error && (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || !username || !password}
            className="mt-6 flex w-full items-center justify-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[var(--color-primary-dark)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting && <Loader2 size={15} className="animate-spin" />}
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="mt-5 text-center text-[10px] leading-relaxed text-[var(--color-text-muted)]">
          Authorised users only. Accounts are issued by your administrator.
        </p>
      </div>
    </div>
  )
}
