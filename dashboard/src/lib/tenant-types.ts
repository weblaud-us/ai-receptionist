/** Types for the Phase 2 tenant API (/t/*). */
import type { BillingBrief } from './billing'
import type { HoursEntry } from '@receptionist/shared/lib/hours'

export type { HoursEntry }

/** 'dentist': sees only their own day (My day); ranks below staff. */
export type Role = 'owner' | 'admin' | 'staff' | 'dentist'
export type Vertical =
  | 'home_services'
  | 'salon'
  | 'restaurant'
  | 'hotel'
  | 'clinic'
  | 'dental'
  | 'professional'
  | 'generic'

export interface AgentConfig {
  name: string
  voice_provider: 'elevenlabs' | 'cartesia' | 'openai' | 'deepgram'
  voice_id: string
  language: string
  stt_language: string
  greeting: string
  tone: 'warm' | 'professional' | 'upbeat'
  custom_instructions: string
  llm_model: string
  endpointing: { min_delay: number; max_delay: number }
  ambience: boolean
  /** Owner's own wording for the "this call is transcribed" notice; empty = the standard one. */
  call_notice?: string
  max_call_minutes: number
  transcript_retention_days: number
  stt_keyterms?: string[]
}

/** Day-before confirmation calls (Phase 2.5). */
export interface ConfirmationCallConfig {
  enabled: boolean
  window: string
  only_without_whatsapp: boolean
}

/** Who is paged, in order, when the assistant takes an urgent dental call (Phase 2.3). */
export interface EscalationConfig {
  enabled: boolean
  on_call: { name: string; whatsapp: string; email: string }[]
  ack_minutes: number
}

/** Dental vertical settings (AU private health funds, UK NHS / private mix). */
export interface DentalSettings {
  accepts_nhs: boolean
  accepts_new_nhs_patients: boolean
  accepts_private_health_funds: boolean
  finance_available: boolean
}

/** Pages the assistant may text to a caller with send_info_link. */
export interface Links {
  booking: string
  menu: string
  directions: string
  payment: string
}

export interface WebhookEndpoint {
  id: string
  url: string
  events: string[]
  active: boolean
  created_at?: string | null
  last_delivery?: { at: string; event: string; ok: boolean; status: number | null } | null
}

export interface Voice {
  voice_id: string
  name: string
  preview_url: string | null
  gender?: string | null
  accent?: string | null
  age?: string | null
  description?: string | null
  category?: string | null
}

export interface VoiceCatalogue {
  provider: string
  available: boolean
  voices: Voice[]
  default_voice_id?: string | null
  note?: string
}

export interface Tenant {
  id: string
  name: string
  slug: string
  vertical: Vertical
  status: string
  region: string
  country: string
  timezone: string
  locale: string
  currency: string
  phone: string
  website: string
  description: string
  address: { line1: string; line2: string; city: string; region: string; postal: string; country: string }
  agent: AgentConfig
  hours: HoursEntry[]
  transfer: { number: string; only_during_hours: boolean; hours: string; warm: boolean }
  emergency_instructions: string
  answering_mode?: 'all_calls' | 'overflow' | 'after_hours'
  pilot?: { active: boolean }
  notifications: {
    owner_phone: string
    owner_email: string
    owner_whatsapp: string
    on_new_booking: string[]
    on_message: string[]
    on_urgent: string[]
    daily_digest: boolean
    monthly_report?: boolean
  }
  scheduling: {
    min_notice_minutes: number
    max_days_ahead: number
    slot_step_minutes: number
    default_duration_minutes: number
    cancellation_policy: string
    max_party_size: number
  }
  limits: { included_minutes: number; max_concurrent_calls: number }
  hotel: { checkin_time: string; checkout_time: string }
  dental?: DentalSettings
  escalation?: EscalationConfig
  confirmation_calls?: ConfirmationCallConfig
  /** One text to callers who hang up without booking (template placeholders: {business} {link} {phone}). */
  missed_call_recovery?: { enabled: boolean; template: string; channel: 'sms' | 'whatsapp' }
  /** Registered business, for AU / UK phone-number regulatory bundles. */
  legal_entity?: { registered_name: string; abn: string; company_number: string }
  /** Health-data handling. Dental practices always encrypt clinical text. */
  privacy?: { encrypt_clinical_text: boolean; lead_retention_days: number }
  policies: Record<string, string>
  onboarding: { step?: string; completed_at?: string }
  links: Links
  integrations?: { whatsapp?: { phone_number_id: string }; webhooks?: WebhookEndpoint[] }
}

export interface VerticalLabels {
  appointment: string
  appointments: string
  contact: string
  contacts: string
  resource: string
  resources: string
  service: string
  services: string
}

export interface TenantContext {
  tenant: Tenant
  role: Role
  platform_admin: boolean
  legacy: boolean
  vertical: { key: Vertical; label: string; labels: VerticalLabels; uses_scheduling: boolean; tools: string[] }
  greeting: string
  billing?: BillingBrief
  /** False: the platform sends no SMS, so text-only options are hidden. */
  sms_enabled?: boolean
  /** An outbound trunk is configured, so the assistant can brief staff before transferring. */
  warm_transfer_available?: boolean
  /** An outbound trunk is configured, so the assistant can place confirmation calls. */
  outbound_calls_available?: boolean
}

export interface VerticalInfo {
  key: Vertical
  label: string
  description: string
  labels: VerticalLabels
  default_services: { name: string; duration_min: number }[]
  default_faqs: { question: string; answer: string }[]
}

export type ServiceCategory = 'cosmetic' | 'implant' | 'emergency' | 'routine' | 'other'

export interface Service {
  id: string
  name: string
  duration_min: number
  buffer_min: number
  price?: { amount?: number | null; currency?: string | null; display: string } | null
  /** Typical value of one of these to the business, in minor units of the tenant's currency. */
  estimated_value_minor?: number | null
  category?: ServiceCategory | null
  resource_ids: string[]
  ai_bookable: boolean
  description: string
  active: boolean
  sort: number
}

export interface Resource {
  id: string
  name: string
  kind: 'staff' | 'chair' | 'room' | 'table' | 'bay' | 'technician' | 'other'
  hours: HoursEntry[] | null
  capacity: number
  active: boolean
  sort: number
}

export interface ScheduleException {
  id: string
  resource_id: string | null
  date_from: string
  date_to: string
  reason: string
  /** The assistant mentions it to callers when relevant (plan 2.4). */
  announce?: boolean
  notice?: string
}

export interface Appointment {
  id: string
  appointment_id: string
  name: string
  phone: string
  email?: string | null
  service_id?: string | null
  service_name?: string | null
  resource_id: string
  resource_name?: string | null
  start: string
  end: string
  status: 'pending' | 'confirmed' | 'cancelled' | 'no_show' | 'completed'
  source: string
  notes: string
  party_size?: number | null
  call_id?: string | null
  contact_id?: string | null
  /** Day-before confirmation call (Phase 2.5). */
  confirmation?: { status?: string; attempts?: number } | null
}

export interface Slot {
  slot_id: string
  start: string
  end: string
  resource_id: string
  resource: string | null
}

export interface Contact {
  id: string
  phone: string
  name?: string | null
  email?: string | null
  notes?: string
  tags: string[]
  do_not_contact: boolean
  first_seen_at?: string
  last_seen_at?: string
  stats?: { calls: number; appointments: number; no_shows: number }
  /** The patient's regular dentist (a resource id): the assistant books them with this dentist first. */
  preferred_resource_id?: string | null
}

export interface ContactDetail extends Contact {
  appointments: Appointment[]
  calls: TenantCall[]
  messages: { message_id: string; message: string; created_at: string; status: string }[]
}

export interface TenantCall {
  call_id: string
  channel: 'phone' | 'browser'
  direction?: string
  caller_phone: string | null
  started_at: string
  ended_at?: string
  duration_s?: number
  status: 'in_progress' | 'completed'
  outcome: string | null
  summary?: string
  transcript?: { role: 'user' | 'assistant'; text: string; at: string; interrupted?: boolean }[]
  latency?: { turns: number; p50_s: number | null; p95_s: number | null; tool_p50_s: number | null }
  tools_used?: string[]
}

export interface Lead {
  id: string
  name: string
  phone: string
  address: string
  problem: string
  urgency: 'emergency' | 'today' | 'this_week' | 'flexible'
  preferred_time: string
  status: 'new' | 'contacted' | 'won' | 'lost'
  created_at: string
  call_id?: string | null
  /** Dental leads: 'dental_emergency' | 'cosmetic_consult'. Home-services job requests have none. */
  kind?: string | null
  treatment_category?: string | null
  estimated_value_minor?: number | null
  currency?: string | null
  priority?: 'high' | 'normal' | null
  /** dental_emergency only. */
  severity?: 'life_threatening' | 'urgent' | 'routine' | null
  red_flag?: boolean | null
  /** On-call escalation (Phase 2.3), when the practice has an on-call list. */
  escalation_id?: string | null
  escalation_status?: 'open' | 'acknowledged' | 'unacknowledged' | null
  escalation_acked_by?: string | null
  /** cosmetic_consult only. */
  patient_status?: 'new' | 'existing' | 'unknown' | null
  timeframe?: 'asap' | 'within_1_month' | '1_3_months' | 'researching' | null
  funding_interest?: 'health_fund' | 'finance' | 'both' | 'none' | 'unknown' | null
  service_name?: string | null
  notes?: string | null
  channel?: string | null
}

export interface KnowledgeItem {
  id: string
  question: string
  answer: string
  tags: string[]
  active: boolean
  source?: string
  updated_at?: string
}

export interface UnansweredItem {
  id: string
  question: string
  channel: string
  call_id?: string | null
  created_at: string
  status: 'open' | 'answered' | 'dismissed'
  /** How many times callers asked this (similar wordings are grouped). */
  count: number
  call_ids: string[]
  first_asked_at?: string
}

export interface KnowledgeList {
  items: KnowledgeItem[]
  inline: boolean
  count: number
  chars: number
  max_items: number
  max_chars: number
}

export interface KnowledgeSuggestion {
  question: string
  hint: string
}

export interface KnowledgeTestResult {
  inline: boolean
  matches: (KnowledgeItem & { strength: 'strong' | 'weak' })[]
}

export interface Overview {
  today: string
  calls_today: number
  bookings_today: number
  messages_new: number
  leads_new: number
  unanswered_open: number
  minutes_used: number
  minutes_included: number
  calls_30d: number
  handled_rate_pct: number | null
  outcomes_30d: Record<string, number>
  series: { date: string; calls: number }[]
  recent_calls: TenantCall[]
  upcoming: Appointment[]
}

/** One follow-up in the front-desk inbox (plan 4.1); the original record is `kind` + `id`. */
export interface InboxItem {
  kind: 'message' | 'lead' | 'escalation' | 'confirmation'
  id: string
  title: string
  detail: string
  created_at: string | null
  due_at: string | null
  assignee_id: string | null
  urgent: boolean
  overdue: boolean
  phone: string
  link: string
}

export interface TeamMember {
  id: string
  email: string
  name: string
  role: Role
  /** For a dentist: the practitioner whose day they see. */
  resource_id?: string | null
  last_login_at: string | null
  disabled: boolean
  pending_password: boolean
}

export interface PhoneNumberInfo {
  e164: string
  purpose: string
  status: string
  provider?: string
}

export interface ForwardingRow {
  carrier: string
  no_answer: string
  busy: string
  unreachable: string
  cancel: string
}


export interface WhatsAppStatus {
  configured: boolean
  own_number: boolean
  templates_configured: boolean
}

export interface WhatsAppTemplate {
  name: string
  language: string
  category: string
  body: string
  /** Number of {{n}} placeholders in the body. */
  params: number
}

export interface WhatsAppOutboxItem {
  id: string
  kind: 'text' | 'template'
  template?: string | null
  body: string
  status: 'accepted' | 'sent' | 'delivered' | 'read' | 'failed'
  error?: string | null
  error_code?: number | null
  created_at: string
}

export interface ContactWhatsApp {
  window: { open: boolean; last_inbound_at: string | null; expires_at: string | null }
  do_not_contact: boolean
  messages: WhatsAppOutboxItem[]
}

/** One patient on a dentist's day (GET /t/my-day). */
export interface DayAppointment {
  appointment_id: string | null
  /** ai = booked by the assistant, dashboard = by staff here, pms = only in Cliniko / Dentally */
  source: 'ai' | 'dashboard' | 'pms'
  booked_here: boolean
  start: string
  end: string | null
  patient_name: string
  phone: string
  service: string
  status: string
  notes: string
  no_shows: number
  first_visit: boolean | null
  last_call_summary: string | null
  triage: { severity: string | null; problem: string | null; red_flag: boolean } | null
  enquiry: { treatment: string | null; timeframe: string | null } | null
}

export interface DayView {
  day: string
  resource: { id: string; name: string }
  timezone: string
  appointments: DayAppointment[]
  warning: string | null
  pms: string | null
}
