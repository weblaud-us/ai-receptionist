/**
 * Turning an AI suggestion (a patch for one section) into choices a person ticks, and applying only
 * the ones they keep (SALES_SUITE_PLAN.md Phase 4). Pure, so it is unit-tested.
 */
import type { AssistSection, Objection, OfferRoi, OfferSettings, ScriptVariant } from '@/lib/sales'

export interface Choice {
  key: string
  label: string
  before: string
  after: string
}

const LISTS = ['value_props', 'proof_points', 'discovery_questions', 'qualification_questions', 'pain_hypotheses'] as const
type ListKey = (typeof LISTS)[number]
const isList = (k: string): k is ListKey => (LISTS as readonly string[]).includes(k)

const objText = (o: Objection | undefined) => (o ? `“${o.objection}” → ${o.response}` : '')
const variantText = (v: ScriptVariant | undefined) =>
  v ? [`Name: ${v.label}`, `Opener: ${v.cold_opener}`, v.first_question && `First question: ${v.first_question}`, v.pitch && `Pitch: ${v.pitch}`, v.human_opener && `For a person: ${v.human_opener}`].filter(Boolean).join('\n') : ''
const roiText = (r: OfferRoi | undefined) =>
  r ? [
    ...r.inputs.map((x, i) => `Question ${i + 1} (${x.label}${x.per === 'once' ? '' : `, per ${x.per}`}): ${x.question}`),
    ...r.factors.map((f) => `Your figure: ${f.label}: ${f.value}${f.percent ? ' %' : ''}`),
    `Result: ${r.result_label}`,
  ].join('\n') : ''
const emailText = (e: { subject: string; body: string } | undefined) => (e ? `Subject: ${e.subject}\n\n${e.body}` : '')
const KEY_LABEL: Record<string, string> = {
  what_we_sell: 'What you sell', ideal_customer: 'Who it’s for', info_email: 'Information email',
  confirmation_email: 'Meeting confirmation', consented_opener: 'Opener for people who asked for a call',
  callback_opener: 'Opener when they asked us to call back',
  voicemail: 'Voicemail', consent_text: 'Consent wording',
}

/** One choice per new or changed item (lists, objections, scripts), or per changed field. */
export function choicesFor(section: AssistSection, form: OfferSettings, patch: Partial<OfferSettings>): Choice[] {
  const out: Choice[] = []
  for (const [key, value] of Object.entries(patch)) {
    if (isList(key)) {
      const before = form[key]
      const after = value as string[]
      after.forEach((item, i) => {
        if (before[i] === item) return
        if (i >= before.length && before.includes(item)) return
        out.push({ key: `${key}.${i}`, label: `${i < before.length ? 'Reworded' : 'New'} ${i + 1}`, before: before[i] ?? '', after: item })
      })
    } else if (key === 'objections') {
      const before = form.objections
      ;(value as Objection[]).forEach((o, i) => {
        const b = before[i]
        if (b && b.objection === o.objection && b.response === o.response) return
        out.push({ key: `objections.${i}`, label: b ? `Answer to “${b.objection}”` : `New: “${o.objection}”`, before: objText(b), after: objText(o) })
      })
    } else if (key === 'variants') {
      const before = form.variants
      ;(value as ScriptVariant[]).forEach((v) => {
        const b = before.find((x) => x.id === v.id)
        if (b && JSON.stringify(b) === JSON.stringify(v)) return
        out.push({ key: `variants.${v.id}`, label: b ? `Script “${b.label}”` : `New script “${v.label}”`, before: variantText(b), after: variantText(v) })
      })
    } else if (key === 'follow_ups') {
      const after = value as { after_days: number; subject: string; body: string }[]
      const text = (xs: { after_days: number; subject: string; body: string }[] | undefined) =>
        (xs ?? []).map((x) => `After ${x.after_days} days: ${x.subject}\n${x.body}`).join('\n\n')
      if (JSON.stringify(form.follow_ups ?? []) !== JSON.stringify(after)) out.push({ key, label: 'Follow-up emails', before: text(form.follow_ups), after: text(after) })
    } else if (key === 'roi') {
      const after = value as OfferRoi
      if (JSON.stringify(form.roi) !== JSON.stringify(after)) out.push({ key, label: 'ROI questions and figures', before: roiText(form.roi), after: roiText(after) })
    } else if (key === 'info_email' || key === 'confirmation_email') {
      const after = value as { subject: string; body: string }
      if (JSON.stringify(form[key]) !== JSON.stringify(after)) out.push({ key, label: KEY_LABEL[key], before: emailText(form[key]), after: emailText(after) })
    } else {
      const before = String((form as unknown as Record<string, unknown>)[key] ?? '')
      if (before !== String(value ?? '')) out.push({ key, label: KEY_LABEL[key] ?? key, before, after: String(value ?? '') })
    }
  }
  void section
  return out
}

/** The patch that puts a picked opener in place: a script's cold opener, or the consented opener. */
export function openerPatch(form: OfferSettings, target: string | undefined, opener: string): Partial<OfferSettings> {
  if (target === 'consented_opener') return { consented_opener: opener }
  if (!form.variants.some((v) => v.id === target)) return {}
  return { variants: form.variants.map((v) => (v.id === target ? { ...v, cold_opener: opener } : v)) }
}

/** The opener an "openers" suggestion would replace. */
export function currentOpener(form: OfferSettings, target: string | undefined): string {
  if (target === 'consented_opener') return form.consented_opener
  return form.variants.find((v) => v.id === target)?.cold_opener ?? ''
}

/** The form patch for the choices kept, and which AI facts in it still need confirming. */
export function applyChoices(
  form: OfferSettings,
  patch: Partial<OfferSettings>,
  kept: Set<string>,
  unconfirmed: string[],
): { patch: Partial<OfferSettings>; unconfirmed: string[] } {
  const out: Partial<OfferSettings> = {}
  const facts: string[] = []
  for (const [key, value] of Object.entries(patch)) {
    if (isList(key)) {
      const before = form[key]
      const after = value as string[]
      const next = [...before]
      after.forEach((item, i) => {
        if (!kept.has(`${key}.${i}`)) return
        if (i < before.length) next[i] = item
        else if (!next.includes(item)) next.push(item)
      })
      if (JSON.stringify(next) !== JSON.stringify(before)) (out as Record<string, unknown>)[key] = next.slice(0, 6)
      if (key === 'proof_points') {
        for (const p of unconfirmed) {
          const i = Number(p.split('.')[1])
          if (kept.has(`proof_points.${i}`)) facts.push(p)
        }
      }
    } else if (key === 'objections') {
      const next = [...form.objections]
      ;(value as Objection[]).forEach((o, i) => {
        if (!kept.has(`objections.${i}`)) return
        if (i < form.objections.length) next[i] = o
        else next.push(o)
      })
      if (JSON.stringify(next) !== JSON.stringify(form.objections)) out.objections = next.slice(0, 12)
    } else if (key === 'variants') {
      const next = [...form.variants]
      for (const v of value as ScriptVariant[]) {
        if (!kept.has(`variants.${v.id}`)) continue
        const at = next.findIndex((x) => x.id === v.id)
        if (at >= 0) next[at] = v
        else next.push(v)
      }
      if (JSON.stringify(next) !== JSON.stringify(form.variants)) out.variants = next.slice(0, 4)
    } else if (key === 'roi') {
      if (!kept.has(key)) continue
      out.roi = value as OfferRoi
      facts.push(...unconfirmed.filter((p) => p.startsWith('roi.factors.')))
    } else if (kept.has(key)) {
      ;(out as Record<string, unknown>)[key] = value
      if (unconfirmed.includes(key)) facts.push(key)
    }
  }
  return { patch: out, unconfirmed: facts }
}
