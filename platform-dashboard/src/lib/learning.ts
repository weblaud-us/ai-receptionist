/**
 * The agent that learns from its calls (SALES_LEARNING_PLAN.md): types, data hooks and the small pure
 * helpers the Learning tab uses. Decisions are made by the backend from outcomes; this only shows them.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'
import type { Attempt, CoachStage } from '@/lib/sales'

const BASE = '/platform/sales'
const NO_TENANT = { tenant: null } as const
const get = <T,>(path: string) => api<T>(BASE + path, NO_TENANT)
const send = <T,>(path: string, method: 'POST' | 'PATCH', body?: unknown) => api<T>(BASE + path, { method, body: body ?? {}, ...NO_TENANT })

export type PlaybookKind = 'gatekeeper' | 'bridge' | 'discovery' | 'pitch' | 'objection' | 'close' | 'tone' | 'avoid'

export interface PlaybookEntry {
  id: string
  kind: PlaybookKind
  when: string
  do: string
  say: string
  origin: 'learned' | 'manual'
  pinned: boolean
  evidence: { leads: number; calls: number; progressed: number; attempt_ids: string[] }
  since: number
}

export type PlaybookStatus = 'awaiting_tests' | 'ready' | 'testing' | 'live' | 'retired' | 'rolled_back' | 'failed_checks'

export interface Playbook {
  id: string
  offer_id: string
  n: number
  status: PlaybookStatus
  entries: PlaybookEntry[]
  lines: string[]
  checksum: string
  parent: string | null
  changes: { op: 'add' | 'edit' | 'remove'; entry_id: string; why: string; evidence?: string[] }[]
  origin: 'learned' | 'manual'
  created_by: string
  created_at: string
  why?: string
  go_live?: boolean
  targets: { version: number; checksum: string; variant_ids: string[]; simulation_id: string; result: unknown }[]
  decision: { action: string; reason: string; at: string } | null
  live_since?: string
  retired_at?: string
}

/** One arm of a live test (backend learning_stats.ArmStats.to_doc). */
export interface ArmNumbers {
  leads: number
  decided: number
  progress: number
  pending: number
  meetings: number
  opt_outs: number
  early_hangups: number
  opener_hangups: number
  severe_flags: number
  other_flags: number
  conversations: number
  reviewed: number
  next_step_rate: number | null
  meeting_rate: number | null
  opt_out_rate: number | null
}

export interface Experiment {
  id: string
  offer_id: string
  champion_id: string | null
  challenger_id: string
  share: number
  status: 'running' | 'paused_traffic' | 'promoted' | 'rolled_back' | 'inconclusive' | 'stopped'
  started_at: string
  ended_at: string | null
  looks_done: number[]
  last_eval: null | {
    at: string; action: string; reason: string; champion: ArmNumbers; challenger: ArmNumbers
    p_better: number; p_meetings_worse: number; guards?: string[]; coverage?: number
  }
  decision: { action: string; reason: string; look?: number | null; at: string } | null
  next_look?: number | null
  looks?: number[]
  thresholds?: number[]
}

export interface LearningPage {
  learning: { mode: 'autopilot' | 'paused' | 'off'; live_playbook_id: string | null; experiment_id: string | null; share: number
    last_learn_at: string | null; last_rollback_at: string | null }
  live: Playbook | null
  playbooks: Playbook[]
  experiment: Experiment | null
  tests: Experiment[]
  campaigns: { id: string; name: string; status: string; mode: string; offer_version: number | null; signed_off: boolean
    learning_allowed: boolean; learning_possible: boolean }[]
  status: { enabled: boolean; reason: string; spent_today_usd: number; daily_cap_usd: number; model: string
    playbooks_enabled: boolean; autopilot: boolean; coach: boolean; policy: string }
  kinds: { value: PlaybookKind; label: string }[]
  limits: { entries: number; chars: number; say_words: number }
  curve: { points: { week: string; reached: number; next_step_rate: number | null; meeting_rate: number | null }[]
    marks: { at: string; kind: 'live' | 'rolled_back'; playbook: number }[] }
  unanswered?: { question: string; count: number; attempt_ids: string[] }[]
}

export type EntryInput = Pick<PlaybookEntry, 'kind' | 'when' | 'do' | 'say'> & { id?: string; pinned?: boolean }

export const useLearning = (offerId: string | null | undefined) =>
  useQuery({ queryKey: ['s-learning', offerId], queryFn: () => get<LearningPage>(`/offers/${offerId}/learning`), enabled: !!offerId,
    refetchInterval: 30_000 })

export function useLearningMutations(offerId: string) {
  const qc = useQueryClient()
  const done = () => { for (const k of ['s-learning', 's-offers', 's-campaigns']) void qc.invalidateQueries({ queryKey: [k] }) }
  return {
    setMode: useMutation({ mutationFn: (mode: LearningPage['learning']['mode']) => send<LearningPage>(`/offers/${offerId}/learning`, 'PATCH', { mode }), onSuccess: done }),
    baseline: useMutation({ mutationFn: () => send<LearningPage>(`/offers/${offerId}/learning/baseline`, 'POST'), onSuccess: done }),
    createPlaybook: useMutation({
      mutationFn: (body: { entries: EntryInput[]; why?: string; go_live: boolean }) => send<{ playbook: Playbook }>(`/offers/${offerId}/playbooks`, 'POST', body),
      onSuccess: done,
    }),
    makeLive: useMutation({ mutationFn: (id: string) => send<{ result: string; playbook: Playbook }>(`/playbooks/${id}/make-live`, 'POST'), onSuccess: done }),
    pin: useMutation({
      mutationFn: ({ playbookId, entryId, pinned }: { playbookId: string; entryId: string; pinned: boolean }) =>
        send<{ playbook: Playbook }>(`/playbooks/${playbookId}/entries/${entryId}`, 'PATCH', { pinned }),
      onSuccess: done,
    }),
    stopTest: useMutation({ mutationFn: () => send<LearningPage>(`/offers/${offerId}/experiment/stop`, 'POST'), onSuccess: done }),
    consent: useMutation({
      mutationFn: ({ campaignId, allowed }: { campaignId: string; allowed: boolean }) => send(`/campaigns/${campaignId}/learning-consent`, 'POST', { allowed }),
      onSuccess: done,
    }),
    runLearning: useMutation({ mutationFn: () => send<{ playbook: Playbook | null; note: string }>(`/offers/${offerId}/learning/run`, 'POST'), onSuccess: done }),
  }
}

// ── Pure helpers (tested in src/__tests__/learning.test.ts) ──────────────────

/** "Likely better (96%)" — plain words for a probability that the new playbook sells better. */
export function chanceText(p: number | null | undefined): string {
  if (p == null) return 'Too early to tell'
  if (p >= 0.9) return `Likely better (${Math.round(p * 100)}%)`
  if (p <= 0.1) return `Likely worse (${Math.round((1 - p) * 100)}% sure)`
  if (p >= 0.65) return `Leaning better (${Math.round(p * 100)}%)`
  if (p <= 0.35) return `Leaning worse (${Math.round(p * 100)}%)`
  return 'Too early to tell'
}

export const STATUS_LABEL: Record<PlaybookStatus, string> = {
  awaiting_tests: 'Running test calls', ready: 'Passed: waiting to be tried', testing: 'Being tried on calls',
  live: 'Live', retired: 'Retired', rolled_back: 'Rolled back', failed_checks: 'Failed its test calls',
}

export const EXPERIMENT_LABEL: Record<Experiment['status'], string> = {
  running: 'Running', paused_traffic: 'Paused until calls are checked', promoted: 'Kept: it sold better',
  rolled_back: 'Rolled back', inconclusive: 'No clear winner', stopped: 'Stopped',
}

/** What changed from one playbook to the next, entry by entry. */
export function playbookDiff(before: PlaybookEntry[], after: PlaybookEntry[]): { op: 'add' | 'edit' | 'remove' | 'same'; entry: PlaybookEntry; old?: PlaybookEntry }[] {
  const old = new Map(before.map((e) => [e.id, e]))
  const out: { op: 'add' | 'edit' | 'remove' | 'same'; entry: PlaybookEntry; old?: PlaybookEntry }[] = []
  for (const e of after) {
    const o = old.get(e.id)
    if (!o) out.push({ op: 'add', entry: e })
    else if (o.kind !== e.kind || o.when !== e.when || o.do !== e.do || o.say !== e.say) out.push({ op: 'edit', entry: e, old: o })
    else out.push({ op: 'same', entry: e })
  }
  const kept = new Set(after.map((e) => e.id))
  for (const e of before) if (!kept.has(e.id)) out.push({ op: 'remove', entry: e })
  return out
}

/** How far a test is to its next planned check: businesses reached with the new playbook, and the target. */
export function testProgress(exp: Pick<Experiment, 'last_eval' | 'next_look' | 'looks'> | null | undefined): { done: number; next: number | null; pct: number } {
  const done = exp?.last_eval?.challenger?.decided ?? 0
  const next = exp?.next_look ?? null
  return { done, next, pct: next ? Math.min(100, Math.round((done / next) * 100)) : 100 }
}

/** Week labels and bar heights for the learning curve (rates 0–1 → 0–100). */
export function curveBars(points: LearningPage['curve']['points']): { week: string; label: string; next: number; meet: number; reached: number }[] {
  return points.map((p) => ({
    week: p.week,
    label: new Date(`${p.week}T00:00:00Z`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' }),
    next: Math.round((p.next_step_rate ?? 0) * 100),
    meet: Math.round((p.meeting_rate ?? 0) * 100),
    reached: p.reached,
  }))
}

// ── The AI coach (shown in a call's sheet; SALES_LEARNING_PLAN.md Phase 2) ───

export const STAGE_LABEL: Record<CoachStage, string> = {
  bridge: 'Reason for calling', discovery: 'Questions first', listening: 'Listening', value: 'Value for them',
  objections: 'Objections', close: 'Asking for the meeting', tone: 'Tone and length',
}

export const FLAG_LABEL: Record<string, string> = {
  not_honest_ai: 'Claimed to be a person', ignored_stop: 'Kept going after “don’t call”', price_policy: 'Price said against the policy',
  unbacked_claim: 'A claim the offer doesn’t back up', pressure: 'Pressure', sms_offered: 'Offered SMS',
  recording: 'Talked about recording', other: 'Other',
}
export const SKIP_TEXT: Record<string, string> = {
  human_assisted: 'A person made this call, and only their side of it is transcribed, so it isn’t reviewed.',
  taken_over: 'A person took this call over, so it isn’t reviewed.',
  too_short: 'Too short to review: they said less than two things.',
  no_transcript: 'There’s no transcript to review.',
}

export const scoreTone = (score: number | null | undefined) =>
  score == null ? 'bg-muted text-muted-foreground'
    : score >= 75 ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300'
      : score >= 50 ? 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300'
        : 'bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300'

/** Every flag on a call, fixed-rule checks first, once per kind. */
export function callFlags(a: Pick<Attempt, 'checks' | 'review'>): { kind: string; detail: string; turn: number | null }[] {
  const out: { kind: string; detail: string; turn: number | null }[] = []
  for (const d of a.checks?.details ?? []) out.push({ kind: d.kind, detail: d.detail, turn: d.turn })
  for (const f of a.review?.flags ?? []) if (!out.some((x) => x.kind === f.kind)) out.push({ kind: f.kind, detail: f.detail, turn: f.turn })
  return out
}

// ── When businesses pick up (SALES_LEARNING_PLAN.md Phase 6) ─────────────────

export interface PickupTimes {
  country: string
  countries: string[]
  updated_at: string | null
  dials: number
  reached: number
  rate: number | null
  days: number
  cells: { dow: number; hour: number; reached: number; dials: number; rate: number | null }[]
}

export const useTiming = (country: string) =>
  useQuery({ queryKey: ['s-timing', country], queryFn: () => get<PickupTimes>(`/timing?country=${encodeURIComponent(country)}`) })

/** Which of five shades a cell gets: equal steps between the lowest and highest rate seen (null = no calls). */
export function heatmapCells(cells: PickupTimes['cells']): (PickupTimes['cells'][number] & { step: number | null })[] {
  const rates = cells.map((c) => c.rate).filter((r): r is number => r != null)
  const lo = Math.min(...rates), hi = Math.max(...rates)
  return cells.map((c) => ({
    ...c,
    step: c.rate == null ? null : hi - lo < 1e-9 ? 2 : Math.min(4, Math.floor(((c.rate - lo) / (hi - lo)) * 5)),
  }))
}
