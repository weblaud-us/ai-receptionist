import { useCallback } from 'react'
import type { ImportFound, ImportPage, ImportStep, SiteDraft } from '../lib/site-import'
import { useProgressStream } from './use-progress-stream'

export interface SiteImportProgressState {
  running: boolean
  url: string
  steps: ImportStep[]
  pages: ImportPage[]
  found: ImportFound[]
  startedAt: number | null
  finishedAt: number | null
  error: string | null
}

/**
 * Read a website with live progress (the steps, each page as it's read, what was found), then hand
 * back the draft. If streaming isn't possible (an older API, a proxy that buffers or cuts the
 * stream), it falls back to the one-shot `fallback` request so the import still works.
 */
export function useSiteImportStream({
  path,
  fallback,
  tenant,
  onDraft,
}: {
  /** e.g. '/t/onboarding/site-import/stream' */
  path: string
  /** The blocking import, used when streaming fails before a result arrives. */
  fallback: (url: string) => Promise<{ draft: SiteDraft }>
  /** Override the X-Tenant-Id header (platform calls send none: null). */
  tenant?: string | null
  onDraft: (draft: SiteDraft) => void
}) {
  const stream = useProgressStream<{ url: string }, SiteDraft>({
    path,
    tenant,
    fallback: ({ url }) => fallback(url).then((r) => r.draft),
    pick: (e) => e.draft as SiteDraft | undefined,
    onResult: onDraft,
  })
  const { run: start } = stream
  const run = useCallback((url: string) => start({ url }, url), [start])
  return { state: stream.state, run, reset: stream.reset }
}
