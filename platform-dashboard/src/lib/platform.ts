/**
 * Platform admin API (/platform/*): businesses, phone-number routing, the vertical catalog and
 * unit economics. Platform-level calls never send X-Tenant-Id (`tenant: null`).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'

const NO_TENANT = { tenant: null } as const

export type Vertical = 'home_services' | 'salon' | 'restaurant' | 'hotel' | 'clinic' | 'professional' | 'generic'

export interface PlatformTenant {
  id: string
  name: string
  slug: string
  vertical: Vertical
  status: string
  region: string
  timezone: string
  calls_30d: number
  minutes_30d: number
  included_minutes: number | null
  max_concurrent_calls: number
  numbers: string[]
  created_at: string
}

export interface VerticalOption {
  key: Vertical
  label: string
  description: string
}

export interface CostModel {
  items: { key: string; label: string; low: number; high: number; basis: string; applies: string }[]
  per_minute: { low: number; mid: number; high: number }
  per_minute_with_recording: { low: number; mid: number; high: number }
  fixed_monthly: { label: string; low: number; high: number }[]
  fixed_monthly_total: { low: number; high: number }
  fixed_per_tenant_month: number
  plans: { key: string; name: string; monthly_usd: number; included_minutes: number; margin_pct: number }[]
  overage: { usd_per_min: number; margin_pct: number }
  last_30d: { calls: number; seconds: number; cost_usd: number }
  tenants_by_status: Record<string, number>
  tenants_by_plan: Record<string, number>
  list_mrr_usd: number
  note: string
}

/* ───────────── queries ───────────── */

export const usePlatformTenants = (enabled = true) =>
  useQuery({
    queryKey: ['p-tenants'],
    queryFn: () => api<{ tenants: PlatformTenant[]; total: number }>('/platform/tenants', NO_TENANT),
    enabled,
  })

export const useVerticals = () =>
  useQuery({
    queryKey: ['p-verticals'],
    queryFn: () => api<{ verticals: VerticalOption[] }>('/platform/verticals', NO_TENANT),
    staleTime: Infinity,
  })

export const useCostModel = () =>
  useQuery({ queryKey: ['p-costs'], queryFn: () => api<CostModel>('/platform/costs', NO_TENANT) })

/* ───────────── mutations ───────────── */

export function usePlatformMutations() {
  const qc = useQueryClient()
  const done = () => void qc.invalidateQueries({ queryKey: ['p-tenants'] })
  return {
    provision: useMutation({
      mutationFn: (body: Record<string, unknown>) =>
        api<{ tenant: { id: string; name: string }; set_password_link: string | null }>('/platform/tenants', {
          method: 'POST',
          body,
          ...NO_TENANT,
        }),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
        api(`/platform/tenants/${id}`, { method: 'PATCH', body, ...NO_TENANT }),
      onSuccess: done,
    }),
    assignNumber: useMutation({
      mutationFn: (body: { e164: string; tenant_id: string }) =>
        api('/platform/phone-numbers', { method: 'POST', body, ...NO_TENANT }),
      onSuccess: done,
    }),
    releaseNumber: useMutation({
      mutationFn: (e164: string) => api(`/platform/phone-numbers/${encodeURIComponent(e164)}`, { method: 'DELETE', ...NO_TENANT }),
      onSuccess: done,
    }),
  }
}
