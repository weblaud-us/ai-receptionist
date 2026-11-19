import { useMutation } from '@tanstack/react-query'
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
