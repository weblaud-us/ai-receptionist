/**
 * What a call-minute costs to or from a number (core/billing/carrier_rates.py on the server): the phone
 * network's rate for that country and line, plus the AI stack. Shown before a call, never a block.
 */

export interface PerMinute { low: number; mid: number; high: number }

/** One number's estimate (single-call review, Economics lookup). */
export interface CallCost {
  direction: 'inbound' | 'outbound'
  country: string
  country_name: string
  line_kind: string
  /** "landline", "mobile", "toll-free"… in words. */
  line_text: string
  carrier_usd_per_min: number
  per_minute: PerMinute
  typical_minutes: number
  typical_call_usd: number
  /** twilio = this account's live rate; estimate = the fallback table; none = not on the phone network. */
  source: 'twilio' | 'estimate' | 'none'
  basis: string
  fetched_at: string | null
  /** Above the platform's warning limit (SALES_CARRIER_WARN_USD_PER_MIN). */
  warn: boolean
}

/** What's kept on an attempt when it's dialled (the power dialer's call card). */
export interface CostStamp {
  usd_per_min: number | null
  carrier_usd_per_min: number | null
  country: string
  line_kind: string
  source: CallCost['source']
  basis: string
}

/** A list of numbers (a campaign's leads), grouped by country and line. */
export interface CostSummary {
  groups: { country: string; country_name: string; line_kind: string; line_text: string; count: number
    usd_per_min: number; carrier_usd_per_min: number; source: CallCost['source']; warn: boolean }[]
  numbers: number
  low: number | null
  high: number | null
  typical_minutes: number
  typical_total_usd: number
  warn: boolean
  sampled: boolean
}

/** $0.044 under a dime, $0.11 above: enough digits to compare rates without noise. */
export const usdRate = (v: number | null | undefined) =>
  v == null ? '–' : v < 0.1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`

export const SOURCE_TEXT: Record<CallCost['source'], string> = {
  twilio: 'Twilio rate',
  estimate: 'estimate',
  none: 'no phone network',
}

const LINE_TEXT: Record<string, string> = {
  landline: 'landline', mobile: 'mobile', landline_or_mobile: 'landline or mobile', toll_free: 'toll-free',
  business_line: 'business line', voip: 'internet phone', premium_rate: 'premium rate',
}

/** "About $0.11/min · Australia, mobile · a 3-min call ≈ $0.33 (Twilio rate)" */
export const costSentence = (c: CallCost) =>
  `About ${usdRate(c.per_minute.mid)}/min · ${[c.country_name, c.line_text].filter(Boolean).join(', ')} · ` +
  `a ${c.typical_minutes}-min call ≈ $${c.typical_call_usd.toFixed(2)} (${SOURCE_TEXT[c.source]})`

/** The call card while it rings: "≈ $0.11/min · AU mobile". */
export const stampSentence = (s: CostStamp) =>
  `≈ ${usdRate(s.usd_per_min)}/min · ${[s.country, LINE_TEXT[s.line_kind] ?? ''].filter(Boolean).join(' ')}` +
  (s.source === 'estimate' ? ' (estimate)' : '')

/** "$0.044–$0.11/min" for a campaign's leads; "" when there are none. */
export const rangeSentence = (s: CostSummary | undefined) => {
  if (!s || s.low == null || s.high == null) return ''
  return s.low === s.high ? `${usdRate(s.low)}/min` : `${usdRate(s.low)}–${usdRate(s.high)}/min`
}
