/**
 * Where each part of an offer lives in the editor, and how ready it is (SALES_SUITE_PLAN.md Phase 5).
 * Issues from the API name a field ("variants.1.cold_opener"); these map it to its tab and the
 * element to focus.
 */
import type { OfferIssue, OfferSettings } from '@/lib/sales'

export const EDITOR_TABS = ['offer', 'conversation', 'scripts', 'follow-up', 'consent', 'prompt', 'tests', 'learning', 'history'] as const
export type EditorTab = (typeof EDITOR_TABS)[number]

export const FIELD_TAB: Record<string, EditorTab> = {
  offer: 'offer', name: 'offer', brand: 'offer', company_name: 'offer', persona_name: 'offer', website: 'offer',
  what_we_sell: 'offer', ideal_customer: 'offer', value_props: 'offer', proof_points: 'offer',
  discovery_questions: 'conversation', qualification_questions: 'conversation', pain_hypotheses: 'conversation',
  objections: 'conversation', pricing: 'conversation', call_goal: 'conversation', roi: 'conversation',
  variants: 'scripts',
  meeting: 'follow-up', info_email: 'follow-up', confirmation_email: 'follow-up', follow_ups: 'follow-up',
  deal_value_default: 'follow-up', deal_currency: 'follow-up', channels: 'follow-up',
  consented_opener: 'consent', callback_opener: 'consent', voicemail: 'consent', consent_text: 'consent',
}

export const tabOf = (field: string): EditorTab => FIELD_TAB[field.split('.')[0]] ?? 'offer'

/** The DOM id of a field's input: "variants.1.cold_opener" → "offer-field-variants-1-cold_opener". */
export const fieldId = (path: string) => `offer-field-${path.replace(/\./g, '-')}`

/** The closest element for a path: the exact field, else its list or section. */
export function fieldElement(path: string, doc: Pick<Document, 'getElementById'> = document): HTMLElement | null {
  const parts = path.split('.')
  for (let n = parts.length; n > 0; n--) {
    const el = doc.getElementById(fieldId(parts.slice(0, n).join('.')))
    if (el) return el
  }
  return null
}

export type TabCounts = Record<EditorTab, { problems: number; warnings: number }>

export function issueCounts(issues: OfferIssue[]): TabCounts {
  const out = Object.fromEntries(EDITOR_TABS.map((t) => [t, { problems: 0, warnings: 0 }])) as TabCounts
  for (const i of issues) {
    const c = out[tabOf(i.field)]
    if (i.level === 'problem') c.problems += 1
    else c.warnings += 1
  }
  return out
}

export interface ReadinessCheck {
  id: string
  label: string
  done: boolean
  /** Publishing is refused while a required check is open (the API decides; this mirrors it). */
  required: boolean
  field: string
}

const filled = (s: string | undefined, min = 1) => (s ?? '').trim().length >= min

/** About 14 things a good offer has. Required ones mirror the API's problems; the rest make calls better. */
export function completeness(form: OfferSettings, unconfirmed: string[] = []): { checks: ReadinessCheck[]; done: number; total: number } {
  const v = form.variants ?? []
  const checks: ReadinessCheck[] = [
    { id: 'name', label: 'Named the offer', done: filled(form.name, 2), required: true, field: 'name' },
    { id: 'what', label: 'Said what you sell', done: filled(form.what_we_sell, 10), required: true, field: 'what_we_sell' },
    { id: 'who', label: 'Said who it’s for', done: filled(form.ideal_customer), required: false, field: 'ideal_customer' },
    { id: 'value', label: 'At least 3 value points', done: form.value_props.filter((x) => filled(x)).length >= 3, required: false, field: 'value_props' },
    { id: 'proof', label: 'At least 1 proof point', done: form.proof_points.filter((x) => filled(x)).length >= 1, required: false, field: 'proof_points' },
    { id: 'discovery', label: 'At least 2 discovery questions', done: form.discovery_questions.filter((x) => filled(x)).length >= 2, required: false, field: 'discovery_questions' },
    { id: 'qualify', label: 'At least 2 qualification points', done: form.qualification_questions.filter((x) => filled(x)).length >= 2, required: false, field: 'qualification_questions' },
    { id: 'objections', label: 'At least 3 objection answers', done: form.objections.filter((o) => filled(o.objection) && filled(o.response)).length >= 3, required: false, field: 'objections' },
    { id: 'price', label: 'Decided the price policy', done: form.pricing.mode === 'never' || filled(form.pricing.text), required: true, field: 'pricing.text' },
    { id: 'openers', label: 'Every script has an opener', done: v.length > 0 && v.every((x) => filled(x.cold_opener, 10)), required: true, field: 'variants' },
    { id: 'ab', label: 'Two scripts to A/B test', done: v.length >= 2, required: false, field: 'variants' },
    { id: 'roi', label: 'Worked out what it’s worth to them (ROI)', done: !!form.roi?.enabled && (form.roi.inputs ?? []).length > 0, required: false, field: 'roi' },
    { id: 'fallback', label: 'A page they can book on themselves', done: filled(form.channels?.booking_url) || filled(form.website), required: false, field: 'channels.booking_url' },
    { id: 'meeting', label: 'Said who they meet', done: form.call_goal !== 'book_meeting' || filled(form.meeting.host_name), required: false, field: 'meeting.host_name' },
    { id: 'consented', label: 'Opener for people who asked for a call', done: filled(form.consented_opener), required: false, field: 'consented_opener' },
    { id: 'facts', label: 'Confirmed the facts the AI wrote', done: unconfirmed.length === 0, required: true, field: unconfirmed[0] ?? 'proof_points' },
  ]
  return { checks, done: checks.filter((c) => c.done).length, total: checks.length }
}

/** When an item is removed from a list, AI facts after it move up one. */
export function shiftUnconfirmed(paths: string[], list: string, removed: number): string[] {
  const out: string[] = []
  for (const p of paths) {
    if (!p.startsWith(`${list}.`)) {
      out.push(p)
      continue
    }
    const idx = p.slice(list.length + 1)
    if (!/^\d+$/.test(idx)) {
      out.push(p)
      continue
    }
    const i = Number(idx)
    if (i === removed) continue
    out.push(i > removed ? `${list}.${i - 1}` : p)
  }
  return out
}
