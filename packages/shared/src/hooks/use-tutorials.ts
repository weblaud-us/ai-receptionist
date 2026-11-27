/**
 * Which welcome tours and getting-started lists the signed-in person has finished or skipped
 * (GET/PUT/DELETE /me/tutorials). Saved on their account, so it follows them across devices.
 *
 * Changes show at once and are rolled back if the save fails. If progress can't be loaded at all,
 * `persist` is false: tours then never start by themselves, rather than on every page load.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/auth'
import { api } from '../lib/api'

export type TutorialStatus = 'done' | 'skipped'

export interface TutorialsResponse {
  tutorials: Record<string, { status: TutorialStatus; at: string | null }>
  /** False for the bootstrap admin (no stored account): progress lasts only this session. */
  persist: boolean
}

export interface Tutorials {
  /** Progress has loaded (or failed to load). */
  ready: boolean
  persist: boolean
  status: (key: string) => TutorialStatus | undefined
  seen: (key: string) => boolean
  mark: (key: string, status: TutorialStatus) => void
  reset: (key: string) => void
}

const path = (key: string) => `/me/tutorials/${encodeURIComponent(key)}`

export function useTutorials(): Tutorials {
  const { status: authStatus, user } = useAuth()
  const qc = useQueryClient()
  const queryKey = ['me', 'tutorials', user?.id ?? null]
  const q = useQuery({
    queryKey,
    queryFn: () => api<TutorialsResponse>('/me/tutorials', { tenant: null }),
    enabled: authStatus === 'authenticated',
    staleTime: Infinity,
    retry: false,
  })
  const data = q.data
  const persist = !!data?.persist && !q.isError

  const change = useMutation({
    meta: { silentError: true },
    mutationFn: ({ key, status }: { key: string; status: TutorialStatus | null }) =>
      persist
        ? api<TutorialsResponse>(path(key), status ? { method: 'PUT', body: { status }, tenant: null } : { method: 'DELETE', tenant: null })
        : Promise.resolve(null),
    onMutate: async ({ key, status }) => {
      await qc.cancelQueries({ queryKey })
      const before = qc.getQueryData<TutorialsResponse>(queryKey)
      const base = before ?? { tutorials: {}, persist: false }
      const tutorials = { ...base.tutorials }
      if (status) tutorials[key] = { status, at: new Date().toISOString() }
      else delete tutorials[key]
      qc.setQueryData<TutorialsResponse>(queryKey, { ...base, tutorials })
      return { before }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.before) qc.setQueryData(queryKey, ctx.before)
    },
    onSuccess: (res) => {
      if (res) qc.setQueryData(queryKey, res)
    },
  })

  const saved = data?.tutorials ?? {}
  return {
    ready: authStatus === 'authenticated' && (q.isSuccess || q.isError),
    persist,
    status: (key) => saved[key]?.status,
    seen: (key) => key in saved,
    mark: (key, status) => change.mutate({ key, status }),
    reset: (key) => change.mutate({ key, status: null }),
  }
}
