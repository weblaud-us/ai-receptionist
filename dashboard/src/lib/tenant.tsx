import { createContext, useContext, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@receptionist/shared/auth/auth'
import { api, ApiError } from '@receptionist/shared/lib/api'
import type { Role, TenantContext } from './tenant-types'

const RANK: Record<Role, number> = { staff: 1, admin: 2, owner: 3 }

interface TenantState {
  ctx: TenantContext | null
  loading: boolean
  error: unknown
  /** Platform admin signed in without a business picked (multi-tenant mode). */
  needsTenant: boolean
  can: (role: Role) => boolean
  refetch: () => void
}

const TenantCtx = createContext<TenantState | null>(null)

export function TenantProvider({ children }: { children: ReactNode }) {
  const { status, tenantId, user } = useAuth()
  const q = useQuery({
    queryKey: ['context', tenantId],
    queryFn: () => api<TenantContext>('/t/context'),
    enabled: status === 'authenticated',
    staleTime: 60_000,
    retry: false,
  })
  const needsTenant = q.error instanceof ApiError && q.error.status === 400 && !!user?.is_platform_admin
  const ctx = q.data ?? null
  const value: TenantState = {
    ctx,
    loading: q.isLoading,
    error: needsTenant ? null : q.error,
    needsTenant,
    can: (role) => !!ctx && (ctx.platform_admin || RANK[ctx.role] >= RANK[role]),
    refetch: () => void q.refetch(),
  }
  return <TenantCtx.Provider value={value}>{children}</TenantCtx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTenant(): TenantState {
  const v = useContext(TenantCtx)
  if (!v) throw new Error('useTenant must be used inside <TenantProvider>')
  return v
}

/** Current tenant context; only call where the layout guarantees it has loaded. */
// eslint-disable-next-line react-refresh/only-export-components
export function useCtx(): TenantContext {
  const { ctx } = useTenant()
  if (!ctx) throw new Error('tenant context not loaded')
  return ctx
}
