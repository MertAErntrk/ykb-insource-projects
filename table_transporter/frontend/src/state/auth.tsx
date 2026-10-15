import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { SESSION_EXPIRED_EVENT, setCsrfToken } from '../api/client.ts'
import * as api from '../api/endpoints.ts'
import type { User } from '../api/types.ts'

type AuthStatus = 'loading' | 'authed' | 'anon'
interface AuthApi {
  status: AuthStatus
  user: User | null
  /** Hata durumunda ApiError fırlatır. */
  login: (username: string, password: string) => Promise<void>
  /** Çalışan iş varsa JOB_RUNNING fırlatır; cancelRunning=true ile iptal edip çıkar. */
  logout: (cancelRunning?: boolean) => Promise<void>
}

const AuthContext = createContext<AuthApi | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<User | null>(null)

  const apply = useCallback((u: User | null, csrf: string | null) => {
    setCsrfToken(csrf)
    setUser(u)
    setStatus(u ? 'authed' : 'anon')
  }, [])

  useEffect(() => {
    let alive = true
    api.me().then(
      (s) => alive && apply(s.user, s.csrf_token),
      () => alive && apply(null, null),
    )
    const onExpired = () => apply(null, null)
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => {
      alive = false
      window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
    }
  }, [apply])

  const login = useCallback(async (username: string, password: string) => {
    const s = await api.login({ username, password })
    apply(s.user, s.csrf_token)
  }, [apply])

  const logout = useCallback(async (cancelRunning = false) => {
    await api.logout(cancelRunning ? { cancel_running: true } : {})
    try { sessionStorage.removeItem('tt_wizard') } catch { /* yok say */ }
    apply(null, null)
  }, [apply])

  const value = useMemo(() => ({ status, user, login, logout }), [status, user, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthApi {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth, AuthProvider içinde kullanılmalı')
  return ctx
}
