/** Types for the Phase 2 tenant API (/t/*, /platform/*). */

export type Role = 'owner' | 'admin' | 'staff'
export type Vertical = 'home_services' | 'salon' | 'restaurant' | 'hotel' | 'clinic' | 'professional' | 'generic'

export interface HoursEntry {
  dow: number // 0 = Monday … 6 = Sunday
  open: string // HH:MM
  close: string // HH:MM, may be ≤ open for overnight, or 24:00
}

export interface AgentConfig {
  name: string
  voice_provider: 'elevenlabs' | 'cartesia' | 'openai'
  voice_id: string
  language: string
  stt_language: string
  greeting: string
  tone: 'warm' | 'professional' | 'upbeat'
  custom_instructions: string
  llm_model: string
  endpointing: { min_delay: number; max_delay: number }
  ambience: boolean
  recording: { enabled: boolean; retention_days: number; announce: boolean }
  max_call_minutes: number
  transcript_retention_days: number
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
  notifications: {
    owner_phone: string
    owner_email: string
    owner_whatsapp: string
    on_new_booking: string[]
    on_message: string[]
    on_urgent: string[]
    daily_digest: boolean
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
}

export interface VerticalInfo {
  key: Vertical
  label: string
  description: string
  labels: VerticalLabels
  default_services: { name: string; duration_min: number }[]
  default_faqs: { question: string; answer: string }[]
}

export interface Service {
  id: string
  name: string
  duration_min: number
  buffer_min: number
  price?: { amount?: number | null; currency?: string | null; display: string } | null
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
  recording?: { path: string } | null
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

export interface TeamMember {
  id: string
  email: string
  name: string
  role: Role
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
