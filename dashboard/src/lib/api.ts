const TOKEN_KEY = 'hotel_admin_token'
const USER_KEY = 'hotel_admin_user'

export const API_BASE: string = (
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8002'
).replace(/\/$/, '')

export const authStore = {
  get token() {
    return localStorage.getItem(TOKEN_KEY)
  },
  get user() {
    return localStorage.getItem(USER_KEY)
  },
  set(token: string, user: string) {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(USER_KEY, user)
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  },
}

/** Fired when the API answers 401 on an authenticated call, so the app can return to /login. */
export const UNAUTHORIZED_EVENT = 'hotel-admin-unauthorized'

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

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', params, body, auth = true } = opts
  const url = new URL(API_BASE + path)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
  }

  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth && authStore.token) headers.Authorization = `Bearer ${authStore.token}`

  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}. Is the backend running?`)
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
    if (res.status === 401 && auth) {
      authStore.clear()
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(res.status, errorMessage(data, `Request failed (${res.status})`))
  }
  return data as T
}
