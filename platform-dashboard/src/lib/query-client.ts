import { MutationCache, QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ApiError } from '@receptionist/shared/lib/api'

type Notify = (message: string) => void

/**
 * Every failed mutation surfaces as ONE toast, here (401s are handled by the auth layer). Callers
 * don't toast errors themselves; a mutation whose errors are an expected step (e.g. "a lawyer must
 * review this first") sets `meta: { silentError: true }` and handles them where it is used.
 */
export function mutationErrorToast(error: unknown, meta: { silentError?: boolean } | undefined, notify: Notify = toast.error) {
  if (meta?.silentError) return
  if (error instanceof ApiError && error.status === 401) return
  notify(error instanceof Error ? error.message : 'Something went wrong')
}

export function createQueryClient(notify?: Notify) {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _variables, _result, mutation) => mutationErrorToast(error, mutation.meta, notify),
    }),
    defaultOptions: {
      queries: {
        retry: (count, error) => !(error instanceof ApiError && error.status < 500 && error.status !== 0) && count < 1,
        refetchOnWindowFocus: false,
        staleTime: 15_000,
      },
    },
  })
}
