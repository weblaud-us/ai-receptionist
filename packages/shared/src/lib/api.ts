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
  /** The response's `detail` when it is structured (e.g. {code: "stale_draft", updated_at, by}). */
  detail?: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

const detailOf = (data: unknown): unknown =>
  data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : undefined

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
    if (detail && typeof detail === 'object' && !Array.isArray(detail) && typeof (detail as { message?: unknown }).message === 'string') {
      const d = detail as { message: string; problems?: unknown }
      return Array.isArray(d.problems) && d.problems.length ? `${d.message}: ${d.problems.join('; ')}` : d.message
    }
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
    throw new ApiError(res.status, errorMessage(data, `Request failed (${res.status})`), detailOf(data))
  }
  return data as T
}

/**
 * POST and read server-sent events as they arrive (e.g. the website import's live progress).
 * EventSource can't send the Authorization header, so this is fetch + a stream reader. Refreshes the
 * session once on a 401, like api(). Resolves when the stream ends; rejects with ApiError when the
 * request is refused before streaming starts, or the connection drops.
 */
export async function streamEvents<E = Record<string, unknown>>(
  path: string,
  body: unknown,
  onEvent: (event: E) => void,
  opts: { signal?: AbortSignal; tenant?: string | null } = {},
): Promise<void> {
  const { signal } = opts
  const tenant = opts.tenant !== undefined ? opts.tenant : tenantOverride
  const open = () => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'text/event-stream' }
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`
    if (tenant) headers['X-Tenant-Id'] = tenant
    return fetch(API_BASE + path, { method: 'POST', headers, body: JSON.stringify(body), credentials: 'omit', signal })
  }
  let res: Response
  try {
    res = await open()
    if (res.status === 401 && (await refreshSession())) res = await open()
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}. Is the backend running?`)
  }
  if (!res.ok || !res.body) {
    let data: unknown = null
    try {
      data = await res.json()
    } catch {
      /* not JSON */
    }
    if (res.status === 401) {
      accessToken = null
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(res.status, errorMessage(data, `Request failed (${res.status})`), detailOf(data))
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  const flush = (block: string) => {
    // One event per blank-line-separated block; only `data:` lines carry it (`:` lines are keep-alives).
    const data = block
      .split('\n')
      .filter((l) => l.startsWith('data:'))
      .map((l) => l.slice(5).replace(/^ /, ''))
      .join('\n')
    if (!data) return
    try {
      onEvent(JSON.parse(data) as E)
    } catch {
      /* a malformed frame is skipped */
    }
  }
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value.replace(/\r\n/g, '\n')
      let cut = buffer.indexOf('\n\n')
      while (cut !== -1) {
        flush(buffer.slice(0, cut))
        buffer = buffer.slice(cut + 2)
        cut = buffer.indexOf('\n\n')
      }
    }
    if (buffer.trim()) flush(buffer)
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new ApiError(0, 'The connection dropped while reading the website.')
  }
}
