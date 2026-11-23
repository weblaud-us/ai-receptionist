/** Outbound sales dialer (Roadmap Phase 4): types and data hooks for /platform/sales. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'

export type CampaignMode = 'ai_cold_b2b_us' | 'ai_consented' | 'human_assisted'

export const MODE_LABEL: Record<CampaignMode, string> = {
  ai_cold_b2b_us: 'AI cold calls — US business landlines',
  ai_consented: 'AI calls — people who asked for one',
  human_assisted: 'You talk, AI takes notes',
}

export const MODE_HELP: Record<CampaignMode, string> = {
  ai_cold_b2b_us: 'US only. Mobiles, do-not-call numbers and switched-off states are blocked automatically.',
  ai_consented: 'Only numbers with a written consent record (the "get a call from our AI" form).',
  human_assisted: 'The system dials for you and skips answering machines. You talk; the AI summarises.',
}

export interface GateDecision {
  allowed: boolean
  failures: string[]
  permanent: boolean
  checks: Record<string, string>
  at: string
  mode: string
  retry_at: string | null
}

export interface SalesLead {
  id: string
  business_name: string
  contact_name: string
  phone: string
  email: string
  vertical: string
  country: string
  region_code: string
  timezone: string
  timezones: string[]
  source: string
  line_type: string
  status: string
  campaign_id: string | null
  attempts: number
  last_attempt_at: string | null
  next_eligible_at: string | null
  last_outcome?: string
  notes: string
  tags: string[]
  gate: GateDecision | null
  gate_log?: GateDecision[]
  created_at: string
}

export interface CampaignProgress {
  leads: Record<string, number>
  total_leads: number
  to_dial: number
  active_calls: number
  dialled_24h: number
  demos: number
}

export interface Campaign {
  id: string
  name: string
  mode: CampaignMode
  status: 'draft' | 'running' | 'paused' | 'completed'
  persona_ids: string[]
  from_numbers: string[]
  calling_window: { start: string; end: string; days: number[] }
  max_attempts: number
  retry_after_hours: number[]
  max_concurrent: number
  daily_cap: number
  voicemail_policy: 'hang_up' | 'leave_message'
  vertical: string
  notes: string
  legal_review: { confirmed_by: string; at: string; note: string } | null
  progress?: CampaignProgress
}

export type CampaignInput = Omit<Campaign, 'id' | 'status' | 'legal_review' | 'progress'>

export interface Attempt {
  id: string
  lead_id: string
  campaign_id: string
  call_id: string
  mode: CampaignMode
  from: string
  to: string
  persona_id: string
  status: string
  outcome: string | null
  amd: string | null
  amd_label: 'human' | 'machine' | null
  sip_status: number | null
  attempt_no: number
  created_at: string
  started_at: string | null
  duration_sec: number | null
  summary: string | null
  next_step: string | null
  cost_usd: number
  detail?: string
  business_name?: string
  contact_name?: string
}

export interface SalesStatus {
  ready: boolean
  problems: string[]
  dialer_enabled: boolean
  line_type_lookup: boolean
  company: string
  registries: { list: string; refreshed_at: string; area_codes: string[]; stale_area_codes: string[]; rows: number }[]
  registry_max_age_days: number
  blocked_us_states: string[]
  modes: { key: CampaignMode; countries: string[] }[]
  personas: { id: string; label: string; opener: string }[]
  consent_campaign_id: string | null
  counts: { leads: number; dnc: number }
}

export interface Funnel {
  funnel: {
    stages: { key: string; label: string; count: number }[]
    answer_rate: number | null
    demo_rate: number | null
    opt_outs: number
    opt_out_rate: number | null
    cost_usd: number
    cost_per_demo_usd: number | null
    by_outcome: Record<string, number>
    by_persona: { persona_id: string; dialled: number; human: number; demo_booked: number; demo_rate: number | null }[]
  }
  amd: { labelled: number; correct: number; accuracy: number | null; hung_up_on_people: number; talked_to_machines: number }
  outcomes: string[]
}

export interface ImportResult {
  headers: string[]
  mapping: Record<string, string>
  fields: string[]
  rows: number
  imported: number
  invalid: number
  duplicates_in_file: number
  already_leads: number
  customers: number
  blocked: number
  invalid_rows: number[]
  dry_run: boolean
  error?: string
}

export interface JoinInfo {
  token: string
  url: string
  room: string
  mode: 'listen' | 'takeover'
}

type Params = Record<string, string | number | boolean | null | undefined>
const BASE = '/platform/sales'
const NO_TENANT = { tenant: null } as const // platform-level: never send X-Tenant-Id

const get = <T,>(path: string, params?: Params) => api<T>(BASE + path, { params, ...NO_TENANT })
const send = <T,>(path: string, method: 'POST' | 'PATCH', body?: unknown) =>
  api<T>(BASE + path, { method, body: body ?? {}, ...NO_TENANT })

function useDone(...keys: string[]) {
  const qc = useQueryClient()
  return () => {
    for (const key of keys) void qc.invalidateQueries({ queryKey: [key] })
  }
}

export const useSalesStatus = () => useQuery({ queryKey: ['s-status'], queryFn: () => get<SalesStatus>('/status') })

export const useCampaigns = () =>
  useQuery({ queryKey: ['s-campaigns'], queryFn: () => get<{ campaigns: Campaign[] }>('/campaigns'), refetchInterval: 15_000 })

export const useSalesLeads = (params: Params) =>
  useQuery({
    queryKey: ['s-leads', params],
    queryFn: () =>
      get<{ leads: SalesLead[]; total: number; page: number; pages: number; by_status: Record<string, number>; sources: string[] }>(
        '/leads', params),
    placeholderData: keepPreviousData,
  })

export const useSalesLead = (id: string | null) =>
  useQuery({
    queryKey: ['s-lead', id],
    queryFn: () => get<{ lead: SalesLead; attempts: Attempt[]; consent: { type: string; source: string; at: string; revoked_at: string | null }[] }>(`/leads/${id}`),
    enabled: !!id,
  })

export const useLiveSalesCalls = (enabled = true) =>
  useQuery({ queryKey: ['s-live'], queryFn: () => get<{ calls: Attempt[]; total: number }>('/live'), refetchInterval: 4_000, enabled })

export const useAttempts = (params: Params) =>
  useQuery({
    queryKey: ['s-attempts', params],
    queryFn: () => get<{ attempts: Attempt[]; total: number; page: number; pages: number }>('/attempts', params),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  })

export const useAttempt = (id: string | null) =>
  useQuery({
    queryKey: ['s-attempt', id],
    queryFn: () =>
      get<{ attempt: Attempt; lead: SalesLead | null; transcript: { role: string; text: string }[] }>(
        `/attempts/${id}`),
    enabled: !!id,
  })

export const useFunnel = (params: Params) =>
  useQuery({ queryKey: ['s-funnel', params], queryFn: () => get<Funnel>('/funnel', params), refetchInterval: 60_000 })

export const useDnc = (params: Params) =>
  useQuery({
    queryKey: ['s-dnc', params],
    queryFn: () => get<{ entries: { phone: string; reason: string; source: string; added_at: string }[]; total: number; page: number; pages: number }>('/dnc', params),
    placeholderData: keepPreviousData,
  })

export function useSalesMutations() {
  const campaigns = useDone('s-campaigns')
  const leads = useDone('s-leads', 's-lead', 's-campaigns', 's-status', 's-dnc')
  const results = useDone('s-attempts', 's-attempt', 's-funnel', 's-leads')
  const live = useDone('s-live', 's-campaigns')
  return {
    createCampaign: useMutation({ mutationFn: (body: CampaignInput) => send<{ campaign: Campaign }>('/campaigns', 'POST', body), onSuccess: campaigns }),
    updateCampaign: useMutation({
      mutationFn: ({ id, ...body }: Partial<CampaignInput> & { id: string }) => send<{ campaign: Campaign }>(`/campaigns/${id}`, 'PATCH', body),
      onSuccess: campaigns,
    }),
    start: useMutation({
      mutationFn: ({ id, ...body }: { id: string; legal_review_confirmed?: boolean; note?: string }) => send(`/campaigns/${id}/start`, 'POST', body),
      onSuccess: campaigns,
    }),
    pause: useMutation({ mutationFn: (id: string) => send(`/campaigns/${id}/pause`, 'POST'), onSuccess: campaigns }),
    importLeads: useMutation({ mutationFn: (body: Record<string, unknown>) => send<ImportResult>('/leads/import', 'POST', body), onSuccess: leads }),
    queueLeads: useMutation({
      mutationFn: (body: { campaign_id: string; source?: string; tag?: string; lead_ids?: string[] }) =>
        send<{ matched: number; queued: number; blocked: number }>('/leads/queue', 'POST', body),
      onSuccess: leads,
    }),
    checkLead: useMutation({ mutationFn: (id: string) => send<{ decision: GateDecision }>(`/leads/${id}/check`, 'POST'), onSuccess: leads }),
    dncLead: useMutation({ mutationFn: (id: string) => send(`/leads/${id}/dnc`, 'POST'), onSuccess: leads }),
    addDnc: useMutation({ mutationFn: (phone: string) => send<{ added: boolean; phone: string }>('/dnc', 'POST', { phone }), onSuccess: leads }),
    join: useMutation({ mutationFn: ({ id, mode }: { id: string; mode: 'listen' | 'takeover' }) => send<JoinInfo>(`/live/${id}/join`, 'POST', { mode }) }),
    hangUp: useMutation({ mutationFn: (id: string) => send(`/live/${id}/hangup`, 'POST'), onSuccess: live }),
    dialNext: useMutation({
      mutationFn: (campaignId: string) =>
        send<{ dialed: boolean; reason?: string; attempt_id?: string; lead?: SalesLead; join?: JoinInfo }>(`/campaigns/${campaignId}/dial-next`, 'POST'),
      onSuccess: live,
    }),
    patchAttempt: useMutation({
      mutationFn: ({ id, ...body }: { id: string; outcome?: string; amd_label?: 'human' | 'machine' }) => send(`/attempts/${id}`, 'PATCH', body),
      onSuccess: results,
    }),
  }
}

const FAILURE_TEXT: Record<string, string> = {
  internal_dnc: 'On your do-not-call list',
  country_not_allowed: 'This kind of call is not allowed in that country',
  state_blocked: 'AI cold calls to that state are switched off',
  state_unknown: 'US state unknown',
  line_type_not_allowed: 'Mobile or unknown line',
  line_type_unchecked: 'Line type not checked yet',
  registry_listed: 'On a national do-not-call registry',
  registry_not_ready: 'Do-not-call registry missing or out of date',
  no_consent: 'No consent record',
  consent_not_written: 'Consent is not in writing',
  max_attempts: 'Maximum attempts reached',
  too_soon: 'Not due yet',
  timezone_unknown: 'Timezone unknown',
  outside_hours: 'Outside calling hours',
}

export const failureText = (code: string) => FAILURE_TEXT[code] ?? code.replaceAll('_', ' ')

export const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
