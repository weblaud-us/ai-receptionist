/**
 * What changed between two versions of an offer (SALES_SUITE_PLAN.md Phase 7), for the editor's
 * History tab and the AI suggestion dialog. Scripts are matched by id, everything else by position.
 */
import type { OfferSettings } from '@/lib/sales'

export interface DiffRow {
  path: string
  label: string
  kind: 'added' | 'removed' | 'changed'
  before: string
  after: string
}

const LABEL: Record<string, string> = {
  name: 'Offer name', brand: 'Brand', company_name: 'Company', persona_name: 'Agent’s name', website: 'Website',
  what_we_sell: 'What you sell', ideal_customer: 'Who it’s for', value_props: 'Value point', proof_points: 'Proof point',
  discovery_questions: 'Discovery question', qualification_questions: 'Qualification', pain_hypotheses: 'Likely problem',
  objections: 'Objection', pricing: 'Price', call_goal: 'Goal of the call', meeting: 'Meeting',
  info_email: 'Information email', confirmation_email: 'Meeting confirmation', consented_opener: 'Consented opener',
  callback_opener: 'Callback opener',
  voicemail: 'Voicemail', consent_text: 'Consent wording', variants: 'Script',
}
const PART: Record<string, string> = {
  mode: 'mode', text: 'sentence', label: 'name', length_min: 'minutes', service_name: 'booked as', url: 'video link',
  host_name: 'who they meet', subject: 'subject', body: 'body', objection: 'objection', response: 'answer',
  cold_opener: 'AI opener', first_question: 'first question', pitch: 'pitch', human_opener: 'opener for a person',
}

/** "objections.2.response" → "Objection 3: answer"; "variants.after_hours.pitch" → "Script after_hours: pitch". */
export function pathLabel(path: string): string {
  const [head, second, third] = path.split('.')
  const base = LABEL[head] ?? head
  if (second === undefined) return base
  if (/^\d+$/.test(second)) return `${base} ${Number(second) + 1}${third ? `: ${PART[third] ?? third}` : ''}`
  if (head === 'variants') return `${base} “${second}”${third ? `: ${PART[third] ?? third}` : ''}`
  return `${base}: ${PART[second] ?? second}`
}

/** Every leaf as text, keyed by path. Scripts are keyed by their id so reordering isn't a change. */
export function flatten(value: unknown, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {}
  const walk = (v: unknown, path: string) => {
    if (Array.isArray(v)) {
      const byId = path === 'variants'
      v.forEach((item, i) => {
        const key = byId && item && typeof item === 'object' && 'id' in item ? String((item as { id: string }).id) : String(i)
        walk(item, path ? `${path}.${key}` : key)
      })
      return
    }
    if (v && typeof v === 'object') {
      for (const [k, x] of Object.entries(v)) {
        if (path.startsWith('variants.') && k === 'id') continue
        walk(x, path ? `${path}.${k}` : k)
      }
      return
    }
    out[path] = v === null || v === undefined ? '' : String(v)
  }
  walk(value, prefix)
  return out
}

/** The rows that differ between `a` (before) and `b` (after), in the order of `b` then removals. */
export function diffOffers(a: Partial<OfferSettings> | null | undefined, b: Partial<OfferSettings> | null | undefined): DiffRow[] {
  const fa = flatten(a ?? {})
  const fb = flatten(b ?? {})
  const rows: DiffRow[] = []
  for (const [path, after] of Object.entries(fb)) {
    const before = fa[path]
    if (before === undefined) {
      if (after !== '') rows.push({ path, label: pathLabel(path), kind: 'added', before: '', after })
    } else if (before !== after) {
      rows.push({ path, label: pathLabel(path), kind: 'changed', before, after })
    }
  }
  for (const [path, before] of Object.entries(fa)) {
    if (fb[path] === undefined && before !== '') rows.push({ path, label: pathLabel(path), kind: 'removed', before, after: '' })
  }
  return rows
}

export interface WordPart {
  text: string
  op: 'same' | 'add' | 'del'
}

/** A word-level diff (longest common subsequence), for highlighting long text. Falls back to whole
 * before/after when the text is too long to compare cheaply. */
export function wordDiff(before: string, after: string, max = 600): WordPart[] {
  const a = before.split(/(\s+)/).filter((x) => x !== '')
  const b = after.split(/(\s+)/).filter((x) => x !== '')
  if (a.length > max || b.length > max) {
    return [...(before ? [{ text: before, op: 'del' as const }] : []), ...(after ? [{ text: after, op: 'add' as const }] : [])]
  }
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out: WordPart[] = []
  const push = (text: string, op: WordPart['op']) => {
    const last = out[out.length - 1]
    if (last && last.op === op) last.text += text
    else out.push({ text, op })
  }
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push(a[i], 'same')
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push(a[i++], 'del')
    } else {
      push(b[j++], 'add')
    }
  }
  while (i < a.length) push(a[i++], 'del')
  while (j < b.length) push(b[j++], 'add')
  return out
}
