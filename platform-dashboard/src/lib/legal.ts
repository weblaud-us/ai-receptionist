/** Legal documents (Terms, Privacy, Subscription, Cookies, DPA…): edited here, published on the marketing site. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@receptionist/shared/lib/api'

export interface LegalDocSummary {
  key: string
  title: string
  published_version: string | null
  published_at: string | null
  draft_updated_at: string | null
  unpublished_changes: boolean
  placeholders: number
}

export interface LegalVersionRow {
  key: string
  version: string
  revision: number
  published_at: string
  by: string
  material: boolean
  summary: string
}

export interface LegalDoc extends LegalDocSummary {
  draft_md: string
  draft_placeholders: string[]
  published_md: string
  history: LegalVersionRow[]
  url: string
}

const NO_TENANT = { tenant: null } as const // platform-level: never send X-Tenant-Id
const BASE = '/platform/legal/documents'

export const useLegalDocs = () =>
  useQuery({
    queryKey: ['legal-docs'],
    queryFn: () => api<{ documents: LegalDocSummary[]; site_url: string }>(BASE, NO_TENANT),
  })

export const useLegalDoc = (key: string | null) =>
  useQuery({
    queryKey: ['legal-doc', key],
    queryFn: () => api<LegalDoc>(`${BASE}/${key}`, NO_TENANT),
    enabled: !!key,
  })

export const useLegalVersion = (key: string | null, version: string | null) =>
  useQuery({
    queryKey: ['legal-version', key, version],
    queryFn: () => api<LegalVersionRow & { md: string; html: string }>(`${BASE}/${key}/versions/${version}`, NO_TENANT),
    enabled: !!key && !!version,
  })

export const useLegalPreview = (markdown: string, enabled: boolean) =>
  useQuery({
    queryKey: ['legal-preview', markdown],
    queryFn: () => api<{ html: string; placeholders: string[] }>('/platform/legal/preview', { method: 'POST', body: { markdown }, ...NO_TENANT }),
    enabled,
    staleTime: Infinity,
    placeholderData: (prev) => prev,
  })

export function useLegalMutations(key: string) {
  const qc = useQueryClient()
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ['legal-docs'] }), qc.invalidateQueries({ queryKey: ['legal-doc', key] })])
  return {
    save: useMutation({
      mutationFn: (body: { title: string; markdown: string }) => api<LegalDoc>(`${BASE}/${key}/draft`, { method: 'PUT', body, ...NO_TENANT }),
      onSuccess: refresh,
    }),
    publish: useMutation({
      mutationFn: (body: { material: boolean; summary: string }) =>
        api<{ version: string; revision: number; material: boolean; previous_version: string | null }>(
          `${BASE}/${key}/publish`, { method: 'POST', body, ...NO_TENANT }),
      onSuccess: refresh,
    }),
  }
}
