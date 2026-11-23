/** Landing-page live demo leads ("Talk to it now", Google sign-in): types and data hooks for /platform/demo-leads. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'

export type DemoLeadStatus = 'new' | 'contacted' | 'qualified' | 'not_a_fit'

export const DEMO_STATUS_LABEL: Record<DemoLeadStatus, string> = {
  new: 'New',
  contacted: 'Contacted',
  qualified: 'Qualified',
  not_a_fit: 'Not a fit',
}

export interface DemoLead {
  id: string
  name: string
  email: string
  email_domain: string
  hosted_domain: string
  practice_name: string
  region: 'au' | 'uk' | string
  status: DemoLeadStatus
  notes: string
  demo_count: number
  talk_seconds: number
  calls: number
  last_summary: string | null
  utm: { source?: string; medium?: string; campaign?: string }
  page: string
  referrer: string
  first_seen_at: string | null
  last_seen_at: string | null
  last_demo_at: string | null
}

export interface DemoSession {
  call_id: string
  tenant_id: string
  started_at: string
  duration_s: number
  outcome: string
  summary: string | null
  tools_used: string[]
  ended_reason: string | null
  transcript_available: boolean
  transcript: { role: 'user' | 'assistant' | string; text: string; at?: string }[]
}

type Params = Record<string, string | number | boolean | null | undefined>
const BASE = '/platform/demo-leads'
const NO_TENANT = { tenant: null } as const // platform-level: never send X-Tenant-Id

export const useDemoLeads = (params: Params) =>
  useQuery({
    queryKey: ['demo-leads', params],
    queryFn: () =>
      api<{ leads: DemoLead[]; counts: Record<DemoLeadStatus, number>; statuses: DemoLeadStatus[]; enabled: Record<string, boolean> }>(
        BASE, { params, ...NO_TENANT }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })

export const useDemoLead = (id: string | null) =>
  useQuery({
    queryKey: ['demo-lead', id],
    queryFn: () => api<{ lead: DemoLead; sessions: DemoSession[] }>(`${BASE}/${id}`, NO_TENANT),
    enabled: !!id,
  })

export function useDemoLeadMutations() {
  const qc = useQueryClient()
  const done = () => {
    void qc.invalidateQueries({ queryKey: ['demo-leads'] })
    void qc.invalidateQueries({ queryKey: ['demo-lead'] })
  }
  return {
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: string; status?: DemoLeadStatus; notes?: string }) =>
        api<{ lead: DemoLead }>(`${BASE}/${id}`, { method: 'PATCH', body, ...NO_TENANT }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api<{ ok: boolean; calls_deleted: number }>(`${BASE}/${id}`, { method: 'DELETE', ...NO_TENANT }),
      onSuccess: done,
    }),
  }
}

/** Download the (filtered) list as a CSV file. */
export async function downloadDemoLeadsCsv(params: Params): Promise<void> {
  const text = await api<string>(`${BASE}.csv`, { params, ...NO_TENANT })
  const url = URL.createObjectURL(new Blob([typeof text === 'string' ? text : ''], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `demo-leads-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

/** "3m 05s" of talking across all demos. */
export function talkTime(seconds: number): string {
  if (!seconds) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}

/** Where the lead came from, for a person: "google / cpc · au-dental", else the page. */
export function sourceText(lead: Pick<DemoLead, 'utm' | 'referrer' | 'page'>): string {
  const { source, medium, campaign } = lead.utm ?? {}
  const utm = [source && medium ? `${source} / ${medium}` : source || medium, campaign].filter(Boolean).join(' · ')
  if (utm) return utm
  if (lead.referrer) {
    try {
      return new URL(lead.referrer).hostname.replace(/^www\./, '')
    } catch {
      return lead.referrer
    }
  }
  return 'Direct'
}
