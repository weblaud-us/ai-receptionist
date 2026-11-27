/** ROI preview and spoken web addresses for the offer editor. */
import type { OfferRoi } from '@/lib/sales'

/** How a web address is said on the phone (mirrors offers.spoken_url on the server). */
export function spokenUrl(url: string): string {
  return url.trim().replace(/^https?:\/\//i, '').replace(/^www\./i, '').split(/[?#]/)[0].replace(/\/+$/, '')
    .toLowerCase().replace(/\./g, ' dot ').replace(/\//g, ' slash ').replace(/-/g, ' dash ').replace(/\s+/g, ' ').trim()
}

const roundFigure = (x: number) => {
  if (x <= 0) return 0
  const digits = String(Math.trunc(x)).length - 2
  return digits > 0 ? Math.round(x / 10 ** digits) * 10 ** digits : Math.round(x)
}

/** What the estimate_roi tool would work out for these example answers (mirrors offers.roi_estimate). */
export function roiExample(roi: OfferRoi, answers: number[]): number | null {
  if (!roi.inputs.length || answers.length < roi.inputs.length || answers.some((a) => !Number.isFinite(a) || a < 0)) return null
  let amount = 1
  roi.inputs.forEach((x, i) => { amount *= answers[i] * (x.per === 'week' ? 52 / 12 : 1) })
  for (const f of roi.factors) amount *= f.value / (f.percent ? 100 : 1)
  return roundFigure(amount)
}
