import { format, formatDistanceToNowStrict, parseISO } from 'date-fns'

export function money(value: number | null | undefined, currency = 'BDT'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${currency} ${Math.round(value).toLocaleString('en-US')}`
}

export function compactMoney(value: number, currency = 'BDT'): string {
  const abs = Math.abs(value)
  const short =
    abs >= 1_000_000
      ? `${(value / 1_000_000).toFixed(1)}M`
      : abs >= 1_000
        ? `${(value / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}K`
        : String(Math.round(value))
  return `${currency} ${short}`
}

/** `2026-11-17` (a calendar date, no timezone) -> `17 Nov 2026` */
export function dateOnly(value: string | null | undefined, pattern = 'd MMM yyyy'): string {
  if (!value) return '—'
  try {
    return format(parseISO(value), pattern)
  } catch {
    return value
  }
}

/** ISO timestamp (UTC, `...Z`) -> local `17 Nov, 14:05` */
export function dateTime(value: string | null | undefined, pattern = 'd MMM, HH:mm'): string {
  if (!value) return '—'
  try {
    return format(parseISO(value), pattern)
  } catch {
    return value
  }
}

export function timeAgo(value: string | null | undefined): string {
  if (!value) return '—'
  try {
    return `${formatDistanceToNowStrict(parseISO(value))} ago`
  } catch {
    return value
  }
}

export function duration(seconds: number | null | undefined): string {
  if (!seconds && seconds !== 0) return '—'
  const s = Math.round(seconds)
  const m = Math.floor(s / 60)
  return m ? `${m}m ${s % 60}s` : `${s}s`
}

export function nights(checkIn: string, checkOut: string): number {
  const a = parseISO(checkIn).getTime()
  const b = parseISO(checkOut).getTime()
  return Math.max(Math.round((b - a) / 86_400_000), 0)
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function sourceLabel(source: string | null | undefined): string {
  switch (source) {
    case 'ai_receptionist':
      return 'AI receptionist (call / WhatsApp)'
    case 'dashboard':
      return 'Dashboard'
    case 'walk_in':
      return 'Walk-in'
    case 'web':
      return 'Website'
    default:
      return titleCase(source)
  }
}

/** Local calendar date as `YYYY-MM-DD` (not UTC). */
export function toISODate(d: Date): string {
  return format(d, 'yyyy-MM-dd')
}

/** `YYYY-MM-DD` + n days, as calendar arithmetic in UTC: the browser's timezone and DST never shift it. */
export function addDaysISO(iso: string, days: number): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10)
}

/**
 * `00880…` -> `+880…`. With the business's dialling code, a national number gets its leading 0 swapped:
 * `0412 345 678` + `+61` -> `+61412345678`. Without one, `01712 345 678` stays as typed (+ prefixed).
 */
export function normalizePhone(raw: string, dial?: string): string {
  const cleaned = raw.replace(/[\s\-().]/g, '')
  if (!cleaned) return ''
  if (cleaned.startsWith('00')) return `+${cleaned.slice(2)}`
  if (dial && /^0[1-9]/.test(cleaned)) return `${dial}${cleaned.slice(1)}`
  return cleaned.startsWith('+') ? cleaned : `+${cleaned}`
}

/** International format: `+`, a country code that does not start with 0, 8–15 digits in total. */
export function isValidPhone(normalized: string): boolean {
  return /^\+[1-9]\d{7,14}$/.test(normalized)
}

/** Timezones offered when a business is created or edited: LaudLine serves Australia and the UK only.
 *  Forms still show a record's own value when it isn't in the list. */
export const TIMEZONES = [
  'Australia/Sydney', 'Australia/Melbourne', 'Australia/Brisbane', 'Australia/Adelaide', 'Australia/Perth',
  'Australia/Hobart', 'Australia/Darwin', 'Europe/London',
]

export const CURRENCIES = ['AUD', 'GBP']

/** ISO countries offered on business forms (code + name). */
export const COUNTRIES = [['AU', 'Australia'], ['GB', 'United Kingdom']] as const
