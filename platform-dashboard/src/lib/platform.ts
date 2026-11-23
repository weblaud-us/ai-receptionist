/**
 * Platform admin API (/platform/*): businesses, phone-number routing, the vertical catalog and
 * unit economics. Platform-level calls never send X-Tenant-Id (`tenant: null`).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'
import type { SiteDraft } from '@receptionist/shared/lib/site-import'

const NO_TENANT = { tenant: null } as const

export type Vertical =
  | 'home_services'
  | 'salon'
  | 'restaurant'
  | 'hotel'
  | 'clinic'
  | 'dental'
  | 'professional'
  | 'generic'

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

/* ───────────── one business (details page) ───────────── */

export type Role = 'owner' | 'admin' | 'staff'

export interface TenantMember {
  id: string
  email: string
  name: string
  role: Role | null
  last_login_at: string | null
  disabled: boolean
  pending_password: boolean
  mfa: boolean
}

export interface TenantNumber {
  e164: string
  purpose?: string
  provider?: string
  status?: string
  country?: string
  assigned_at?: string
}

export interface TenantCall {
  call_id: string
  started_at: string
  duration_s: number | null
  outcome: string | null
  status: string
  direction?: string
  caller_phone?: string
  summary?: string
  after_hours?: boolean | null
}

export interface AuditEntry {
  user_id: string | null
  action: string
  target_type: string
  target_id: string
  diff: Record<string, unknown>
  ip: string
  at: string
}

/** The tenant document as stored (only the parts the details page reads are typed). */
export interface TenantDoc {
  id: string
  name: string
  slug: string
  vertical: Vertical
  status: string
  region: 'us' | 'eu' | 'me'
  country: string
  timezone: string
  locale: string
  currency: string
  phone: string
  website: string
  answering_mode: 'all_calls' | 'overflow' | 'after_hours'
  agent: { name: string; max_call_minutes: number; call_notice?: string }
  transfer: { number: string }
  notifications: { owner_email: string; owner_phone: string }
  limits: { included_minutes: number; max_concurrent_calls: number; max_users: number; max_numbers: number }
  billing: {
    plan: string
    interval: string
    subscription_status: string
    stripe_customer_id: string
    trial_ends_at: string | null
    period_end: string | null
    hard_cap: boolean
    cancelled_at: string | null
    delete_after: string | null
  }
  pilot: { active: boolean; ends_at: string | null }
  legal: { dpa_version: string; dpa_accepted_at: string | null }
  onboarding: { completed_at?: string }
  created_at: string | null
  updated_at: string | null
}

export interface TenantDetails {
  tenant: TenantDoc
  stats: {
    calls_30d: number
    minutes_30d: number
    calls_total: number
    contacts: number
    appointments_upcoming: number
    leads: number
    messages: number
    knowledge: number
    services: number
  }
  numbers: TenantNumber[]
  members: TenantMember[]
  invites: { email: string; role: Role; expires_at: string | null }[]
  recent_calls: TenantCall[]
  audit: AuditEntry[]
  number_orders: NumberOrder[]
}

/** Orders still moving: the details page and the wizard's last step refresh while one is. */
export const ORDER_IN_FLIGHT: NumberOrder['status'][] = ['pending', 'running']

export const usePlatformTenant = (id: string | undefined) =>
  useQuery({
    queryKey: ['p-tenant', id],
    queryFn: () => api<TenantDetails>(`/platform/tenants/${encodeURIComponent(id!)}`, NO_TENANT),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.number_orders?.some((o) => ORDER_IN_FLIGHT.includes(o.status)) ? 3000 : false),
  })

/* ───────────── New business wizard: website import, numbers ───────────── */

export type NumberMode = 'twilio' | 'fake' | 'off'

export interface NumberOption {
  e164: string
  locality?: string
  region?: string
  country?: string
  friendly_name?: string
  fake?: boolean
}

export interface NumberList {
  numbers: NumberOption[]
  mode: NumberMode
  problems: string[]
  instant?: boolean
}

export interface NumberChoice {
  source: 'owned' | 'buy' | 'none'
  e164: string
}

export interface ProvisionResult {
  tenant: { id: string; name: string; slug: string; vertical: Vertical }
  owner: { id: string; email: string }
  set_password_link: string | null
  emailed: boolean
  added: { services: number; knowledge: number }
  order: NumberOrder | null
  number_error: string | null
}

export const useSiteImport = () =>
  useMutation({
    mutationFn: (url: string) => api<{ draft: SiteDraft }>('/platform/site-import', { method: 'POST', body: { url }, ...NO_TENANT }),
  })

export const useOwnedNumbers = (enabled = true) =>
  useQuery({
    queryKey: ['p-owned-numbers'],
    queryFn: () => api<NumberList>('/platform/numbers/owned', NO_TENANT),
    enabled,
    staleTime: 30_000,
  })

export const useAvailableNumbers = (search: { country: string; area_code: string; contains: string } | null) =>
  useQuery({
    queryKey: ['p-available-numbers', search],
    queryFn: () => api<NumberList>('/platform/numbers/available', { params: search ?? undefined, ...NO_TENANT }),
    enabled: !!search,
  })

export function useOrderTenantNumber(tenantId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { e164: string; source: 'buy' | 'owned' }) =>
      api<{ order: NumberOrder }>(`/platform/tenants/${encodeURIComponent(tenantId)}/numbers`, { method: 'POST', body, ...NO_TENANT }),
    onSuccess: () => {
      for (const key of ['p-tenant', 'p-tenants', 'p-owned-numbers', 'p-number-orders']) void qc.invalidateQueries({ queryKey: [key] })
    },
  })
}

export function useTenantTeamMutations(tenantId: string) {
  const qc = useQueryClient()
  const done = () => void qc.invalidateQueries({ queryKey: ['p-tenant', tenantId] })
  const base = `/platform/tenants/${encodeURIComponent(tenantId)}/members`
  return {
    add: useMutation({
      mutationFn: (body: { email: string; name: string; role: Role }) =>
        api<{ member: { id: string; email: string }; set_password_link: string | null }>(base, { method: 'POST', body, ...NO_TENANT }),
      onSuccess: done,
    }),
    setRole: useMutation({
      mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
        api(`${base}/${encodeURIComponent(userId)}`, { method: 'PATCH', body: { role }, ...NO_TENANT }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (userId: string) => api(`${base}/${encodeURIComponent(userId)}`, { method: 'DELETE', ...NO_TENANT }),
      onSuccess: done,
    }),
    resetLink: useMutation({
      mutationFn: (userId: string) => api<{ link: string }>(`${base}/${encodeURIComponent(userId)}/reset-link`, { method: 'POST', ...NO_TENANT }),
      onSuccess: done,
    }),
  }
}

/* ───────────── mutations ───────────── */

export function usePlatformMutations() {
  const qc = useQueryClient()
  const done = () => {
    void qc.invalidateQueries({ queryKey: ['p-tenants'] })
    void qc.invalidateQueries({ queryKey: ['p-tenant'] })
  }
  return {
    provision: useMutation({
      mutationFn: (body: Record<string, unknown>) =>
        api<ProvisionResult>('/platform/tenants', { method: 'POST', body, ...NO_TENANT }),
      onSuccess: () => {
        done()
        void qc.invalidateQueries({ queryKey: ['p-owned-numbers'] })
      },
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
    /** Cancel now: stops the Stripe subscription, keeps data CANCEL_RETENTION_DAYS, e-mails the owners. */
    cancel: useMutation({
      mutationFn: (id: string) => api(`/platform/tenants/${encodeURIComponent(id)}/cancel`, { method: 'POST', ...NO_TENANT }),
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

/* ───────────── number orders (Phase 5; operator-assisted outside US/CA) ───────────── */

export interface NumberOrder {
  id: string
  tenant_id: string
  e164: string
  country: string
  provider: string
  status: 'pending' | 'running' | 'active' | 'failed' | 'verify_failed' | 'needs_documents' | 'cancelled'
  steps: Record<string, boolean>
  error: string
  source?: 'owned' | 'buy'
  failed_step?: string
  note?: string
  attempts: number
  created_at: string
  approved_at?: string
}

export const useNumberOrders = (status = '') =>
  useQuery({
    queryKey: ['p-number-orders', status],
    queryFn: () => api<{ orders: NumberOrder[] }>(`/platform/number-orders${status ? `?status=${status}` : ''}`, NO_TENANT),
  })

export function useNumberOrderMutations() {
  const qc = useQueryClient()
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['p-number-orders'] })
    void qc.invalidateQueries({ queryKey: ['p-tenant'] })
  }
  return {
    approve: useMutation({
      mutationFn: ({ id, bundle_sid, address_sid }: { id: string; bundle_sid: string; address_sid: string }) =>
        api(`/platform/number-orders/${id}/approve`, { ...NO_TENANT, method: 'POST', body: { bundle_sid, address_sid } }),
      onSuccess: refresh,
    }),
    retry: useMutation({
      mutationFn: (id: string) => api(`/platform/number-orders/${id}/retry`, { ...NO_TENANT, method: 'POST' }),
      onSuccess: refresh,
    }),
  }
}
