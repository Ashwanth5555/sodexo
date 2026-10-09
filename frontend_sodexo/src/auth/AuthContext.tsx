import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, clearAuthToken, setAuthToken } from '../services/api'

export interface AuthUser {
  username: string
  full_name: string
  role: string
  job_title: string
}

interface AuthState {
  user: AuthUser | null
  /** True until the stored token (if any) has been checked against the API. */
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const TOKEN_KEY = 'sodexo.auth.token'
const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  // On first load, a token in localStorage is only a claim - it may be expired
  // or signed with a secret the server no longer uses. Verify it before
  // rendering the app, otherwise every page would mount and then 401.
  useEffect(() => {
    const stored = localStorage.getItem(TOKEN_KEY)
    if (!stored) {
      setLoading(false)
      return
    }
    setAuthToken(stored)
    let cancelled = false
    api
      .get<AuthUser>('/auth/me')
      .then(({ data }) => {
        if (!cancelled) setUser(data)
      })
      .catch(() => {
        localStorage.removeItem(TOKEN_KEY)
        clearAuthToken()
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const { data } = await api.post<{ access_token: string; user: AuthUser }>('/auth/login', { username, password })
    localStorage.setItem(TOKEN_KEY, data.access_token)
    setAuthToken(data.access_token)
    setUser(data.user)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    clearAuthToken()
    setUser(null)
  }, [])

  // The API layer signs out on any 401 so an expired token mid-session drops
  // the user back to the login screen instead of showing broken pages.
  useEffect(() => {
    const handler = () => logout()
    window.addEventListener('sodexo:unauthorized', handler)
    return () => window.removeEventListener('sodexo:unauthorized', handler)
  }, [logout])

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export const ROLE_LABELS: Record<string, string> = {
  ADMIN: 'Administrator',
  OPERATIONS_MANAGER: 'Operations Manager',
  DATA_ANALYST: 'Data Analyst',
  EXECUTIVE: 'Executive',
}
