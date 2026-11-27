/** Starting points for the offer editor: the blank template and an AI draft. */
import type { GeneratedOffer, OfferSettings } from '@/lib/sales'

const OPT_OUT = "If you'd rather not get calls like this, just say so and I'll take you off our list."

/** A new offer starts from a script that already passes the opener checks. */
export const BLANK_OFFER: OfferSettings = {
  name: '', brand: 'weblaud', company_name: 'WebLaud', persona_name: 'Max', website: '',
  what_we_sell: '', ideal_customer: '',
  value_props: [], proof_points: [], discovery_questions: [], qualification_questions: [], pain_hypotheses: [],
  objections: [],
  pricing: { mode: 'never', text: '' },
  // Same key order as the server's defaults, so an untouched offer doesn't look changed.
  roi: { enabled: false, inputs: [], factors: [], result_label: 'extra revenue a month', monthly_cost: null },
  call_goal: 'book_meeting',
  meeting: { label: 'discovery call', length_min: 15, service_name: 'Demo', url: '', host_name: '' },
  channels: { whatsapp: false, whatsapp_template: '', whatsapp_language: 'en', booking_url: '' },
  info_email: { subject: '{company}: a quick note for {business}', body: "Hi {first_name},\n\nThanks for taking our call. Reply to this email and we'll set up a short {meeting_label} at a time that suits you." },
  confirmation_email: { subject: 'Your {company} {meeting_label}: {when}', body: 'Hi {first_name},\n\nYour {meeting_label} with {host} is booked for {when}.\n{meeting_url}\n\nReply to this email if you need to change the time.' },
  consented_opener: '', callback_opener: '', voicemail: '', consent_text: '',
  follow_ups: [], deal_value_default: null, deal_currency: 'USD',
  variants: [{
    id: 'main', label: 'Main script',
    cold_opener: `Hi, this is {persona}, an AI assistant calling from {company}. Am I speaking with the owner or manager of {business}? ${OPT_OUT}`,
    first_question: '', pitch: '', human_opener: '',
  }],
}

const SETTINGS_KEYS = Object.keys(BLANK_OFFER) as (keyof OfferSettings)[]
export const settingsOf = (o: Partial<OfferSettings>): OfferSettings =>
  Object.fromEntries(SETTINGS_KEYS.map((k) => [k, o[k] ?? BLANK_OFFER[k]])) as unknown as OfferSettings

export interface EditorStart {
  form: OfferSettings
  unconfirmed: string[]
  aiFields: string[]
  sources: Record<string, string>
  notes?: string
  warnings?: string[]
}

export const startFromAi = (r: GeneratedOffer): EditorStart => ({
  form: settingsOf(r.draft), unconfirmed: r.ai_unconfirmed, sources: r.sources, notes: r.notes, warnings: r.warnings,
  aiFields: Object.keys(r.draft).filter((k) => !['brand', 'call_goal', 'website', 'meeting', 'roi', 'channels'].includes(k)),
})
