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

/* ───────────── Phase 6/7: pilots, transcript review, eval cases, margins, privacy ───────────── */

export interface ValueReport {
  calls_answered: number
  minutes: number
  would_have_missed: number
  bookings: number
  est_booking_value: number
  currency: string
  leads: number
  messages: number
  transfers: number
  handled_without_human_pct: number | null
  latency_p50_s: number | null
  unanswered_questions: number
  satisfaction: { score: number; at: string; comment: string } | null
}

export interface Pilot {
  id: string
  name: string
  vertical: Vertical
  status: string
  answering_mode: string
  pilot: { active: boolean; started_at: string | null; ends_at: string | null; contact_name: string; feedback_day: string; case_study_ok: boolean; notes: string }
  last_7_days: ValueReport
  reviews_30d: { reviewed: number; issues: number }
}

export interface QueuedCall {
  call_id: string
  tenant_id: string
  business: string
  pilot: boolean
  started_at: string
  duration_s: number | null
  outcome: string | null
  summary?: string
  latency?: { p50_s: number | null }
  tools_used?: string[]
  after_hours?: boolean | null
}

export interface ReviewCall {
  call: QueuedCall & { transcript?: { role: string; text: string; at: string }[]; caller_phone?: string }
  review: { verdict: 'good' | 'issue'; issues: string[]; notes: string; expected: string } | null
}

export interface EvalCase {
  id: string
  title: string
  vertical: string
  status: 'draft' | 'approved' | 'rejected'
  caller_turns: string[]
  expect: { intent: string; must_call: string[]; must_not_call: string[] }
  created_at: string
  author: string
}

export interface MarginRow {
  id: string
  name: string
  plan: string
  status: string
  pilot: boolean
  minutes: number
  cost_usd: number
  revenue_usd: number | null
  margin_usd: number | null
  margin_pct: number | null
  flag: string
}

export interface PrivacyRequestRow {
  id: string
  business: string
  tenant_id: string
  kind: 'access' | 'erasure'
  phone_masked: string
  received_at: string
  due_at: string
  completed_at: string
  actor: string
}

export interface ConsentEvent {
  kind: 'opt_out' | 'opt_in'
  phone: string
  channel: string
  scope: 'tenant' | 'platform'
  tenant_id: string | null
  at: string
  dnc_added?: boolean
}

export const REVIEW_ISSUES = ['prompt', 'tool', 'knowledge', 'latency', 'speech', 'transfer', 'hallucination', 'other'] as const

export const usePilots = () =>
  useQuery({ queryKey: ['p-pilots'], queryFn: () => api<{ pilots: Pilot[] }>('/platform/pilots', NO_TENANT) })

export const useReviewQueue = (tenantId?: string) =>
  useQuery({
    queryKey: ['p-review-queue', tenantId ?? ''],
    queryFn: () => api<{ calls: QueuedCall[] }>('/platform/reviews/queue', { params: { tenant_id: tenantId || undefined }, ...NO_TENANT }),
  })

export const useReviewCall = (tenantId: string | null, callId: string | null) =>
  useQuery({
    queryKey: ['p-review-call', tenantId, callId],
    queryFn: () => api<ReviewCall>(`/platform/reviews/calls/${tenantId}/${callId}`, NO_TENANT),
    enabled: !!tenantId && !!callId,
  })

export const useReviewStats = () =>
  useQuery({
    queryKey: ['p-review-stats'],
    queryFn: () => api<{ by_tenant: Record<string, { reviewed: number; issues: number }>; issues: Record<string, number>; eval_cases: { draft: number; approved: number } }>('/platform/reviews/stats', NO_TENANT),
  })

export const useEvalCases = () =>
  useQuery({ queryKey: ['p-eval-cases'], queryFn: () => api<{ cases: EvalCase[] }>('/platform/eval-cases', NO_TENANT) })

export const useMargins = () =>
  useQuery({
    queryKey: ['p-margins'],
    queryFn: () => api<{ days: number; fixed_per_business_usd: number; businesses: MarginRow[]; totals: { revenue_usd: number; cost_usd: number; margin_usd: number } }>('/platform/margins', NO_TENANT),
  })

export const usePrivacyRequests = () =>
  useQuery({ queryKey: ['p-privacy'], queryFn: () => api<{ requests: PrivacyRequestRow[] }>('/platform/privacy/requests', NO_TENANT) })

export const useConsentEvents = () =>
  useQuery({ queryKey: ['p-consent'], queryFn: () => api<{ events: ConsentEvent[] }>('/platform/consent-events', NO_TENANT) })

export function useReviewMutations() {
  const qc = useQueryClient()
  const refresh = () => {
    for (const key of ['p-review-queue', 'p-review-call', 'p-review-stats', 'p-eval-cases', 'p-pilots']) void qc.invalidateQueries({ queryKey: [key] })
  }
  return {
    review: useMutation({
      mutationFn: (body: { tenant_id: string; call_id: string; verdict: 'good' | 'issue'; issues: string[]; notes: string; expected: string }) =>
        api('/platform/reviews', { method: 'POST', body, ...NO_TENANT }),
      onSuccess: refresh,
    }),
    makeCase: useMutation({
      mutationFn: (body: { tenant_id: string; call_id: string; title: string; expected: string; must_call: string[]; must_not_call: string[] }) =>
        api<{ case: EvalCase }>('/platform/eval-cases', { method: 'POST', body, ...NO_TENANT }),
      onSuccess: refresh,
    }),
    setCaseStatus: useMutation({
      mutationFn: ({ id, ...body }: { id: string; status: EvalCase['status'] }) =>
        api(`/platform/eval-cases/${id}`, { method: 'PATCH', body, ...NO_TENANT }),
      onSuccess: refresh,
    }),
  }
}
