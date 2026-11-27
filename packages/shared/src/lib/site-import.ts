/**
 * The website import draft (POST /platform/site-import, POST /t/onboarding/site-import).
 * Nothing is saved until the reviewed draft is sent back.
 */
import type { HoursEntry } from './hours'

export interface DraftAddress {
  line1: string
  line2?: string
  city: string
  region: string
  postal: string
  country: string
}

export interface DraftProfile {
  name: string
  description: string
  vertical: string
  phone: string
  email: string
  website: string
  address: DraftAddress
  country: string
  timezone: string
  currency: string
}

export interface DraftService {
  name: string
  duration_min: number | null
  description: string
  price: { amount: number | null; currency: string | null; display: string } | null
}

export interface DraftFaq {
  question: string
  answer: string
}

export interface SiteDraft {
  profile: DraftProfile
  hours: HoursEntry[]
  services: DraftService[]
  faqs: DraftFaq[]
  links: Partial<Record<'booking' | 'menu' | 'payment', string>>
  sources: string[]
  warnings: string[]
}

export const EMPTY_ADDRESS: DraftAddress = { line1: '', city: '', region: '', postal: '', country: '' }

export const emptyDraft = (): SiteDraft => ({
  profile: { name: '', description: '', vertical: 'generic', phone: '', email: '', website: '', address: { ...EMPTY_ADDRESS },
    country: '', timezone: '', currency: '' },
  hours: [],
  services: [],
  faqs: [],
  links: {},
  sources: [],
  warnings: [],
})

/** "from $45" / "£45.50" → 4500 / 4550 minor units; null when there's no number. */
export function priceAmount(display: string): number | null {
  const m = display.replace(/,(?=\d{3}\b)/g, '').match(/(\d+(?:[.,]\d{1,2})?)/)
  return m ? Math.round(parseFloat(m[1].replace(',', '.')) * 100) : null
}

/** The kept parts of a draft, cleaned for the API (blank rows dropped). */
export function draftForApi(draft: SiteDraft) {
  return {
    hours: draft.hours,
    services: draft.services.filter((s) => s.name.trim()).map((s) => ({ ...s, name: s.name.trim(), duration_min: s.duration_min || 30 })),
    faqs: draft.faqs.filter((f) => f.question.trim() && f.answer.trim()),
    links: Object.fromEntries(Object.entries(draft.links).filter(([, v]) => v)) as Record<string, string>,
  }
}

/**
 * Live progress while a website is read (POST …/site-import/stream, server-sent events from
 * AI-receptionist-1/core/services/site_import.py). Steps update in place by id.
 */
export type ImportStep = { type: 'step'; id: string; status: 'running' | 'done' | 'warn'; label: string; detail?: string }
export type ImportPage = { type: 'page'; url: string; title: string; words: number; ok: boolean }
export type ImportFound = { type: 'found'; field: string; label: string; value: string; ok: boolean }
export type ImportEvent =
  | ImportStep
  | ImportPage
  | ImportFound
  | { type: 'result'; draft: SiteDraft }
  | { type: 'error'; status: number; message: string }
