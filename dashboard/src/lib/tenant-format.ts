/** Formatting helpers for tenant data: times in the business's timezone, outcomes, roles. */

// Shared with the platform app (business provisioning form).
export { CURRENCIES, TIMEZONES } from '@receptionist/shared/lib/format'

/** A UTC ISO timestamp shown as wall-clock time in the business's timezone. */
export function inZone(iso: string | null | undefined, tz: string, opts: Intl.DateTimeFormatOptions = {}): string {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat('en-US', { timeZone: tz, ...opts }).format(new Date(iso))
  } catch {
    return iso
  }
}

export const zoneTime = (iso: string, tz: string) => inZone(iso, tz, { hour: 'numeric', minute: '2-digit' })
export const zoneDay = (iso: string, tz: string) => inZone(iso, tz, { weekday: 'short', day: 'numeric', month: 'short' })
export const zoneDateTime = (iso: string, tz: string) =>
  inZone(iso, tz, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })

/** `YYYY-MM-DD` of an instant in the business's timezone. */
export function zoneDateKey(iso: string, tz: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(new Date(iso))
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}

/** Today's date in the business's timezone. */
export function zoneToday(tz: string): string {
  return zoneDateKey(new Date().toISOString(), tz)
}

/** Business-local date + HH:MM → UTC ISO string (handles DST by probing the offset). */
export function localToUtcISO(date: string, time: string, tz: string): string {
  const [y, m, d] = date.split('-').map(Number)
  const [hh, mm] = time.split(':').map(Number)
  const guess = Date.UTC(y, m - 1, d, hh, mm)
  const offsetAt = (ms: number) => {
    const p = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric',
    }).formatToParts(new Date(ms))
    const g = (t: string) => Number(p.find((x) => x.type === t)?.value)
    return Date.UTC(g('year'), g('month') - 1, g('day'), g('hour'), g('minute')) - ms
  }
  const first = guess - offsetAt(guess)
  return new Date(guess - offsetAt(first)).toISOString()
}

export const OUTCOME_LABEL: Record<string, string> = {
  booked: 'Booked', rescheduled: 'Rescheduled', cancelled: 'Cancelled', message: 'Message taken',
  transferred: 'Transferred', faq: 'Question answered', completed: 'Completed', hangup: 'Hung up',
  voicemail: 'Voicemail', spam: 'Spam', unassigned: 'Unassigned number', busy: 'Lines busy',
}

export const OUTCOME_STYLE: Record<string, string> = {
  booked: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  rescheduled: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
  transferred: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-500/20 dark:text-indigo-300',
  message: 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300',
  cancelled: 'bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300',
}

export const ROLE_LABEL: Record<string, string> = { owner: 'Owner', admin: 'Admin', staff: 'Staff' }

export const APPOINTMENT_STYLE: Record<string, string> = {
  confirmed: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
  pending: 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300',
  completed: 'bg-slate-200 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300',
  cancelled: 'bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300',
  no_show: 'bg-orange-100 text-orange-800 dark:bg-orange-500/20 dark:text-orange-300',
}

export const URGENCY_STYLE: Record<string, string> = {
  emergency: 'bg-red-100 text-red-800 dark:bg-red-500/20 dark:text-red-300',
  today: 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300',
  this_week: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
  flexible: 'bg-slate-100 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300',
}
