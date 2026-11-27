/** Outbound sales dialer (Roadmap Phase 4; SALES_DIALER_PLAN.md): types and data hooks for /platform/sales. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'

export type CampaignMode = 'ai_cold_b2b_us' | 'ai_consented' | 'human_assisted'

export const MODE_LABEL: Record<CampaignMode, string> = {
  ai_cold_b2b_us: 'AI cold calls — business landlines',
  ai_consented: 'AI calls — people who asked for one',
  human_assisted: 'You talk, AI takes notes',
}

export const MODE_HELP: Record<CampaignMode, string> = {
  ai_cold_b2b_us: 'US business landlines (other countries only once counsel switches them on). Mobiles, do-not-call numbers and switched-off states are blocked automatically.',
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
  /** What the agent should know about this business before calling (goes into the call). */
  agent_notes?: string
  tags: string[]
  website?: string
  whatsapp?: string
  whatsapp_consent_at?: string | null
  /** The last estimate the agent gave them on a call. */
  roi_estimate?: { amount: number; currency: string; label: string; at: string } | null
  research?: Research | null
  qualification?: Qualification
  declined_offers?: string[]
  cooldown_until?: string | null
  offer_history?: { offer_id: string; campaign_id: string; attempt_id: string; outcome: string; reason: string; at: string }[]
  demo?: { appointment_id: string; start: string; offer_id?: string }
  gate: GateDecision | null
  gate_log?: GateDecision[]
  created_at: string
  /** The built-in CRM (SALES_SUITE_PLAN.md wave 3). */
  owner_id?: string
  fit?: Fit | null
  fit_score?: number | null
  contacts?: Contact[]
  custom?: Record<string, unknown>
  email_consent_at?: string | null
  replied_at?: string | null
  merged_into?: string
  /** What the agent will know on the next call (GET /leads/{id} only; SALES_LEARNING_PLAN.md Phase 1). */
  memory?: LeadMemory
  memory_cleared_at?: string | null
}

/** The <earlier_calls> lines the agent gets about this business, built from earlier calls. */
export interface LeadMemory {
  lines: string[]
  cleared_at: string | null
  enabled: boolean
  days: number
}

/** What was read on the lead's website before the call (backend core/outbound/research.py). */
export interface Research {
  status: 'pending' | 'running' | 'done' | 'failed' | 'no_website'
  url?: string
  fetched_at: string | null
  expires_at: string | null
  cost_usd?: number
  error?: string
  data: {
    summary: string
    services: string[]
    location: string
    size_signals: string
    tech_signals: { website_quality: string; online_booking: boolean | null; live_chat: boolean | null; cms_guess: string }
    likely_pains: string[]
    hook: string
    contact_names: string[]
    emails: string[]
    confidence: number
  } | null
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
  /** What the campaign sells: a published offer version and 1–2 of its scripts. None = the built-in LaudLine offer. */
  offer_id: string | null
  offer_version: number | null
  variant_ids: string[]
  legal_review: { confirmed_by: string; at: string; note: string } | null
  progress?: CampaignProgress
  /** When the campaign pauses itself (SALES_SUITE_PLAN.md Phase 11). */
  guardrails: Guardrails
  /** Best-fit leads first, or first in first out. */
  order: 'fit' | 'fifo'
  owner_id: string
  /** Retries at the hours businesses pick up (inside the campaign and legal windows); missing = fixed. */
  retry_timing?: 'fixed' | 'smart'
  /** Calls drift toward the approved script that does better; missing = an even split. */
  script_split?: 'even' | 'auto'
  /** The current split between scripts (GET /campaigns): shares, and the chance each is the better one. */
  scripts?: { alloc: Record<string, number>; how: 'even' | 'auto'; p_best?: Record<string, number> } | null
  paused_reason?: { code: string; detail: string; at: string; trips: { code: string; detail: string }[] } | null
}

export interface Guardrails {
  enabled: boolean
  window_hours: number
  max_optout_rate: number
  min_humans_for_optout: number
  min_answer_rate: number
  min_dials_for_answer: number
  max_error_rate: number
  min_dials_for_error: number
  max_cost_per_meeting_usd: number | null
}

export const DEFAULT_GUARDRAILS: Guardrails = {
  enabled: true, window_hours: 24, max_optout_rate: 0.05, min_humans_for_optout: 10, min_answer_rate: 0.05,
  min_dials_for_answer: 40, max_error_rate: 0.2, min_dials_for_error: 20, max_cost_per_meeting_usd: null,
}

export type CampaignInput = Omit<Campaign, 'id' | 'status' | 'legal_review' | 'progress' | 'paused_reason'>

// ── Safe launch (SALES_SUITE_PLAN.md wave 2) ────────────────────────────────

export interface PreflightCheck { id: string; label: string; status: 'pass' | 'warn' | 'fail'; detail: string; fix: string }

export interface CampaignDraft extends Omit<CampaignInput, 'guardrails' | 'order' | 'owner_id' | 'persona_ids'> {
  lead_count: number
  countries: string[]
  reasons: string[]
  warnings: string[]
}

export interface SimulationResult {
  scenario: string
  label: string
  passed: boolean
  reason: string
  tools: string[]
  transcript?: { role: string; text: string }[]
  variant_id: string
}

export interface Simulation {
  id: string
  offer_id: string
  version: number | null
  checksum: string
  variant_ids: string[]
  status: 'queued' | 'running' | 'done' | 'failed'
  results: SimulationResult[]
  passed: number
  total: number
  cost_usd: number
  error: string
  created_at: string
  finished_at: string | null
}

export interface NumberHealth {
  number: string
  country: string
  dials: number
  answer_rate: number | null
  human_rate: number | null
  short_hangup_rate: number | null
  opt_outs: number
  previous: { dials: number; answer_rate: number | null }
  suspect: string
  rested_until: string | null
  rest_reason: string
}

/** Settings that need a fresh lawyer's review when they change (backend models.MATERIAL_FIELDS). */
export const MATERIAL_FIELDS = ['mode', 'offer_id', 'offer_version', 'variant_ids', 'persona_ids', 'from_numbers', 'voicemail_policy'] as const

// ── Offers: what the dialer sells ────────────────────────────────────────────

export interface Objection { objection: string; response: string }

export interface ScriptVariant {
  id: string
  label: string
  /** Spoken word for word by the AI: must say it's an AI, name the company and offer an opt-out. */
  cold_opener: string
  first_question: string
  pitch: string
  /** A cheat sheet for a person on human-assisted calls. */
  human_opener: string
}

export interface EmailTemplate { subject: string; body: string }

export interface RoiInput { question: string; label: string; per: 'week' | 'month' | 'once'; max: number }
export interface RoiFactor { label: string; value: number; percent: boolean }
export interface OfferRoi {
  enabled: boolean
  inputs: RoiInput[]
  factors: RoiFactor[]
  result_label: string
  monthly_cost: number | null
}
export interface OfferChannels { whatsapp: boolean; whatsapp_template: string; whatsapp_language: string; booking_url: string }

export interface OfferSettings {
  name: string
  brand: 'laudline' | 'weblaud' | 'other'
  company_name: string
  persona_name: string
  website: string
  what_we_sell: string
  ideal_customer: string
  value_props: string[]
  proof_points: string[]
  discovery_questions: string[]
  qualification_questions: string[]
  pain_hypotheses: string[]
  objections: Objection[]
  pricing: { mode: 'never' | 'range'; text: string }
  /** What it could be worth to them: their numbers × your figures, worked out by a tool on the call. */
  roi: OfferRoi
  call_goal: 'book_meeting' | 'send_info' | 'transfer'
  meeting: { label: string; length_min: number; service_name: string; url: string; host_name: string }
  /** Where details go when they won't book on the call. */
  channels: OfferChannels
  info_email: EmailTemplate
  confirmation_email: EmailTemplate
  consented_opener: string
  /** Spoken when they asked us to call back; empty = the usual opener. Same rules as every opener. */
  callback_opener: string
  voicemail: string
  consent_text: string
  variants: ScriptVariant[]
  /** Up to 3 emails after "send me info" or a booking, only to someone who agreed to email. */
  follow_ups?: { after_days: number; subject: string; body: string }[]
  /** A new deal's amount for this offer. */
  deal_value_default?: number | null
  deal_currency?: string
}

export interface Offer extends OfferSettings {
  id: string
  status: 'draft' | 'active' | 'archived'
  published_version: number | null
  updated_at: string
  updated_by?: string
  /** Facts the AI wrote (proof points, the price sentence, consent wording) that nobody has confirmed yet. */
  ai_unconfirmed?: string[]
  last_review?: { at: string; by: string; checksum: string; result: OfferReview } | null
}

/** A save of the draft: AI facts still to confirm, and the updated_at this editor loaded (409 when stale). */
export type OfferDraft = OfferSettings & { ai_unconfirmed?: string[]; expected_updated_at?: string; autosave?: boolean }

/** One problem (stops publishing) or warning, tied to the field it's about ("variants.1.cold_opener"). */
export interface OfferIssue { level: 'problem' | 'warning'; field: string; message: string }

export interface OfferSummary {
  id: string
  name: string
  brand: OfferSettings['brand']
  company_name: string
  status: Offer['status']
  published_version: number | null
  published: { version: number; published_at: string; variants: { id: string; label: string }[] } | null
  campaigns: number
  running_campaigns: number
  /** The learning loop: the live playbook and the test running (SALES_LEARNING_PLAN.md). */
  learning?: { mode: 'autopilot' | 'paused' | 'off'; live_n: number | null
    testing: { n: number | null; done: number; next: number | null; status: string } | null } | null
  updated_at: string
}

export interface OfferPreview {
  opener: string
  consented_opener: string
  callback_opener?: string
  voicemail: string
  consent_text: string
  prompt: string
  approx_tokens: number
  problems: string[]
  warnings: string[]
  issues?: OfferIssue[]
}

// ── The AI offer copilot (SALES_SUITE_PLAN.md wave 1) ───────────────────────

export interface AiAssistStatus { enabled: boolean; reason: string; spent_today_usd: number; daily_cap_usd: number; model: string }
export type Market = 'AU' | 'GB' | 'US' | 'CA'

export interface GenerateOfferInput {
  brief: string
  website?: string
  company_name?: string
  persona_name?: string
  brand?: OfferSettings['brand']
  market?: Market
  call_goal?: OfferSettings['call_goal']
  pricing_hint?: string
}

export interface GeneratedOffer {
  draft: OfferSettings
  issues: OfferIssue[]
  ai_unconfirmed: string[]
  /** "proof_points.0" → the page it was found on */
  sources: Record<string, string>
  notes: string
  warnings: string[]
  pages_read: number
  cost_usd: number
}

export type AssistSection =
  | 'basics' | 'value_props' | 'proof_points' | 'discovery_questions' | 'qualification_questions' | 'pain_hypotheses'
  | 'objections' | 'variant' | 'fix_opener' | 'emails' | 'consent_voicemail' | 'follow_ups'

export interface AssistInput {
  offer: Partial<OfferSettings>
  section: AssistSection
  action?: 'add' | 'rewrite'
  target?: string
  instruction?: string
  market?: Market
}

export interface AssistResult { patch: Partial<OfferSettings>; ai_unconfirmed: string[]; notes: string; cost_usd: number }

export interface ReviewFinding {
  severity: 'high' | 'medium' | 'low'
  area: 'clarity' | 'spoken' | 'claims' | 'compliance' | 'flow'
  field: string
  issue: string
  fix: { field: string; value: string; unconfirmed: boolean } | null
}

export interface OfferReview { score: number; summary: string; findings: ReviewFinding[]; checksum: string; cost_usd: number }

export interface OfferVersionFull {
  id: string
  offer_id: string
  version: number
  settings: OfferSettings
  checksum: string
  published_by: string
  published_by_name?: string
  published_at: string
}

export const GENERATE_STREAM_PATH = '/platform/sales/offers/generate/stream'
export const generateOffer = (body: GenerateOfferInput) => send<GeneratedOffer>('/offers/generate', 'POST', body)
export const assistOffer = (body: AssistInput) => send<AssistResult>('/offers/assist', 'POST', body)
export const reviewOffer = (id: string, body: { offer?: Partial<OfferSettings>; market?: Market }) =>
  send<OfferReview>(`/offers/${id}/review`, 'POST', body)

export const BRAND_LABEL: Record<OfferSettings['brand'], string> = { laudline: 'LaudLine', weblaud: 'WebLaud', other: 'Other' }
export const GOAL_LABEL: Record<OfferSettings['call_goal'], string> = {
  book_meeting: 'Book a meeting',
  send_info: 'Get permission to email information',
  transfer: 'Put them through to a person now',
}

export interface Attempt {
  id: string
  lead_id: string
  campaign_id: string
  call_id: string
  mode: CampaignMode
  from: string
  to: string
  persona_id: string
  offer_id?: string
  offer_version?: number
  variant_id?: string
  reason?: string
  qualification?: Qualification
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
  /** The learning loop (SALES_LEARNING_PLAN.md): who ended it, next steps a tool confirmed, fixed-rule checks, the coach. */
  ended_by?: 'agent' | 'prospect' | 'system' | null
  hangup_stage?: 'opener' | 'early' | 'later' | null
  events?: string[]
  checks?: CallChecks
  review?: CallReview | null
  playbook_id?: string | null
  arm?: 'champion' | 'challenger' | 'off' | null
  experiment_id?: string | null
  playbook_applied?: boolean
  memory_lines?: string[]
}

/** Fixed-rule checks on what the agent said, worked out without a model (backend core/outbound/coach.scan). */
export interface CallChecks {
  severe: string[]
  other: string[]
  details?: { kind: string; turn: number; detail: string }[]
  long_turns?: number
}

export const COACH_STAGES = ['bridge', 'discovery', 'listening', 'value', 'objections', 'close', 'tone'] as const
export type CoachStage = (typeof COACH_STAGES)[number]

/** The AI coach's review of one call (backend core/outbound/coach.py). Scores are for reading, not for decisions. */
export interface CallReview {
  status: 'pending' | 'running' | 'done' | 'skipped' | 'failed'
  reason?: string
  error?: string
  tries?: number
  at?: string
  rubric_version?: string
  model?: string
  metrics?: {
    agent_turns: number; prospect_turns: number; talk_ratio: number | null; longest_turn_words: number
    long_turns: number; questions: number; prospect_questions: number; interruptions: number
    latency_p50_s?: number | null; latency_p95_s?: number | null
  }
  scores?: Partial<Record<CoachStage, number | null>>
  overall?: number | null
  moments?: { turn: number | null; kind: string; prospect_said: string; agent_did: string; better: string }[]
  lessons?: { situation: string; do: string; why: string; kind: string; objection_key: string }[]
  unanswered?: string[]
  flags?: { kind: string; turn: number | null; detail: string }[]
  entry_usage?: { entry_id: string; used: boolean; helped: boolean | null }[]
  ended_because?: string
  text_cleared?: boolean
  cost_usd?: number
}

/** What the agent noted after the call (backend core/outbound/qualify.py). */
export interface Qualification {
  decision_maker: boolean | null
  role: string
  pains: string[]
  current_solution: string
  budget_signal: 'none' | 'low' | 'medium' | 'high' | 'unknown'
  timeline: string
  interest: number
  objections: string[]
  next_step: string
  follow_up_by: string
  /** Who was named or spoken to, whether they screen calls, and when the decision-maker is best reached. */
  people?: { name: string; role: string }[]
  gatekeeper?: boolean | null
  best_time_to_reach?: string
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
  research: { enabled: boolean; spent_today_usd: number; daily_cap_usd: number; pending: number }
  ai_assist?: AiAssistStatus
  /** Australian leads with no fresh Do Not Call Register wash (they can't be called until washed). */
  au_dncr_needs_wash: number
  counts: { leads: number; dnc: number }
}

export interface Funnel {
  funnel: {
    stages: { key: string; label: string; count: number }[]
    answer_rate: number | null
    demo_rate: number | null
    /** Businesses reached, and how many took a real next step (verified: meeting, details sent, a kept callback). */
    reached?: number
    next_steps?: number
    next_step_rate?: number | null
    opt_outs: number
    opt_out_rate: number | null
    cost_usd: number
    cost_per_demo_usd: number | null
    by_outcome: Record<string, number>
    by_persona: { persona_id: string; dialled: number; human: number; demo_booked: number; demo_rate: number | null }[]
    by_variant: { offer_id: string; offer_version: number; variant_id: string; label: string; dialled: number; human: number; demo_booked: number; demo_rate: number | null; reached?: number; next_steps?: number; next_step_rate?: number | null }[]
  }
  amd: { labelled: number; correct: number; accuracy: number | null; hung_up_on_people: number; talked_to_machines: number }
  outcomes: string[]
  /** What a person may set by hand (never the system-only outcomes, and never "dnc"). */
  manual_outcomes: string[]
  outcome_labels: Record<string, string>
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
    queryFn: () => get<{
      lead: SalesLead & { stage: Stage; history?: { campaign_id: string; offer_id: string; offer_version: number; status: string; attempts: number; last_outcome: string | null; until: string }[] }
      attempts: Attempt[]
      activity: Activity[]
      tasks: SalesTask[]
      consent: { type: string; source: string; at: string; revoked_at: string | null }[]
      deals: Deal[]
      scheduled_emails: ScheduledEmail[]
      email_allowed: boolean
    }>(`/leads/${id}`),
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

export const useAttempt = (id: string | null, refetchInterval: number | false = false) =>
  useQuery({
    queryKey: ['s-attempt', id],
    queryFn: () =>
      get<{ attempt: Attempt & { answered_at?: string | null }; lead: SalesLead | null; transcript: { role: string; text: string }[] }>(
        `/attempts/${id}`),
    enabled: !!id,
    refetchInterval,
  })

export const useFunnel = (params: Params) =>
  useQuery({ queryKey: ['s-funnel', params], queryFn: () => get<Funnel>('/funnel', params), refetchInterval: 60_000 })

export const useDnc = (params: Params) =>
  useQuery({
    queryKey: ['s-dnc', params],
    queryFn: () => get<{ entries: { phone: string; reason: string; source: string; added_at: string }[]; total: number; page: number; pages: number }>('/dnc', params),
    placeholderData: keepPreviousData,
  })

export const useOffers = (includeArchived = false) =>
  useQuery({ queryKey: ['s-offers', includeArchived], queryFn: () => get<{ offers: OfferSummary[] }>('/offers', { include_archived: includeArchived }) })

export const useOffer = (id: string | null) =>
  useQuery({
    queryKey: ['s-offer', id],
    queryFn: () =>
      get<{
        offer: Offer
        summary: OfferSummary
        versions: { id: string; version: number; published_at: string; published_by: string; published_by_name?: string; checksum: string }[]
        campaigns: { id: string; name: string; status: Campaign['status']; offer_version: number }[]
        problems: string[]
        warnings: string[]
        issues: OfferIssue[]
        ai_unconfirmed: string[]
        last_review: Offer['last_review']
      }>(`/offers/${id}`),
    enabled: !!id,
  })

export const useOfferVersion = (id: string | null, version: number | null) =>
  useQuery({
    queryKey: ['s-offer-version', id, version],
    queryFn: () => get<{ version: OfferVersionFull; campaigns: { id: string; name: string; status: string }[] }>(`/offers/${id}/versions/${version}`),
    enabled: !!id && !!version,
    staleTime: Infinity, // published versions never change
  })

export const previewOffer = (id: string, body: { offer?: OfferSettings; variant_id?: string; lead_id?: string; mode?: CampaignMode; ai_unconfirmed?: string[]; playbook_id?: string }) =>
  send<OfferPreview>(`/offers/${id}/preview`, 'POST', body)

/** Talk to the agent in the browser for this offer: a dry run, nothing is dialled, booked or sent. */
export const startTestCall = (id: string, body: { offer?: OfferSettings; variant_id?: string; business_name?: string; timezone?: string; playbook_id?: string }) =>
  send<JoinInfo & { max_seconds: number }>(`/offers/${id}/test-call`, 'POST', body)

export function useOfferMutations() {
  const done = useDone('s-offers', 's-offer', 's-campaigns')
  return {
    create: useMutation({ mutationFn: (body: OfferDraft) => send<{ offer: Offer; issues: OfferIssue[] }>('/offers', 'POST', body), onSuccess: done }),
    update: useMutation({
      mutationFn: ({ id, ...body }: OfferDraft & { id: string }) => send<{ offer: Offer; issues: OfferIssue[] }>(`/offers/${id}`, 'PATCH', body),
      onSuccess: done,
      meta: { silentError: true }, // the editor shows a stale draft (409) as a banner and its own messages
    }),
    restore: useMutation({
      mutationFn: ({ id, version, expected_updated_at }: { id: string; version: number; expected_updated_at?: string }) =>
        send<{ offer: Offer; issues: OfferIssue[] }>(`/offers/${id}/restore`, 'POST', { version, expected_updated_at }),
      onSuccess: done,
    }),
    publish: useMutation({ mutationFn: (id: string) => send<{ version: number }>(`/offers/${id}/publish`, 'POST'), onSuccess: done }),
    archive: useMutation({ mutationFn: (id: string) => send(`/offers/${id}/archive`, 'POST'), onSuccess: done }),
    unarchive: useMutation({ mutationFn: (id: string) => send(`/offers/${id}/unarchive`, 'POST'), onSuccess: done }),
    duplicate: useMutation({ mutationFn: (id: string) => send<{ offer: Offer }>(`/offers/${id}/duplicate`, 'POST'), onSuccess: done }),
  }
}

// ── Pipeline, tasks, meetings, email (SALES_DIALER_PLAN.md Phase 3) ──────────

export const PIPELINE_STAGES = ['new', 'contacted', 'interested', 'meeting_booked', 'proposal', 'won', 'lost'] as const
export type Stage = (typeof PIPELINE_STAGES)[number]
export const STAGE_LABEL: Record<Stage, string> = {
  new: 'New', contacted: 'Contacted', interested: 'Interested', meeting_booked: 'Meeting booked',
  proposal: 'Proposal sent', won: 'Won', lost: 'Lost',
}

export interface PipelineLead {
  id: string
  business_name: string
  contact_name: string
  phone: string
  email: string
  status: string
  stage: Stage
  open_tasks: number
  demo?: { appointment_id: string; start: string }
  qualification?: Qualification
  updated_at: string
}

export interface SalesTask {
  id: string
  lead_id: string
  title: string
  due_at: string | null
  owner: string
  source: 'agent' | 'manual' | 'wrapup'
  done_at: string | null
  cancelled: boolean
  created_at: string
  lead?: { id: string; business_name: string; contact_name: string; phone: string } | null
}

export interface Activity { id: string; type: 'call' | 'note' | 'email' | 'stage' | 'task' | 'meeting'; text: string; by: string; at: string }

export interface Meeting {
  appointment_id: string
  start: string
  end: string
  status: string
  name: string
  email: string
  service_name: string
  lead: { id: string; business_name: string; contact_name: string; phone: string } | null
}

export interface OutboxEmail { id: string; to: string; subject: string; kind: string; status: string; tries: number; error: string; created_at: string; lead_id: string | null }

export const usePipeline = () =>
  useQuery({ queryKey: ['s-pipeline'], queryFn: () => get<{ columns: Record<string, PipelineLead[]>; counts: Record<string, number> }>('/pipeline'), refetchInterval: 60_000 })

export const useTasks = (params: Params = {}) =>
  useQuery({ queryKey: ['s-tasks', params], queryFn: () => get<{ tasks: SalesTask[] }>('/tasks', params) })

export const useMeetings = () => useQuery({ queryKey: ['s-meetings'], queryFn: () => get<{ meetings: Meeting[] }>('/meetings') })

export const useOutbox = () => useQuery({ queryKey: ['s-outbox'], queryFn: () => get<{ emails: OutboxEmail[] }>('/outbox') })

export async function downloadSalesCsv(kind: 'leads' | 'attempts' | 'dncr-wash' | 'audit', params: Params = {}): Promise<void> {
  const text = await api<string>(`${BASE}/${kind}.csv`, { params, ...NO_TENANT })
  const url = URL.createObjectURL(new Blob([typeof text === 'string' ? text : ''], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  const name = kind === 'dncr-wash' ? 'au-dncr-wash' : kind === 'leads' ? 'sales-leads' : kind === 'audit' ? 'sales-audit' : 'sales-calls'
  a.download = `${name}-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

export type LeadPatch = Partial<Pick<SalesLead, 'business_name' | 'contact_name' | 'email' | 'notes' | 'agent_notes' | 'timezone' | 'region_code' | 'website'>> & { stage?: string; tags?: string[]; owner_id?: string }

export function useFollowUpMutations() {
  const done = useDone('s-pipeline', 's-tasks', 's-lead', 's-leads', 's-outbox', 's-meetings')
  return {
    patchLead: useMutation({ mutationFn: ({ id, ...body }: LeadPatch & { id: string }) => send<{ lead: SalesLead }>(`/leads/${id}`, 'PATCH', body), onSuccess: done }),
    addNote: useMutation({ mutationFn: ({ id, text }: { id: string; text: string }) => send(`/leads/${id}/notes`, 'POST', { text }), onSuccess: done }),
    createTask: useMutation({ mutationFn: (body: { lead_id: string; title: string; due_at?: string | null }) => send<{ task: SalesTask }>('/tasks', 'POST', body), onSuccess: done }),
    updateTask: useMutation({ mutationFn: ({ id, ...body }: { id: string; done?: boolean; cancelled?: boolean; due_at?: string | null }) => send(`/tasks/${id}`, 'PATCH', body), onSuccess: done }),
    retryEmail: useMutation({ mutationFn: (id: string) => send<{ email: OutboxEmail }>(`/outbox/${id}/retry`, 'POST'), onSuccess: done }),
    markEmailSent: useMutation({ mutationFn: (id: string) => send(`/outbox/${id}/mark-sent`, 'POST'), onSuccess: done }),
    forgetMemory: useMutation({ mutationFn: (id: string) => send<{ memory: LeadMemory }>(`/leads/${id}/memory/forget`, 'POST'), onSuccess: done }),
  }
}

/** What a person needs in front of them on a human-assisted call (GET /attempts/{id}/context). */
export interface CallContext {
  attempt: Attempt & { answered_at?: string | null }
  lead: SalesLead | null
  research: Research | null
  earlier_calls: { id: string; outcome: string; summary: string | null; created_at: string }[]
  /** The same <earlier_calls> lines the AI agent would get. */
  memory?: string[]
  cheat_sheet: {
    offer: string
    company: string
    opener: string
    first_question: string
    pitch: string
    discovery_questions: string[]
    qualification_questions: string[]
    value_props: string[]
    proof_points: string[]
    objections: Objection[]
    pricing: string
    meeting: string
  }
}

export interface WrapUpInput {
  outcome: string
  confirm_dnc?: boolean
  notes?: string
  next_step?: string
  callback_at?: string | null
  stage?: string | null
  task?: { title: string; due_at?: string | null } | null
}

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
      mutationFn: ({ id, ...body }: { id: string; legal_review_confirmed?: boolean; note?: string; allow_learning?: boolean }) => send(`/campaigns/${id}/start`, 'POST', body),
      onSuccess: campaigns,
      meta: { silentError: true },          // "legal_review_required" opens the sign-off dialog instead
    }),
    pause: useMutation({ mutationFn: (id: string) => send(`/campaigns/${id}/pause`, 'POST'), onSuccess: campaigns }),
    importLeads: useMutation({ mutationFn: (body: Record<string, unknown>) => send<ImportResult>('/leads/import', 'POST', body), onSuccess: leads }),
    queueLeads: useMutation({
      mutationFn: (body: { campaign_id: string; source?: string; tag?: string; lead_ids?: string[] }) =>
        send<{ matched: number; queued: number; blocked: number; resting: number; reused: number }>('/leads/queue', 'POST', body),
      onSuccess: leads,
    }),
    checkLead: useMutation({ mutationFn: (id: string) => send<{ decision: GateDecision }>(`/leads/${id}/check`, 'POST'), onSuccess: leads }),
    dncLead: useMutation({ mutationFn: (id: string) => send(`/leads/${id}/dnc`, 'POST'), onSuccess: leads }),
    researchLead: useMutation({ mutationFn: (id: string) => send<{ research: Research }>(`/leads/${id}/research`, 'POST'), onSuccess: leads }),
    addDnc: useMutation({ mutationFn: (phone: string) => send<{ added: boolean; phone: string }>('/dnc', 'POST', { phone }), onSuccess: leads }),
    join: useMutation({ mutationFn: ({ id, mode }: { id: string; mode: 'listen' | 'takeover' }) => send<JoinInfo>(`/live/${id}/join`, 'POST', { mode }) }),
    hangUp: useMutation({ mutationFn: (id: string) => send(`/live/${id}/hangup`, 'POST'), onSuccess: live }),
    dialNext: useMutation({
      mutationFn: (campaignId: string) =>
        send<{ dialed: boolean; reason?: string; attempt_id?: string; lead?: SalesLead; join?: JoinInfo; context?: CallContext }>(`/campaigns/${campaignId}/dial-next`, 'POST'),
      onSuccess: live,
    }),
    wrapUp: useMutation({
      mutationFn: ({ id, ...body }: WrapUpInput & { id: string }) => send<{ attempt: Attempt }>(`/attempts/${id}/wrap-up`, 'POST', body),
      onSuccess: () => { results(); live() },
    }),
    patchAttempt: useMutation({
      mutationFn: ({ id, ...body }: { id: string; outcome?: string; amd_label?: 'human' | 'machine'; confirm_dnc?: boolean }) =>
        send(`/attempts/${id}`, 'PATCH', body),
      onSuccess: results,
    }),
    reviewAttempt: useMutation({
      mutationFn: (id: string) => send<{ attempt: Attempt }>(`/attempts/${id}/review`, 'POST'),
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

// ── Sales team (SALES_SUITE_PLAN.md Phase 2) ─────────────────────────────────

export type SalesRole = 'viewer' | 'caller' | 'manager'
export interface TeamMember { id: string; email: string; name: string; role: SalesRole | 'admin'; takes_leads: boolean; disabled: boolean; mfa_enabled: boolean; last_login_at: string | null }

export const useSalesTeam = (enabled = true) => useQuery({ queryKey: ['s-team'], queryFn: () => get<{ members: TeamMember[] }>('/team'), enabled })

export function useTeamMutations() {
  const done = useDone('s-team')
  return {
    add: useMutation({ mutationFn: (body: { email: string; name: string; role: SalesRole; takes_leads: boolean }) => send<{ member: TeamMember; set_password_link: string | null }>('/team', 'POST', body), onSuccess: done }),
    update: useMutation({ mutationFn: ({ id, ...body }: { id: string; role?: SalesRole | 'none'; takes_leads?: boolean }) => send<{ member: TeamMember }>(`/team/${id}`, 'PATCH', body), onSuccess: done }),
  }
}

export const usePreflight = (campaignId: string | null) =>
  useQuery({ queryKey: ['s-preflight', campaignId], queryFn: () => get<{ checks: PreflightCheck[]; can_start: boolean }>(`/campaigns/${campaignId}/preflight`), enabled: !!campaignId })

export const suggestCampaigns = (body: { offer_id: string; source?: string; tag?: string; lead_ids?: string[] }) =>
  send<{ drafts: CampaignDraft[]; warnings: string[]; leads_considered: number }>('/campaigns/suggest', 'POST', body)

export const useSimulations = (offerId: string | null) =>
  useQuery({
    queryKey: ['s-simulations', offerId],
    queryFn: () => get<{ simulations: Simulation[] }>(`/offers/${offerId}/simulations`),
    enabled: !!offerId,
    refetchInterval: (q) => (q.state.data?.simulations.some((x) => x.status === 'queued' || x.status === 'running') ? 5000 : false),
  })

export const useSimulation = (id: string | null) =>
  useQuery({ queryKey: ['s-simulation', id], queryFn: () => get<{ simulation: Simulation }>(`/simulations/${id}`), enabled: !!id })

export const useNumberHealth = () =>
  useQuery({ queryKey: ['s-number-health'], queryFn: () => get<{ numbers: NumberHealth[]; min_dials: number; rest_hours: number }>('/numbers/health') })

export function useSafetyMutations() {
  const sims = useDone('s-simulations')
  const numbers = useDone('s-number-health', 's-preflight')
  return {
    simulate: useMutation({
      mutationFn: ({ offerId, ...body }: { offerId: string; offer?: OfferSettings; version?: number; variant_ids?: string[] }) =>
        send<{ simulation: Simulation; scenarios: string[] }>(`/offers/${offerId}/simulate`, 'POST', body),
      onSuccess: sims,
    }),
    rest: useMutation({ mutationFn: ({ number, hours }: { number: string; hours?: number }) => send(`/numbers/${encodeURIComponent(number)}/rest`, 'POST', { hours: hours ?? 72 }), onSuccess: numbers }),
    reactivate: useMutation({ mutationFn: (number: string) => send(`/numbers/${encodeURIComponent(number)}/reactivate`, 'POST'), onSuccess: numbers }),
  }
}

// ── The built-in CRM (SALES_SUITE_PLAN.md wave 3) ───────────────────────────

export const DEAL_STAGES = ['qualified', 'meeting_booked', 'proposal', 'won', 'lost'] as const
export type DealStage = (typeof DEAL_STAGES)[number]
export const DEAL_STAGE_LABEL: Record<DealStage, string> = {
  qualified: 'Qualified', meeting_booked: 'Meeting booked', proposal: 'Proposal', won: 'Won', lost: 'Lost',
}

export interface Deal {
  id: string
  lead_id: string
  offer_id: string | null
  campaign_id: string | null
  title: string
  amount: number
  currency: string
  stage: DealStage
  probability: number
  expected_close: string | null
  owner_id: string
  lost_reason: string
  proposal: { sent_at: string; amount: number; link: string } | null
  custom: Record<string, unknown>
  won_at: string | null
  created_at: string
  updated_at: string
  lead?: { id: string; business_name?: string; contact_name?: string; phone?: string; city?: string; country?: string }
}

export interface Contact { id: string; name: string; role: string; email: string; phone: string; primary: boolean; notes: string; archived: boolean; email_consent_at: string | null }
export interface Person { id: string; name: string; role: string }
export interface CustomField { id: string; key: string; label: string; type: 'text' | 'number' | 'date' | 'select'; options: string[]; applies_to: 'lead' | 'deal' }
export interface SavedView { id: string; name: string; kind: 'leads' | 'deals'; filters: Record<string, string | number>; shared: boolean; owner: string }
export interface ScheduledEmail { id: string; subject: string; send_after: string; step: number; sequence_trigger: string }
export interface Fit { score: number; source: 'research' | 'rules'; reasons: string[] }

export interface Board {
  columns: Record<DealStage, Deal[]>
  totals: Record<DealStage, { count: number; value: number; weighted: number }>
  probability: Record<DealStage, number>
}

export interface MyWork {
  tasks: SalesTask[]
  overdue: number
  deals: Deal[]
  leads: { id: string; business_name: string; status: string; stage?: string; phone: string }[]
  pipeline_value: number
}

export interface Insights {
  days: number
  calls: number
  answered: number
  outcomes: Record<string, number>
  scripts: {
    variants: { offer_id: string; offer_version: number; variant_id: string; dialled: number; human: number; demo_booked: number; demo_rate: number | null }[]
    leader: string | null
    note: string
  }
  objections: { text: string; count: number; covered: boolean }[]
  suggestions: { objections: Objection[]; discovery_questions: string[]; pain_hypotheses: string[] } | null
  note: string
  cached?: boolean
}

export interface SalesReport {
  days: number
  pipeline: { stages: Record<DealStage, { count: number; value: number; weighted: number }>; open_value: number; forecast: number }
  results: { won: number; lost: number; win_rate: number | null; average_won: number | null; cycle_days: number | null }
  meetings: { total: number; by_offer: Record<string, number>; by_campaign: Record<string, number>; by_source: Record<string, number> }
  activity: Record<string, Record<string, number>>
  funnel: { leads: number; called: number; reached: number; meetings: number; won: number }
}

export interface AuditEntry {
  id: string
  at: string
  user_id: string
  user_name: string
  action: string
  target_type: string
  target_id: string
  ip: string
  diff: Record<string, unknown>
}

export const useBoard = (ownerId?: string) =>
  useQuery({ queryKey: ['s-board', ownerId ?? ''], queryFn: () => get<Board>('/board', { owner_id: ownerId || undefined }) })
export const useMyWork = () => useQuery({ queryKey: ['s-my-work'], queryFn: () => get<MyWork>('/me/work') })
export const usePeople = () => useQuery({ queryKey: ['s-people'], queryFn: () => get<{ people: Person[] }>('/team/people'), staleTime: 60_000 })
export const useCustomFields = () => useQuery({ queryKey: ['s-fields'], queryFn: () => get<{ fields: CustomField[] }>('/fields') })
export const useViews = (kind: 'leads' | 'deals' = 'leads') =>
  useQuery({ queryKey: ['s-views', kind], queryFn: () => get<{ views: SavedView[] }>('/views', { kind }) })
export const useDuplicates = (enabled: boolean) =>
  useQuery({ queryKey: ['s-duplicates'], queryFn: () => get<{ groups: { reason: string; leads: SalesLead[] }[] }>('/crm/duplicates'), enabled })
export const searchSales = (q: string) => get<{ leads: SalesLead[]; deals: Deal[] }>('/search', { q })
export const useReport = (days: number) => useQuery({ queryKey: ['s-report', days], queryFn: () => get<SalesReport>('/reports', { days }) })
export const useAudit = (params: Params) =>
  useQuery({
    queryKey: ['s-audit', params],
    queryFn: () => get<{ entries: AuditEntry[]; total: number; page: number; pages: number }>('/audit', params),
    placeholderData: keepPreviousData,
  })
export const verifyAudit = () => get<{ intact: boolean; problems: string[] }>('/audit/verify')
export const offerInsights = (offerId: string, days: 7 | 30 | 90) => send<Insights>(`/offers/${offerId}/insights`, 'POST', { days })
export const draftLeadEmail = (leadId: string, body: { purpose: 'follow_up' | 'proposal' | 'reply'; deal_id?: string; instruction?: string; market?: Market }) =>
  send<{ subject: string; body: string; cost_usd: number }>(`/leads/${leadId}/draft-email`, 'POST', body)

export function useCrmMutations() {
  const done = useDone('s-board', 's-lead', 's-leads', 's-my-work', 's-pipeline', 's-outbox', 's-views', 's-fields', 's-duplicates', 's-report')
  return {
    createDeal: useMutation({ mutationFn: (body: Partial<Deal> & { lead_id: string; title: string }) => send<{ deal: Deal }>('/deals', 'POST', body), onSuccess: done }),
    updateDeal: useMutation({ mutationFn: ({ id, ...body }: Partial<Deal> & { id: string }) => send<{ deal: Deal }>(`/deals/${id}`, 'PATCH', body), onSuccess: done }),
    addContact: useMutation({
      mutationFn: ({ leadId, ...body }: Partial<Contact> & { leadId: string; name: string }) => send<{ contact: Contact }>(`/leads/${leadId}/contacts`, 'POST', body),
      onSuccess: done,
    }),
    updateContact: useMutation({
      mutationFn: ({ leadId, id, ...body }: Partial<Contact> & { leadId: string; id: string }) => send(`/leads/${leadId}/contacts/${id}`, 'PATCH', body),
      onSuccess: done,
    }),
    merge: useMutation({ mutationFn: ({ keep, merge }: { keep: string; merge: string }) => send(`/leads/${keep}/merge`, 'POST', { merge_id: merge }), onSuccess: done }),
    bulk: useMutation({
      mutationFn: (body: { lead_ids: string[]; action: 'assign' | 'stage' | 'tag' | 'queue'; value: unknown }) => send<Record<string, number>>('/leads/bulk', 'POST', body),
      onSuccess: done,
    }),
    sendEmail: useMutation({
      mutationFn: ({ leadId, ...body }: { leadId: string; to: string; subject: string; text: string }) => send<{ basis: string }>(`/leads/${leadId}/email`, 'POST', body),
      onSuccess: done,
    }),
    emailConsent: useMutation({ mutationFn: ({ leadId, ...body }: { leadId: string; email: string; note: string }) => send(`/leads/${leadId}/email-consent`, 'POST', body), onSuccess: done }),
    stopSequence: useMutation({ mutationFn: (leadId: string) => send<{ cancelled: number }>(`/leads/${leadId}/sequence/stop`, 'POST'), onSuccess: done }),
    setCustom: useMutation({ mutationFn: ({ leadId, values }: { leadId: string; values: Record<string, unknown> }) => send(`/leads/${leadId}/custom`, 'PATCH', values), onSuccess: done }),
    saveView: useMutation({ mutationFn: (body: Omit<SavedView, 'id' | 'owner'>) => send<{ view: SavedView }>('/views', 'POST', body), onSuccess: done }),
    archiveView: useMutation({ mutationFn: (id: string) => send(`/views/${id}`, 'PATCH', { archived: true }), onSuccess: done }),
    createField: useMutation({ mutationFn: (body: Omit<CustomField, 'id'>) => send<{ field: CustomField }>('/fields', 'POST', body), onSuccess: done }),
    archiveField: useMutation({ mutationFn: (id: string) => send(`/fields/${id}`, 'PATCH', { archived: true }), onSuccess: done }),
  }
}
