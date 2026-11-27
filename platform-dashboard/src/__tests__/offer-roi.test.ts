import { describe, expect, it } from 'vitest'
import { roiExample, spokenUrl } from '@/components/sales/offer-editor/roi'
import { shiftUnconfirmed } from '@/components/sales/offer-editor/readiness'
import type { OfferRoi } from '@/lib/sales'

const roi: OfferRoi = {
  enabled: true, result_label: 'extra revenue a month', monthly_cost: null,
  inputs: [{ question: 'Missed calls a week?', label: 'missed calls a week', per: 'week', max: 1000 },
    { question: 'Value of a new patient?', label: 'value of a new patient', per: 'once', max: 1_000_000 }],
  factors: [{ label: 'answered', value: 80, percent: true }, { label: 'booked', value: 50, percent: true }],
}

describe('ROI preview', () => {
  it('matches the server: weeks become months, your figures multiply in, two significant figures', () => {
    expect(roiExample(roi, [10, 1000])).toBe(17000)
    expect(roiExample(roi, [10])).toBeNull()
    expect(roiExample(roi, [10, NaN])).toBeNull()
  })

  it('says web addresses the way the agent does', () => {
    expect(spokenUrl('https://www.weblaud.com/book')).toBe('weblaud dot com slash book')
    expect(spokenUrl('cal.weblaud.com/book-a-call/?x=1')).toBe('cal dot weblaud dot com slash book dash a dash call')
  })

  it('moves ROI figure confirmations up when one is removed', () => {
    expect(shiftUnconfirmed(['roi.factors.0', 'roi.factors.2', 'proof_points.1'], 'roi.factors', 1))
      .toEqual(['roi.factors.0', 'roi.factors.1', 'proof_points.1'])
  })
})
