/**
 * Phase 6 (legal & privacy: /t/legal, /t/privacy/*) and Phase 7 (results: /t/insights, /t/feedback,
 * /public/site) for the client dashboard.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'
import { downloadFile } from './billing'

/* ───────────── types ───────────── */

export interface LegalDocument {
  key: string
  title: string
  version: string
  url: string
}

export interface Subprocessor {
  name: string
  purpose: string
  data: string
  location: string
  when: string
}

export interface LegalStatus {
  entity: string
  documents: LegalDocument[]
  dpa: { version: string; accepted_version: string | null; accepted_at: string | null; current: boolean; url: string }
  outstanding: string[]
  subprocessors: Subprocessor[]
  retention: { transcript_days: number; after_cancellation_days: number }
  privacy_contact: string
  dsr_deadline_days: number
}

export interface SubjectSummary {
  phone: string
  counts: Record<string, number>
  total: number
  future_appointments: number
  recordings: number
}

export interface PrivacyRequest {
  id: string
  kind: 'access' | 'erasure'
  phone_masked: string
  received_at: string
  due_at: string
  completed_at: string
  status: string
  actor: string
  note: string
  result: Record<string, number>
}

export interface Insights {
  days: number
  pilot: boolean
  answering_mode: 'all_calls' | 'overflow' | 'after_hours'
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
  by_day: { day: string; calls: number }[]
  /** Dental practices only. */
  dental?: DentalInsights
}

export interface DentalInsights {
  overflow_calls_answered: number
  by_window: { lunch_break: number; after_hours: number; busy_overflow: number }
  consultations_booked: number
  emergency_triages_captured: number
  cosmetic_leads: number
  missed_call_sms_recoveries: number
  estimated_pipeline_value_minor: number
  currency: string
  recall_texts_sent?: number
  reactivation_bookings_attributed?: number
}

/** /public/site moved to shared: the platform app's Help menu needs it too. */
export { usePublicSite, type PublicSite } from '@receptionist/shared/lib/site'

/* ───────────── legal ───────────── */

export const useLegal = () => useQuery({ queryKey: ['t-legal'], queryFn: () => api<LegalStatus>('/t/legal') })

export function useLegalMutations() {
  const qc = useQueryClient()
  const done = () => void qc.invalidateQueries({ queryKey: ['t-legal'] })
  return {
    acceptDpa: useMutation({
      mutationFn: (body: { version: string; signer_name: string; signer_title?: string }) =>
        api('/t/legal/dpa/accept', { method: 'POST', body }),
      onSuccess: done,
    }),
    acceptDocuments: useMutation({
      mutationFn: (documents: string[]) => api('/t/legal/accept', { method: 'POST', body: { documents } }),
      onSuccess: done,
    }),
  }
}

/* ───────────── data subject requests ───────────── */

export const lookupSubject = (phone: string) => api<SubjectSummary>('/t/privacy/subject', { params: { phone } })

export const exportSubject = (phone: string, receivedAt?: string) => {
  const q = new URLSearchParams({ phone })
  if (receivedAt) q.set('received_at', receivedAt)
  return downloadFile(`/t/privacy/subject/export?${q.toString()}`, 'caller-data.json')
}

export const usePrivacyRequests = () =>
  useQuery({ queryKey: ['t-privacy-requests'], queryFn: () => api<{ requests: PrivacyRequest[] }>('/t/privacy/requests') })

export function useEraseSubject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { phone: string; confirm: string; include_future: boolean; suppress: boolean; received_at?: string; note?: string }) =>
      api<{ phone: string; counts: Record<string, number>; suppressed: boolean }>('/t/privacy/subject/erase', { method: 'POST', body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['t-privacy-requests'] }),
  })
}

/* ───────────── results & feedback ───────────── */

export const useInsights = (days: number) =>
  useQuery({ queryKey: ['t-insights', days], queryFn: () => api<Insights>('/t/insights', { params: { days } }) })

export function useFeedback() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { score: number; comment: string; testimonial_ok: boolean }) =>
      api('/t/feedback', { method: 'POST', body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['t-insights'] }),
  })
}
