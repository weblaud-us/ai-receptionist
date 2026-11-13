import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Navigate, useLocation } from 'react-router-dom'
import { api, authStore, UNAUTHORIZED_EVENT } from '@/lib/api'

interface AuthState {
  user: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [user, setUser] = useState<string | null>(() => (authStore.token ? authStore.user : null))

  const logout = useCallback(() => {
    authStore.clear()
    setUser(null)
    qc.clear()
  }, [qc])

  const login = useCallback(async (username: string, password: string) => {
    const res = await api<{ access_token: string; username: string }>('/admin/login', {
      method: 'POST',
      body: { username, password },
      auth: false,
    })
    authStore.set(res.access_token, res.username)
    setUser(res.username)
  }, [])

  // The API client fires this when any authenticated call returns 401 (expired token).
  useEffect(() => {
    const onUnauthorized = () => {
      setUser(null)
      qc.clear()
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [qc])

  const value = useMemo(() => ({ user, login, logout }), [user, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const location = useLocation()
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <>{children}</>
}
