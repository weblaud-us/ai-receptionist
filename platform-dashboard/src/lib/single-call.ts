/**
 * Single calls (SALES_SINGLE_CALL_PLAN.md): call one business now, without a campaign. The AI talks,
 * after a person reviewed exactly that call. Backend: routers/sales_calls.py, core/outbound/single_call.py.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'
import type { CallCost } from '@/lib/call-cost'
import type { Attempt, CampaignMode, JoinInfo, SalesLead } from '@/lib/sales'

const BASE = '/platform/sales'
const NO_TENANT = { tenant: null } as const // platform-level: never send X-Tenant-Id
const post = <T,>(path: string, body: unknown) => api<T>(BASE + path, { method: 'POST', body, ...NO_TENANT })

/** Any ISO country code (lib/countries.ts); a number typed with + says its own country. */
export type Country = string

/** A check a sales manager may override: our own rules, never a law's (backend single_call.OVERRIDES). */
export type Override = 'simulation' | 'business_hours' | 'single_gap' | 'recently_called'

export interface CallCheck {
  id: string
  label: string
  /** `overridden`: it failed, and the person overrode it (it is in the review's `overrides`). */
  status: 'pass' | 'fail' | 'warn' | 'overridden'
  detail: string
  /** Set on a failed (or overridden) check that a sales manager may override. */
  override?: Override
}

/** Everything the call would do, worked out on the server. Approving it approves exactly this (`hash`). */
export interface SingleCallReview {
  lead_id: string
  mode: CampaignMode | null
  mode_reason: string
  offer: { id: string; name: string; version: number; company_name: string }
  variant_id: string
  /** They asked to be called back: the AI opens with that, about the offer they asked about. */
  callback: boolean
  offers: { id: string; name: string; version: number; mode: CampaignMode | null; reason: string; variants: { id: string; label: string }[] }[]
  from_number: string
  caller_ids: string[]
  opener: string
  checks: CallCheck[]
  failures: string[]
  retry_at: string | null
  local_times: { tz: string; text: string }[]
  /** What a minute of this call costs: its country, landline or mobile, our caller ID. */
  cost: CallCost
  can_call: boolean
  /** Overrides applied to this review (the hash covers them). */
  overrides: Override[]
  /** Failed checks that could still be overridden. */
  overridable: Override[]
  /** This person may override (a sales manager or an admin). */
  may_override: boolean
  hash: string
}

export interface CallSuggestion {
  offer_id: string
  offer_version: number
  variant_id: string
  why: string
  vertical: string
  source: 'ai' | 'rule'
}

export interface CallPrep {
  /** The website had no phone number: ask for it. */
  needs_phone?: boolean
  found?: { business_name: string; website: string }
  site_error?: string
  lead?: SalesLead
  created?: boolean
  /** False: the AI may not call this business (the reason says why); nothing was spent. */
  callable?: boolean
  reason?: string
  ai_filled?: string[]
  suggestion?: CallSuggestion | null
  research_note?: string
  review?: SingleCallReview | null
}

export interface NewBusiness {
  website: string
  /** They have no website of their own: nothing to read, and the AI can offer to build one. */
  no_website: boolean
  business_name: string
  phone: string
  country: Country
  contact_name: string
}

export interface ReviewInput {
  lead_id: string
  offer_id: string
  variant_id: string
  from_number?: string
  overrides?: Override[]
}

/** "Approve & call": the review as shown, and why the person overrode its checks (when they did). */
export type PlaceInput = SingleCallReview & { override_reason?: string }

export interface PlacedCall {
  attempt_id: string
  room: string
  attempt: Attempt
  join: JoinInfo
}

export function useSingleCall() {
  const qc = useQueryClient()
  const refresh = (...keys: string[]) => () => {
    for (const key of keys) void qc.invalidateQueries({ queryKey: [key] })
  }
  return {
    prepare: useMutation({
      mutationFn: (body: { lead_id: string } | { business: NewBusiness }) => post<CallPrep>('/calls/prepare', body),
      onSuccess: refresh('s-leads', 's-lead'),
    }),
    review: useMutation({ mutationFn: (body: ReviewInput) => post<SingleCallReview>('/calls/review', body) }),
    place: useMutation({
      mutationFn: (r: PlaceInput) => post<PlacedCall>('/calls', {
        lead_id: r.lead_id, offer_id: r.offer.id, offer_version: r.offer.version, variant_id: r.variant_id,
        from_number: r.from_number, review_hash: r.hash, approved: true,
        overrides: r.overrides, override_reason: r.overrides.length ? r.override_reason ?? '' : '',
      }),
      onSuccess: refresh('s-live', 's-leads', 's-lead'),
      meta: { silentError: true },      // "changed since you reviewed" and refusals are shown in the sheet
    }),
  }
}

/** What a refused "Approve & call" carries: the fresh review to show, and why. */
export function refusal(error: unknown): { message: string; review: SingleCallReview | null } {
  const e = error as { message?: string; detail?: unknown } | null
  const detail = (e?.detail ?? null) as { message?: string; review?: SingleCallReview } | null
  return { message: detail?.message || e?.message || 'The call could not be placed.', review: detail?.review ?? null }
}

export const FIELD_LABEL: Record<string, string> = {
  business_name: 'business name', contact_name: 'contact', email: 'email', vertical: 'kind of business',
  address: 'address', region_code: 'state', timezones: 'time zone', website: 'website',
}
