import { describe, expect, it } from 'vitest'
import { applyChoices, choicesFor, currentOpener, openerPatch } from '@/components/sales/offer-editor/suggestions'
import { BLANK_OFFER, settingsOf } from '@/components/sales/offer-editor/draft'
import { completeness, issueCounts, shiftUnconfirmed, tabOf } from '@/components/sales/offer-editor/readiness'
import { diffOffers, pathLabel, wordDiff } from '@/lib/offer-diff'
import { can, salesOnly } from '@/lib/roles'
import type { OfferSettings } from '@/lib/sales'

const offer = (p: Partial<OfferSettings> = {}): OfferSettings => settingsOf({ ...BLANK_OFFER, name: 'Test', ...p })

describe('offer diff', () => {
  it('matches scripts by id and reports added, changed and removed rows', () => {
    const a = offer({ value_props: ['Fast', 'Cheap'], variants: [{ ...BLANK_OFFER.variants[0], id: 'a', pitch: 'Old pitch' }] })
    const b = offer({ value_props: ['Fast'], variants: [{ ...BLANK_OFFER.variants[0], id: 'b', label: 'New' }, { ...BLANK_OFFER.variants[0], id: 'a', pitch: 'New pitch' }] })
    const rows = diffOffers(a, b)
    expect(rows.find((r) => r.path === 'variants.a.pitch')).toMatchObject({ kind: 'changed', before: 'Old pitch', after: 'New pitch' })
    expect(rows.some((r) => r.path === 'variants.b.cold_opener' && r.kind === 'added')).toBe(true)
    expect(rows.find((r) => r.path === 'value_props.1')).toMatchObject({ kind: 'removed', before: 'Cheap' })
    expect(diffOffers(a, a)).toEqual([])
  })

  it('labels paths the way the editor names them', () => {
    expect(pathLabel('objections.2.response')).toBe('Objection 3: answer')
    expect(pathLabel('info_email.subject')).toBe('Information email: subject')
  })

  it('highlights only the changed words', () => {
    const parts = wordDiff('we answer every call', 'we answer every single call')
    expect(parts.filter((p) => p.op === 'add').map((p) => p.text.trim())).toEqual(['single'])
    expect(parts.some((p) => p.op === 'del')).toBe(false)
  })
})

describe('readiness', () => {
  it('puts each issue on its tab', () => {
    expect(tabOf('variants.1.cold_opener')).toBe('scripts')
    expect(tabOf('pricing.text')).toBe('conversation')
    expect(tabOf('consent_text')).toBe('consent')
    const c = issueCounts([{ level: 'problem', field: 'variants.0.cold_opener', message: 'x' }, { level: 'warning', field: 'proof_points', message: 'y' }])
    expect(c.scripts.problems).toBe(1)
    expect(c.offer.warnings).toBe(1)
  })

  it('counts what a good offer has, and unconfirmed AI facts keep it from being ready', () => {
    const r = completeness(offer({ what_we_sell: 'We build AI receptionists.' }), ['proof_points.0'])
    expect(r.total).toBe(16)
    expect(r.checks.find((c) => c.id === 'facts')).toMatchObject({ done: false, required: true, field: 'proof_points.0' })
    expect(r.checks.find((c) => c.id === 'what')?.done).toBe(true)
  })

  it('moves AI facts up when an item above them is removed', () => {
    expect(shiftUnconfirmed(['proof_points.0', 'proof_points.2', 'pricing.text'], 'proof_points', 1)).toEqual(['proof_points.0', 'proof_points.1', 'pricing.text'])
    expect(shiftUnconfirmed(['proof_points.1'], 'proof_points', 1)).toEqual([])
  })
})

describe('AI suggestions', () => {
  it('offers new list items as choices and applies only the ones kept', () => {
    const form = offer({ value_props: ['Fast'] })
    const patch = { value_props: ['Fast', 'Cheap', 'Friendly'] }
    const choices = choicesFor('value_props', form, patch)
    expect(choices.map((c) => c.after)).toEqual(['Cheap', 'Friendly'])
    const out = applyChoices(form, patch, new Set(['value_props.2']), [])
    expect(out.patch.value_props).toEqual(['Fast', 'Friendly'])
  })

  it('keeps AI facts unconfirmed only for the proof points applied', () => {
    const form = offer({ proof_points: ['A', 'B'] })
    const patch = { proof_points: ['A1', 'B1'] }
    const out = applyChoices(form, patch, new Set(['proof_points.1']), ['proof_points.0', 'proof_points.1'])
    expect(out.patch.proof_points).toEqual(['A', 'B1'])
    expect(out.unconfirmed).toEqual(['proof_points.1'])
  })

  it('adds a new script as one choice', () => {
    const form = offer()
    const v = { ...BLANK_OFFER.variants[0], id: 'after_hours', label: 'After hours' }
    const choices = choicesFor('variant', form, { variants: [...form.variants, v] })
    expect(choices).toHaveLength(1)
    expect(applyChoices(form, { variants: [...form.variants, v] }, new Set([choices[0].key]), []).patch.variants).toHaveLength(2)
  })

  it('offers a suggested ROI as one choice and keeps its AI figures unconfirmed', () => {
    const form = offer()
    const roi = { enabled: true, result_label: 'extra revenue a month', monthly_cost: null,
      inputs: [{ question: 'How many calls do you miss a week?', label: 'missed calls a week', per: 'week' as const, max: 500 }],
      factors: [{ label: 'value of a new patient', value: 1200, percent: false }] }
    const choices = choicesFor('roi', form, { roi })
    expect(choices).toHaveLength(1)
    expect(choices[0].after).toContain('value of a new patient: 1200')
    const out = applyChoices(form, { roi }, new Set([choices[0].key]), ['roi.factors.0'])
    expect(out.patch.roi).toEqual(roi)
    expect(out.unconfirmed).toEqual(['roi.factors.0'])
    expect(applyChoices(form, { roi }, new Set(), ['roi.factors.0'])).toEqual({ patch: {}, unconfirmed: [] })
  })

  it('puts a picked opener on the right script, or on the consented opener', () => {
    const second = { ...BLANK_OFFER.variants[0], id: 'after_hours', label: 'After hours', cold_opener: 'Old' }
    const form = { ...offer(), variants: [...offer().variants, second] }
    const patch = openerPatch(form, 'after_hours', 'New opener')
    expect(patch.variants?.map((v) => v.cold_opener)).toEqual([form.variants[0].cold_opener, 'New opener'])
    expect(currentOpener(form, 'after_hours')).toBe('Old')
    expect(openerPatch(form, 'consented_opener', 'Asked')).toEqual({ consented_opener: 'Asked' })
    expect(openerPatch(form, 'gone', 'x')).toEqual({})
  })
})

describe('sales roles', () => {
  it('ranks viewer < caller < manager < admin', () => {
    expect(can({ is_platform_admin: false, sales_role: 'caller' }, 'caller')).toBe(true)
    expect(can({ is_platform_admin: false, sales_role: 'caller' }, 'manager')).toBe(false)
    expect(can({ is_platform_admin: true, sales_role: null }, 'admin')).toBe(true)
    expect(can({ is_platform_admin: false, sales_role: null }, 'viewer')).toBe(false)
    expect(salesOnly({ is_platform_admin: false, sales_role: 'viewer' })).toBe(true)
    expect(salesOnly({ is_platform_admin: true, sales_role: 'admin' })).toBe(false)
  })
})
