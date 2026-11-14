import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type {
  Appointment,
  Contact,
  ContactDetail,
  ForwardingRow,
  KnowledgeItem,
  Lead,
  Overview,
  PhoneNumberInfo,
  PlatformTenant,
  Resource,
  ScheduleException,
  Service,
  Slot,
  Tenant,
  TeamMember,
  TenantCall,
  UnansweredItem,
  VerticalInfo,
} from './tenant-types'

type Params = Record<string, string | number | boolean | null | undefined>

function useInvalidate(...keys: string[]) {
  const qc = useQueryClient()
  return () => {
    for (const key of keys) void qc.invalidateQueries({ queryKey: [key] })
  }
}

/* ───────────── queries ───────────── */

export const useOverview = () =>
  useQuery({ queryKey: ['t-overview'], queryFn: () => api<Overview>('/t/overview'), refetchInterval: 60_000 })

export const useVerticals = () =>
  useQuery({ queryKey: ['t-verticals'], queryFn: () => api<{ verticals: VerticalInfo[] }>('/t/verticals'), staleTime: Infinity })

export const useServices = () =>
  useQuery({ queryKey: ['t-services'], queryFn: () => api<{ services: Service[] }>('/t/services') })

export const useResources = () =>
  useQuery({ queryKey: ['t-resources'], queryFn: () => api<{ resources: Resource[] }>('/t/resources') })

export const useExceptions = () =>
  useQuery({ queryKey: ['t-exceptions'], queryFn: () => api<{ exceptions: ScheduleException[] }>('/t/exceptions') })

export const useAppointments = (params: Params) =>
  useQuery({
    queryKey: ['t-appointments', params],
    queryFn: () => api<{ appointments: Appointment[]; timezone: string }>('/t/appointments', { params }),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  })

export const useSlots = (params: Params, enabled: boolean) =>
  useQuery({
    queryKey: ['t-slots', params],
    queryFn: () => api<{ slots: Slot[] }>('/t/slots', { params }),
    enabled,
    staleTime: 0,
  })

export const useContacts = (params: Params) =>
  useQuery({
    queryKey: ['t-contacts', params],
    queryFn: () => api<{ contacts: Contact[]; total: number; page: number; pages: number }>('/t/contacts', { params }),
    placeholderData: keepPreviousData,
  })

export const useContact = (id: string | null) =>
  useQuery({ queryKey: ['t-contact', id], queryFn: () => api<ContactDetail>(`/t/contacts/${id}`), enabled: !!id })

export const useTenantCalls = (params: Params) =>
  useQuery({
    queryKey: ['t-calls', params],
    queryFn: () => api<{ calls: TenantCall[]; total: number; page: number; pages: number }>('/t/calls', { params }),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  })

export const useTenantCall = (id: string | null) =>
  useQuery({ queryKey: ['t-call', id], queryFn: () => api<TenantCall>(`/t/calls/${id}`), enabled: !!id })

export const useLeads = (status?: string) =>
  useQuery({
    queryKey: ['t-leads', status],
    queryFn: () => api<{ leads: Lead[]; new: number }>('/t/leads', { params: { status } }),
    refetchInterval: 30_000,
  })

export const useKnowledge = () =>
  useQuery({
    queryKey: ['t-knowledge'],
    queryFn: () => api<{ items: KnowledgeItem[]; inline: boolean; count: number }>('/t/knowledge'),
  })

export const useUnanswered = (status = 'open') =>
  useQuery({
    queryKey: ['t-unanswered', status],
    queryFn: () => api<{ items: UnansweredItem[] }>('/t/unanswered', { params: { status } }),
  })

export const usePhoneNumbers = () =>
  useQuery({
    queryKey: ['t-phone'],
    queryFn: () => api<{ numbers: PhoneNumberInfo[]; forwarding: ForwardingRow[]; note: string }>('/t/phone-numbers'),
  })

export const useTeam = () =>
  useQuery({
    queryKey: ['t-team'],
    queryFn: () =>
      api<{ members: TeamMember[]; invites: { email: string; role: string; expires_at: string }[] }>('/t/team'),
  })

export const useAudit = () =>
  useQuery({
    queryKey: ['t-audit'],
    queryFn: () =>
      api<{ entries: { id: string; action: string; target_type: string; user_id: string; at: string; diff: Record<string, unknown> }[] }>(
        '/t/audit',
      ),
  })

export const usePlatformTenants = (enabled = true) =>
  useQuery({
    queryKey: ['p-tenants'],
    queryFn: () => api<{ tenants: PlatformTenant[]; total: number }>('/platform/tenants', { tenant: null }),
    enabled,
  })

/* ───────────── mutations ───────────── */

export function useUpdateSettings() {
  const done = useInvalidate('context', 't-overview')
  return useMutation({
    mutationFn: (changes: Partial<Tenant> | Record<string, unknown>) =>
      api<{ tenant: Tenant; greeting: string }>('/t/settings', { method: 'PATCH', body: changes }),
    onSuccess: done,
  })
}

export function useOnboarding() {
  const done = useInvalidate('context', 't-services', 't-knowledge')
  return {
    template: useMutation({
      mutationFn: (body: { vertical: string; replace?: boolean }) =>
        api<{ added: { services: number; knowledge: number } }>('/t/onboarding/template', { method: 'POST', body }),
      onSuccess: done,
    }),
    step: useMutation({
      mutationFn: (body: { step: string; completed?: boolean }) => api('/t/onboarding/step', { method: 'POST', body }),
      onSuccess: done,
    }),
  }
}

function crud<T extends { id: string }>(path: string, key: string, extra: string[] = []) {
  return function useCrud() {
    const done = useInvalidate(key, ...extra)
    return {
      create: useMutation({
        mutationFn: (body: Partial<Omit<T, 'id'>> | Record<string, unknown>) => api<T>(path, { method: 'POST', body }),
        onSuccess: done,
      }),
      update: useMutation({
        mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
          api<T>(`${path}/${id}`, { method: 'PATCH', body }),
        onSuccess: done,
      }),
      remove: useMutation({
        mutationFn: (id: string) => api(`${path}/${id}`, { method: 'DELETE' }),
        onSuccess: done,
      }),
    }
  }
}

export const useServiceMutations = crud<Service>('/t/services', 't-services')
export const useResourceMutations = crud<Resource>('/t/resources', 't-resources', ['t-services'])
export const useExceptionMutations = crud<ScheduleException>('/t/exceptions', 't-exceptions')
export const useKnowledgeMutations = crud<KnowledgeItem>('/t/knowledge', 't-knowledge')

export function useAppointmentMutations() {
  const done = useInvalidate('t-appointments', 't-slots', 't-overview', 't-contact')
  return {
    create: useMutation({
      mutationFn: (body: Record<string, unknown>) => api<Appointment>('/t/appointments', { method: 'POST', body }),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: string; status?: string; notes?: string; cancel_reason?: string }) =>
        api<Appointment>(`/t/appointments/${id}`, { method: 'PATCH', body }),
      onSuccess: done,
    }),
  }
}

export function useUpdateContact() {
  const done = useInvalidate('t-contacts', 't-contact')
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Partial<Contact>) =>
      api<Contact>(`/t/contacts/${id}`, { method: 'PATCH', body }),
    onSuccess: done,
  })
}

export function useUpdateLead() {
  const done = useInvalidate('t-leads', 't-overview')
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api(`/t/leads/${id}`, { method: 'PATCH', body: { status } }),
    onSuccess: done,
  })
}

export function useUnansweredMutations() {
  const done = useInvalidate('t-unanswered', 't-knowledge', 't-overview')
  return {
    answer: useMutation({
      mutationFn: ({ id, answer, question }: { id: string; answer: string; question?: string }) =>
        api(`/t/unanswered/${id}/answer`, { method: 'POST', body: { answer, question } }),
      onSuccess: done,
    }),
    dismiss: useMutation({
      mutationFn: (id: string) => api(`/t/unanswered/${id}/dismiss`, { method: 'POST' }),
      onSuccess: done,
    }),
  }
}

export const useImportKnowledge = () =>
  useMutation({
    mutationFn: (url: string) =>
      api<{ drafts: { question: string; answer: string }[]; source: string }>('/t/knowledge/import', {
        method: 'POST',
        body: { url },
      }),
  })

export const useTestCall = () =>
  useMutation({ mutationFn: () => api<{ url: string; room: string }>('/t/test-call', { method: 'POST' }) })

export function useTeamMutations() {
  const done = useInvalidate('t-team')
  return {
    invite: useMutation({
      mutationFn: (body: { email: string; role: string }) =>
        api<{ emailed: boolean; link: string }>('/t/team/invite', { method: 'POST', body }),
      onSuccess: done,
    }),
    role: useMutation({
      mutationFn: ({ id, role }: { id: string; role: string }) =>
        api(`/t/team/${id}`, { method: 'PATCH', body: { role } }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: string) => api(`/t/team/${id}`, { method: 'DELETE' }), onSuccess: done }),
  }
}

export function usePlatformMutations() {
  const done = useInvalidate('p-tenants')
  return {
    provision: useMutation({
      mutationFn: (body: Record<string, unknown>) =>
        api<{ tenant: { id: string; name: string }; set_password_link: string | null }>('/platform/tenants', {
          method: 'POST',
          body,
          tenant: null,
        }),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
        api(`/platform/tenants/${id}`, { method: 'PATCH', body, tenant: null }),
      onSuccess: done,
    }),
    assignNumber: useMutation({
      mutationFn: (body: { e164: string; tenant_id: string }) =>
        api('/platform/phone-numbers', { method: 'POST', body, tenant: null }),
      onSuccess: done,
    }),
    releaseNumber: useMutation({
      mutationFn: (e164: string) => api(`/platform/phone-numbers/${encodeURIComponent(e164)}`, { method: 'DELETE', tenant: null }),
      onSuccess: done,
    }),
  }
}

/* ───────────── integrations, recordings, two-factor ───────────── */

export interface IntegrationsStatus {
  google_calendar: {
    available: boolean
    connected: boolean
    calendar_id: string | null
    last_sync_at: string | null
    last_error: string | null
    connected_at: string | null
  }
  whatsapp: { phone_number_id: string | null }
}

export const useIntegrations = () =>
  useQuery({ queryKey: ['t-integrations'], queryFn: () => api<IntegrationsStatus>('/t/integrations') })

export function useGoogleCalendar() {
  const done = useInvalidate('t-integrations', 't-slots')
  return {
    connect: useMutation({ mutationFn: () => api<{ url: string }>('/t/integrations/google/connect', { method: 'POST' }) }),
    sync: useMutation({ mutationFn: () => api('/t/integrations/google/sync', { method: 'POST' }), onSuccess: done }),
    disconnect: useMutation({ mutationFn: () => api('/t/integrations/google/disconnect', { method: 'POST' }), onSuccess: done }),
    setCalendar: useMutation({
      mutationFn: (calendar_id: string) => api('/t/integrations/google', { method: 'PATCH', body: { calendar_id } }),
      onSuccess: done,
    }),
  }
}

export const useRecordingUrl = () =>
  useMutation({ mutationFn: (callId: string) => api<{ url: string; expires_in: number }>(`/t/calls/${callId}/recording`) })

export function useMfa() {
  return {
    setup: useMutation({ mutationFn: () => api<{ secret: string; otpauth_uri: string }>('/auth/mfa/setup', { method: 'POST' }) }),
    enable: useMutation({
      mutationFn: (code: string) => api<import('./api').TokenResponse>('/auth/mfa/enable', { method: 'POST', body: { code } }),
    }),
    disable: useMutation({ mutationFn: (code: string) => api('/auth/mfa/disable', { method: 'POST', body: { code } }) }),
  }
}
