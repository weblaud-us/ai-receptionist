import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@receptionist/shared/lib/api'
import type { SiteDraft } from '@receptionist/shared/lib/site-import'
import type { Appointment, Contact, ContactDetail, ContactWhatsApp, DayView, ForwardingRow, KnowledgeItem, KnowledgeList, KnowledgeSuggestion, KnowledgeTestResult, Lead, Overview, PhoneNumberInfo, Resource, ScheduleException, Service, Slot, TeamMember, Tenant, TenantCall, UnansweredItem, VerticalInfo, VoiceCatalogue, WebhookEndpoint, WhatsAppOutboxItem, WhatsAppStatus, WhatsAppTemplate } from './tenant-types'

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

export const useResources = (enabled = true) =>
  useQuery({ queryKey: ['t-resources'], enabled, queryFn: () => api<{ resources: Resource[] }>('/t/resources') })

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
    queryFn: () => api<{ slots: Slot[]; timezone?: string }>('/t/slots', { params }),
    enabled,
    staleTime: 0,
    // One network blip (e.g. the API restarting) shouldn't leave the picker blank.
    retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
  })

export const useContacts = (params: Params, enabled = true) =>
  useQuery({
    queryKey: ['t-contacts', params],
    queryFn: () => api<{ contacts: Contact[]; total: number; page: number; pages: number }>('/t/contacts', { params }),
    enabled,
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
    queryFn: () => api<KnowledgeList>('/t/knowledge'),
  })

export const useKnowledgeSuggestions = () =>
  useQuery({
    queryKey: ['t-knowledge', 'suggestions'],
    queryFn: () => api<{ suggestions: KnowledgeSuggestion[] }>('/t/knowledge/suggestions'),
  })

export const useTestKnowledge = () =>
  useMutation({
    mutationFn: (question: string) => api<KnowledgeTestResult>('/t/knowledge/test', { method: 'POST', body: { question } }),
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
      api<{ members: TeamMember[]; invites: { email: string; role: string; resource_id?: string | null; expires_at: string }[] }>('/t/team'),
  })

export const useAudit = () =>
  useQuery({
    queryKey: ['t-audit'],
    queryFn: () =>
      api<{ entries: { id: string; action: string; target_type: string; user_id: string; at: string; diff: Record<string, unknown> }[] }>(
        '/t/audit',
      ),
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

/** "Start from your website": draft the setup from the site (nothing saved), then save what the owner kept. */
export function useSiteImport() {
  const qc = useQueryClient()
  return {
    fetch: useMutation({
      mutationFn: (url: string) => api<{ draft: SiteDraft }>('/t/onboarding/site-import', { method: 'POST', body: { url } }),
    }),
    apply: useMutation({
      mutationFn: (body: Record<string, unknown>) =>
        api<{ added: { services: number; knowledge: number } }>('/t/onboarding/apply-import', { method: 'POST', body }),
      // Wait for the refetch so the next step already sees the imported type and details.
      onSuccess: () => Promise.all(['context', 't-services', 't-knowledge', 't-overview'].map((k) => qc.invalidateQueries({ queryKey: [k] }))),
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
    /** Same time, another dentist (books the new one first, then cancels the old, in the PMS too). */
    reassign: useMutation({
      mutationFn: ({ id, resource_id }: { id: string; resource_id: string }) =>
        api<Appointment>(`/t/appointments/${id}/reassign`, { method: 'POST', body: { resource_id } }),
      onSuccess: done,
    }),
  }
}

/** Practitioners free at the same time as this appointment (the "Change dentist" dialog). */
export const useFreePractitioners = (appointmentId: string | undefined) =>
  useQuery({
    queryKey: ['t-free-practitioners', appointmentId],
    queryFn: () => api<{ practitioners: { _id: string; name: string }[] }>(
      `/t/appointments/${appointmentId}/free-practitioners`),
    enabled: !!appointmentId,
    staleTime: 0,
  })

/** A dentist's patients today or tomorrow. Dentists always get their own; staff pass `resourceId`. */
export const useMyDay = (day: 'today' | 'tomorrow', resourceId?: string) =>
  useQuery({
    queryKey: ['t-my-day', day, resourceId ?? null],
    queryFn: () => api<DayView>('/t/my-day', { params: { day, resource_id: resourceId } }),
    refetchInterval: 60_000,
  })

export function useMarkMyDay() {
  const done = useInvalidate('t-my-day', 't-appointments')
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'completed' | 'no_show' }) =>
      api(`/t/my-day/${id}`, { method: 'PATCH', body: { status } }),
    onSuccess: done,
  })
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
      mutationFn: ({ id, answer, question, tags }: { id: string; answer: string; question?: string; tags?: string[] }) =>
        api(`/t/unanswered/${id}/answer`, { method: 'POST', body: { answer, question, tags } }),
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
      mutationFn: (body: { email: string; role: string; resource_id?: string }) =>
        api<{ emailed: boolean; link: string }>('/t/team/invite', { method: 'POST', body }),
      onSuccess: done,
    }),
    role: useMutation({
      mutationFn: ({ id, role, resource_id }: { id: string; role: string; resource_id?: string }) =>
        api(`/t/team/${id}`, { method: 'PATCH', body: { role, resource_id } }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: string) => api(`/t/team/${id}`, { method: 'DELETE' }), onSuccess: done }),
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
  /** Dental practice-management system (Cliniko AU, Dentally UK). */
  pms?: {
    available: { cliniko: boolean; dentally: boolean }
    provider: 'builtin' | 'cliniko' | 'dentally'
    connected: boolean
    account: Record<string, string | number | null>
    last_sync_at: string | null
    last_error: string | null
    connected_at: string | null
  }
}

export type PmsProvider = 'cliniko' | 'dentally'

export function usePms() {
  const done = useInvalidate('t-integrations', 't-slots', 't-services', 't-resources')
  return {
    connect: useMutation({
      mutationFn: (body: { provider: PmsProvider; api_key: string }) =>
        api<{ success: boolean; sync: { practitioners: number; types: number } }>('/t/integrations/pms', {
          method: 'PUT',
          body,
        }),
      onSuccess: done,
    }),
    test: useMutation({ mutationFn: () => api('/t/integrations/pms/test', { method: 'POST' }) }),
    sync: useMutation({ mutationFn: () => api('/t/integrations/pms/sync', { method: 'POST' }), onSuccess: done }),
    disconnect: useMutation({ mutationFn: () => api('/t/integrations/pms', { method: 'DELETE' }), onSuccess: done }),
  }
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

export const useWebhooks = (enabled = true) =>
  useQuery({
    queryKey: ['t-webhooks'],
    queryFn: () => api<{ endpoints: WebhookEndpoint[]; events: string[] }>('/t/integrations/webhooks'),
    enabled,
  })

export function useWebhookMutations() {
  const done = useInvalidate('t-webhooks', 'context')
  return {
    create: useMutation({
      mutationFn: (body: { url: string; events: string[] }) =>
        api<{ endpoint: WebhookEndpoint; secret: string }>('/t/integrations/webhooks', { method: 'POST', body }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api(`/t/integrations/webhooks/${id}`, { method: 'DELETE' }),
      onSuccess: done,
    }),
    test: useMutation({
      mutationFn: (id: string) =>
        api<{ ok: boolean; status: number | null; attempts: number }>(`/t/integrations/webhooks/${id}/test`, { method: 'POST' }),
      onSuccess: done,
    }),
  }
}

export const useVoices = (provider: string) =>
  useQuery({
    queryKey: ['t-voices', provider],
    queryFn: () => api<VoiceCatalogue>(`/t/voices?provider=${encodeURIComponent(provider)}`),
    staleTime: 60 * 60_000,
  })


export const useWhatsAppStatus = () =>
  useQuery({ queryKey: ['t-wa-status'], queryFn: () => api<WhatsAppStatus>('/t/whatsapp/status'), staleTime: 60_000 })

export const useWhatsAppTemplates = (enabled: boolean) =>
  useQuery({
    queryKey: ['t-wa-templates'],
    queryFn: () => api<{ templates: WhatsAppTemplate[] }>('/t/whatsapp/templates'),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  })

export const useContactWhatsApp = (id: string | null, enabled = true) =>
  useQuery({
    queryKey: ['t-contact-wa', id],
    queryFn: () => api<ContactWhatsApp>(`/t/contacts/${id}/whatsapp`),
    enabled: !!id && enabled,
    refetchInterval: 15_000, // delivery receipts arrive by webhook
  })

export function useSendContactWhatsApp(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { text: string } | { template: string; language: string; params: string[] }) =>
      api<{ success: boolean; message: WhatsAppOutboxItem }>(`/t/contacts/${id}/whatsapp`, { method: 'POST', body }),
    // Failed sends are recorded too, so refresh either way.
    onSettled: () => qc.invalidateQueries({ queryKey: ['t-contact-wa', id] }),
  })
}
