import { describe, expect, it } from 'vitest'
import { filtersLabel, moveDeal } from '@/components/sales/crm-logic'
import type { Board, Deal } from '@/lib/sales'

const deal = (id: string, stage: Deal['stage'], amount: number, probability: number): Deal => ({
  id, lead_id: `lead_${id}`, offer_id: null, campaign_id: null, title: id, amount, currency: 'USD', stage, probability,
  expected_close: null, owner_id: '', lost_reason: '', proposal: null, custom: {}, won_at: null, created_at: '', updated_at: '',
})

const board = (): Board => ({
  columns: { qualified: [deal('a', 'qualified', 1000, 0.2)], meeting_booked: [], proposal: [deal('b', 'proposal', 5000, 0.6)], won: [], lost: [] },
  totals: {
    qualified: { count: 1, value: 1000, weighted: 200 }, meeting_booked: { count: 0, value: 0, weighted: 0 },
    proposal: { count: 1, value: 5000, weighted: 3000 }, won: { count: 0, value: 0, weighted: 0 }, lost: { count: 0, value: 0, weighted: 0 },
  },
  probability: { qualified: 0.2, meeting_booked: 0.4, proposal: 0.6, won: 1, lost: 0 },
})

describe('deal board', () => {
  it('moves a deal and keeps every column total right', () => {
    const next = moveDeal(board(), 'a', 'meeting_booked')
    expect(next.columns.qualified).toHaveLength(0)
    expect(next.columns.meeting_booked[0]).toMatchObject({ id: 'a', stage: 'meeting_booked', probability: 0.4 })
    expect(next.totals.qualified).toEqual({ count: 0, value: 0, weighted: 0 })
    expect(next.totals.meeting_booked).toEqual({ count: 1, value: 1000, weighted: 400 })
    expect(next.totals.proposal.weighted).toBe(3000)
  })

  it('leaves the board alone for a move to the same stage or an unknown deal', () => {
    const b = board()
    expect(moveDeal(b, 'a', 'qualified')).toBe(b)
    expect(moveDeal(b, 'nope', 'won')).toBe(b)
  })

  it('describes saved view filters', () => {
    expect(filtersLabel({ status: 'interested', min_fit: 60, q: '' })).toBe('status: interested · min fit: 60')
  })
})
