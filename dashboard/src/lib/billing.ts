/**
 * Phase 5: plans, billing, self-serve numbers and data export (/t/billing, /t/numbers, /t/export,
 * /public/plans).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, API_BASE, ApiError, refreshSession, session } from '@receptionist/shared/lib/api'

export type TenantStatus = 'incomplete' | 'trial' | 'active' | 'past_due' | 'suspended' | 'cancelled'
export type Gate = 'ok' | 'suspended' | 'cap'

export interface PlanInfo {
  key: string
  name: string
  monthly_usd: number
  annual_usd: number
  included_minutes: number
  max_users: number
  max_numbers: number
  tagline: string
  features: string[]
  recommended: boolean
  self_serve: boolean
}

export interface Catalogue {
  plans: PlanInfo[]
  overage_usd_per_min: number
  setup_fee_usd: number
  trial: { days: number; minutes: number; card_required: boolean }
  annual_months_free: number
  currency: string
  terms_url: string
  privacy_url: string
  signup_enabled?: boolean
  billing_enabled?: boolean
}

/** The slice of billing every page gets in /t/context (drives the banner). */
export interface BillingBrief {
  plan: string
  status: TenantStatus
  self_serve: boolean
  trial_ends_at: string | null
  delete_after: string | null
  cancel_at_period_end: boolean
  gate: Gate
  billing_enabled: boolean
}

export interface Usage {
  period_start: string
  period_end: string
  used_minutes: number
  used_seconds: number
  included_minutes: number
  percent: number | null
  overage_minutes: number
  trial: boolean
  hard_cap: boolean
  capped: boolean
}

export interface Billing {
  status: TenantStatus
  plan: string
  plan_name: string
  interval: 'month' | 'year'
  self_serve: boolean
  billing_enabled: boolean
  has_customer: boolean
  subscription_status: string
  trial_ends_at: string | null
  trial_used: boolean
  cancel_at_period_end: boolean
  past_due_since: string | null
  cancelled_at: string | null
  delete_after: string | null
  number_release_after: string | null
  hard_cap: boolean
  verified_phone: string
  usage: Usage
  gate: Gate
  limits: { included_minutes: number; max_concurrent_calls: number; max_users: number; max_numbers: number }
  overage_usd_per_min: number
  catalogue: Catalogue
  can_manage: boolean
}

export interface AvailableNumber {
  e164: string
  locality: string
  region: string
  country: string
  fake?: boolean
}

export interface NumberOrder {
  id: string
  e164: string
  country: string
  status: 'pending' | 'running' | 'active' | 'failed' | 'verify_failed' | 'needs_documents' | 'cancelled'
  steps: Record<string, boolean>
  error: string
  note?: string
  created_at: string
}

export const usePlans = () =>
  useQuery({ queryKey: ['public-plans'], queryFn: () => api<Catalogue>('/public/plans', { auth: false }), staleTime: 300_000 })

export const useBilling = () =>
  useQuery({ queryKey: ['t-billing'], queryFn: () => api<Billing>('/t/billing'), refetchInterval: 60_000 })

/** Send the browser to a Stripe-hosted page (Checkout or the Customer Portal). */
function redirect(r: { url?: string | null }) {
  if (r.url) window.location.assign(r.url)
}

export const useCheckout = () =>
  useMutation({
    mutationFn: (body: { plan: string; interval: 'month' | 'year'; setup_fee?: boolean }) =>
      api<{ url: string }>('/t/billing/checkout', { method: 'POST', body }),
    onSuccess: redirect,
  })

export const usePortal = () =>
  useMutation({ mutationFn: () => api<{ url: string }>('/t/billing/portal', { method: 'POST' }), onSuccess: redirect })

export function usePatchBilling() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { hard_cap?: boolean }) => api('/t/billing', { method: 'PATCH', body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['t-billing'] })
      void qc.invalidateQueries({ queryKey: ['context'] })
    },
  })
}

export const useVerifyPhone = () =>
  useMutation({
    mutationFn: (phone: string) =>
      api<{ sent: boolean; dev_code?: string }>('/t/billing/verify-phone', { method: 'POST', body: { phone } }),
  })

export function useConfirmPhone() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => api<{ phone: string }>('/t/billing/verify-phone/confirm', { method: 'POST', body: { code } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['t-billing'] })
      void qc.invalidateQueries({ queryKey: ['context'] })
    },
  })
}

export const useNumberSearch = (params: { country: string; area_code: string }, enabled: boolean) =>
  useQuery({
    queryKey: ['t-number-search', params],
    queryFn: () =>
      api<{ numbers: AvailableNumber[]; mode: string; instant: boolean }>('/t/numbers/search', { params }),
    enabled,
    staleTime: 60_000,
    retry: false,
  })

export const useNumberOrders = (poll: boolean) =>
  useQuery({
    queryKey: ['t-number-orders'],
    queryFn: () => api<{ orders: NumberOrder[]; mode: string }>('/t/numbers/orders'),
    refetchInterval: poll ? 3_000 : false,
  })

export function useOrderNumber() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (e164: string) => api<{ order: NumberOrder }>('/t/numbers', { method: 'POST', body: { e164 } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['t-number-orders'] })
      void qc.invalidateQueries({ queryKey: ['t-phone'] })
    },
  })
}

export const useResendVerification = () =>
  useMutation({ mutationFn: () => api<{ emailed?: boolean; already_verified?: boolean }>('/signup/resend-verification', { method: 'POST' }) })

/** Download an authenticated file (the export zip / CSVs) without exposing the token in a URL. */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const go = () => {
    const headers: Record<string, string> = {}
    if (session.token) headers.Authorization = `Bearer ${session.token}`
    if (session.tenant) headers['X-Tenant-Id'] = session.tenant
    return fetch(API_BASE + path, { headers })
  }
  let res = await go()
  if (res.status === 401 && (await refreshSession())) res = await go()
  if (!res.ok) {
    let message = `Download failed (${res.status})`
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) message = body.detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message)
  }
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export const STATUS_LABEL: Record<TenantStatus, string> = {
  incomplete: 'Choose a plan',
  trial: 'Free trial',
  active: 'Active',
  past_due: 'Payment failed',
  suspended: 'Paused',
  cancelled: 'Cancelled',
}
