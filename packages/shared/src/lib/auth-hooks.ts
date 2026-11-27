import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type TokenResponse } from './api'

/** Two-factor (TOTP) setup for the signed-in user. Platform admins must have it on. */
export function useMfa() {
  return {
    setup: useMutation({ mutationFn: () => api<{ secret: string; otpauth_uri: string }>('/auth/mfa/setup', { method: 'POST' }) }),
    enable: useMutation({
      mutationFn: (code: string) => api<TokenResponse>('/auth/mfa/enable', { method: 'POST', body: { code } }),
    }),
    disable: useMutation({ mutationFn: (code: string) => api('/auth/mfa/disable', { method: 'POST', body: { code } }) }),
  }
}

export interface AccountInfo {
  /** The bootstrap .env admin: nothing here can be changed from the dashboard. */
  legacy: boolean
  email: string
  name: string
  /** A new address waiting for its confirmation link to be opened. */
  pending_email: string | null
  created_at: string | null
  last_login_at: string | null
  password_changed_at: string | null
}

export interface AccountSession {
  id: string
  user_agent: string
  mfa: boolean
  signed_in_at: string | null
  last_active_at: string | null
  /** This browser. */
  current: boolean
}

const ACCOUNT = ['auth', 'account'] as const
const SESSIONS = ['auth', 'sessions'] as const

export function useAccount() {
  return useQuery({ queryKey: ACCOUNT, queryFn: () => api<AccountInfo>('/auth/account') })
}

export function useSessions(enabled = true) {
  return useQuery({
    queryKey: SESSIONS,
    queryFn: () => api<{ sessions: AccountSession[] }>('/auth/sessions').then((r) => r.sessions),
    enabled,
  })
}

/** The signed-in user's own name, password, e-mail and sessions. */
export function useAccountActions() {
  const qc = useQueryClient()
  const refetch = () => {
    void qc.invalidateQueries({ queryKey: ACCOUNT })
    void qc.invalidateQueries({ queryKey: SESSIONS })
  }
  return {
    updateName: useMutation({
      mutationFn: (name: string) => api('/auth/profile', { method: 'PATCH', body: { name } }),
      onSuccess: refetch,
    }),
    changePassword: useMutation({
      mutationFn: (body: { current_password: string; new_password: string }) =>
        api<{ signed_out: number }>('/auth/password/change', { method: 'POST', body }),
      onSuccess: refetch,
    }),
    changeEmail: useMutation({
      mutationFn: (body: { password: string; new_email: string }) =>
        api<{ pending_email: string; emailed: boolean }>('/auth/email/change', { method: 'POST', body }),
      onSuccess: refetch,
    }),
    cancelEmailChange: useMutation({
      mutationFn: () => api('/auth/email/change', { method: 'DELETE' }),
      onSuccess: refetch,
    }),
    revokeSession: useMutation({
      mutationFn: (id: string) => api(`/auth/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
      onSuccess: refetch,
    }),
    revokeOthers: useMutation({
      mutationFn: () => api<{ signed_out: number }>('/auth/sessions/revoke-others', { method: 'POST' }),
      onSuccess: refetch,
    }),
  }
}
