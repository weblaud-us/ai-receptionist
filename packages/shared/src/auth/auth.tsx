import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Navigate, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { api, refreshSession, session, UNAUTHORIZED_EVENT, type Membership, type TokenResponse } from '../lib/api'

export interface AuthUser {
  id: string
  email: string
  name: string
  is_platform_admin: boolean
  /** The platform's own sales team (platform dashboard → Sales dialer). Platform admins are "admin". */
  sales_role?: 'admin' | 'manager' | 'caller' | 'viewer' | null
  mfa_enabled?: boolean
  email_verified?: boolean
}

interface AuthState {
  status: 'loading' | 'authenticated' | 'anonymous'
  user: AuthUser | null
  memberships: Membership[]
  /** The business the dashboard acts on (token tenant, or the platform admin's pick). */
  tenantId: string | null
  /** Resolves to an MFA challenge token when a TOTP code is still needed. */
  login: (email: string, password: string) => Promise<string | null>
  verifyMfa: (mfaToken: string, code: string) => Promise<void>
  /** Replace the session after enabling two-factor. */
  applyTokens: (res: TokenResponse) => void
  logout: () => Promise<void>
  switchTenant: (tenantId: string | null) => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [status, setStatus] = useState<AuthState['status']>('loading')
  const [user, setUser] = useState<AuthUser | null>(null)
  const [memberships, setMemberships] = useState<Membership[]>([])
  const [tenantId, setTenantId] = useState<string | null>(null)

  const apply = useCallback((res: TokenResponse) => {
    session.setToken(res.access_token)
    setUser(res.user ?? null)
    setMemberships(res.memberships ?? [])
    // Platform admins keep their chosen business; members use the token's tenant.
    const override = session.tenant
    setTenantId(res.user?.is_platform_admin ? override : res.tenant_id)
    if (!res.user?.is_platform_admin) session.setTenant(null)
    setStatus('authenticated')
  }, [])

  const reset = useCallback(() => {
    session.clear()
    setUser(null)
    setMemberships([])
    setTenantId(null)
    setStatus('anonymous')
    qc.clear()
  }, [qc])

  // On load: a refresh cookie may still be valid — sign in silently.
  useEffect(() => {
    let alive = true
    void refreshSession().then((res) => {
      if (!alive) return
      if (res?.access_token) apply(res)
      else setStatus('anonymous')
    })
    return () => {
      alive = false
    }
  }, [apply])

  useEffect(() => {
    const onUnauthorized = () => reset()
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [reset])

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await api<TokenResponse>('/auth/login', { method: 'POST', body: { email, password }, auth: false })
      if (res.mfa_required && res.mfa_token) return res.mfa_token
      qc.clear()
      apply(res)
      return null
    },
    [apply, qc],
  )

  const verifyMfa = useCallback(
    async (mfaToken: string, code: string) => {
      const res = await api<TokenResponse>('/auth/mfa/verify', { method: 'POST', body: { mfa_token: mfaToken, code }, auth: false })
      qc.clear()
      apply(res)
    },
    [apply, qc],
  )

  const logout = useCallback(async () => {
    try {
      await api('/auth/logout', { method: 'POST', auth: false })
    } finally {
      reset()
    }
  }, [reset])

  const switchTenant = useCallback(
    async (next: string | null) => {
      if (user?.is_platform_admin || !next) {
        session.setTenant(next)
        setTenantId(next)
      } else {
        const res = await api<TokenResponse>('/auth/switch', { method: 'POST', body: { tenant_id: next } })
        apply(res)
      }
      qc.clear() // every cached list belonged to the previous business
    },
    [apply, qc, user],
  )

  const value = useMemo(
    () => ({ status, user, memberships, tenantId, login, verifyMfa, applyTokens: apply, logout, switchTenant }),
    [status, user, memberships, tenantId, login, verifyMfa, apply, logout, switchTenant],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export function FullPageSpinner() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 className="size-6 animate-spin text-muted-foreground" />
    </div>
  )
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') return <FullPageSpinner />
  if (status === 'anonymous') return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <>{children}</>
}
