import { describe, expect, it } from 'vitest'
import { chanceText, curveBars, playbookDiff, testProgress, type PlaybookEntry } from '@/lib/learning'

const e = (id: string, patch: Partial<PlaybookEntry> = {}): PlaybookEntry => ({
  id, kind: 'objection', when: 'they object', do: 'ask why', say: '', origin: 'manual', pinned: false,
  evidence: { leads: 0, calls: 0, progressed: 0, attempt_ids: [] }, since: 1, ...patch,
})

describe('learning helpers', () => {
  it('says a probability in plain words', () => {
    expect(chanceText(undefined)).toBe('Too early to tell')
    expect(chanceText(0.96)).toBe('Likely better (96%)')
    expect(chanceText(0.04)).toBe('Likely worse (96% sure)')
    expect(chanceText(0.5)).toBe('Too early to tell')
    expect(chanceText(0.7)).toMatch(/^Leaning better/)
  })

  it('diffs two playbooks entry by entry', () => {
    const diff = playbookDiff([e('a'), e('b'), e('c')], [e('a'), e('b', { do: 'ask what is behind it' }), e('d')])
    expect(diff.map((d) => `${d.op}:${d.entry.id}`)).toEqual(['same:a', 'edit:b', 'add:d', 'remove:c'])
    expect(playbookDiff([e('a', { pinned: true })], [e('a')])[0].op).toBe('same')   // pinning isn't a change
  })

  it('shows progress to the next planned check', () => {
    expect(testProgress({ next_look: 40, looks: [40, 80], last_eval: { challenger: { decided: 10 } } } as never)).toEqual({ done: 10, next: 40, pct: 25 })
    expect(testProgress(null)).toEqual({ done: 0, next: null, pct: 100 })
  })

  it('turns weekly rates into chart points', () => {
    const bars = curveBars([{ week: '2026-11-16', reached: 12, next_step_rate: 0.25, meeting_rate: null }])
    expect(bars[0]).toMatchObject({ next: 25, meet: 0, reached: 12 })
    expect(bars[0].label).toMatch(/16/)
  })
})
