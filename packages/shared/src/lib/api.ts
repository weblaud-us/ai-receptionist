/**
 * API client.
 *
 * Auth (Roadmap Task 2.10): the short-lived access token lives in memory only — never in
 * localStorage, where any XSS could read it. The refresh token is an httpOnly cookie the browser
 * sends to /auth/* only. On a 401 the client refreshes once and retries the request.
 *
 * Tenant: platform admins (and members of several businesses) pick the business they act on;
 * it is sent as X-Tenant-Id. Only the id is stored in localStorage — it isn't a secret.
 */

const TENANT_KEY = 'rcpt_active_tenant'

export const API_BASE: string = (
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8002'
).replace(/\/$/, '')

let accessToken: string | null = null

function readTenant(): string | null {
  try {
    return localStorage.getItem(TENANT_KEY)
  } catch {
    return null
  }
}

let tenantOverride: string | null = readTenant()

export const session = {
  get token() {
    return accessToken
  },
  setToken(token: string | null) {
    accessToken = token
  },
  get tenant() {
    return tenantOverride
  },
  setTenant(tenantId: string | null) {
    tenantOverride = tenantId
    try {
      if (tenantId) localStorage.setItem(TENANT_KEY, tenantId)
      else localStorage.removeItem(TENANT_KEY)
    } catch {
      /* private mode: the override lasts for this tab only */
    }
  },
  clear() {
    accessToken = null
    this.setTenant(null)
  },
}

/** Fired when the session can't be renewed, so the app can return to /login. */
export const UNAUTHORIZED_EVENT = 'rcpt-unauthorized'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Params = Record<string, string | number | boolean | null | undefined>

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  params?: Params
  body?: unknown
  auth?: boolean
  tenant?: string | null // override the active tenant for this call
}

function errorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // FastAPI validation errors
      return detail
        .map((d: { loc?: unknown[]; msg?: string }) =>
          `${(d.loc ?? []).filter((p) => p !== 'body').join('.')}: ${d.msg ?? 'invalid'}`,
        )
        .join('; ')
    }
  }
  return fallback
}

export interface TokenResponse {
  access_token: string | null
  expires_in?: number
  tenant_id: string | null
  role: string | null
  user?: { id: string; email: string; name: string; is_platform_admin: boolean; mfa_enabled?: boolean; email_verified?: boolean }
  memberships?: Membership[]
  mfa_required?: boolean
  mfa_token?: string
}

export interface Membership {
  tenant_id: string
  name: string
  vertical: string
  status: string
  role: string
}

let refreshing: Promise<TokenResponse | null> | null = null

/** Exchange the refresh cookie for a new access token. Single-flight: concurrent 401s share one call. */
export function refreshSession(tenantId?: string | null): Promise<TokenResponse | null> {
  if (refreshing && !tenantId) return refreshing
  const run = (async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(tenantId ? { tenant_id: tenantId } : {}),
      })
      if (!res.ok) return null
      const data = (await res.json()) as TokenResponse
      accessToken = data.access_token
      return data
    } catch {
      return null
    } finally {
      refreshing = null
    }
  })()
  if (!tenantId) refreshing = run
  return run
}

async function send(path: string, opts: RequestOptions): Promise<Response> {
  const { method = 'GET', params, body, auth = true } = opts
  const url = new URL(API_BASE + path)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
  }
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`
  const tenant = opts.tenant !== undefined ? opts.tenant : tenantOverride
  if (auth && tenant) headers['X-Tenant-Id'] = tenant
  try {
    return await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: path.startsWith('/auth/') ? 'include' : 'omit',
    })
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}. Is the backend running?`)
  }
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const auth = opts.auth ?? true
  let res = await send(path, opts)
  if (res.status === 401 && auth && !path.startsWith('/auth/')) {
    const renewed = await refreshSession()
    if (renewed) res = await send(path, opts)
  }

  let data: unknown = null
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }

  if (!res.ok) {
    if (res.status === 401 && auth && !path.startsWith('/auth/')) {
      accessToken = null
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(res.status, errorMessage(data, `Request failed (${res.status})`))
  }
  return data as T
}
