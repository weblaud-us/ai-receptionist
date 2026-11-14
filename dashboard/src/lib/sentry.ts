import * as Sentry from '@sentry/react'

// Phone numbers (9–15 digits, optional +) and e-mails are masked before anything leaves the browser.
const PHONE_RE = /\+?\d{9,15}/g
const EMAIL_RE = /([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})/g

function redact<T>(value: T): T {
  if (typeof value === 'string') {
    return value
      .replace(PHONE_RE, (m) => `${m.slice(0, m.length > 9 ? 4 : 0)}•••${m.slice(-4)}`)
      .replace(EMAIL_RE, '$1•••@$2') as T
  }
  if (Array.isArray(value)) return value.map(redact) as T
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redact(v)])) as T
  }
  return value
}

/** Error tracking. Enabled only when VITE_SENTRY_DSN is set at build time. */
export function initSentry() {
  const dsn = import.meta.env.VITE_SENTRY_DSN as string | undefined
  if (!dsn) return
  Sentry.init({
    dsn,
    environment: (import.meta.env.VITE_APP_ENV as string | undefined) ?? import.meta.env.MODE,
    release: import.meta.env.VITE_GIT_SHA as string | undefined,
    // ⚖ no guest phone numbers or e-mails in error reports (PII is off by default; we also redact)
    tracesSampleRate: 0,
    beforeSend: (event) => redact(event),
    beforeBreadcrumb: (crumb) => redact(crumb),
  })
}
