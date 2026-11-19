/**
 * Where each app lives. The client dashboard (businesses) and the platform admin app are separate
 * deployments on separate origins; they link to each other for the platform admin's hand-off.
 * Baked in at build time (VITE_*), like VITE_API_URL.
 */

const trim = (url: string) => url.replace(/\/$/, '')

export const CLIENT_URL: string = trim((import.meta.env.VITE_CLIENT_URL as string | undefined) ?? 'http://localhost:5173')

export const PLATFORM_URL: string = trim((import.meta.env.VITE_PLATFORM_URL as string | undefined) ?? 'http://localhost:5174')

/** Client-dashboard URL that opens a business as a platform admin (audited server-side). */
export const impersonateUrl = (tenantId: string) => `${CLIENT_URL}/impersonate/${encodeURIComponent(tenantId)}`
