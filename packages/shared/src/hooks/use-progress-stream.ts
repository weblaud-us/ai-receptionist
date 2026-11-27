import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, streamEvents } from '../lib/api'
import type { ImportFound, ImportPage, ImportStep } from '../lib/site-import'
import type { SiteImportProgressState } from './use-site-import-stream'

type ProgressEvent =
  | ImportStep
  | ImportPage
  | ImportFound
  | ({ type: 'result' } & Record<string, unknown>)
  | { type: 'error'; status: number; message: string }

const IDLE: SiteImportProgressState = {
  running: false, url: '', steps: [], pages: [], found: [], startedAt: null, finishedAt: null, error: null,
}

/**
 * A long job that reports progress over server-sent events (utils/sse.py on the API): its steps, the
 * pages it read and what it found, then one result. If streaming isn't possible (an older API, a
 * proxy that buffers or cuts the stream) it falls back to the one-shot `fallback` request.
 *
 * `pick` takes the result out of the final `{"type": "result", …}` event.
 */
export function useProgressStream<TBody, TResult>({
  path,
  fallback,
  tenant,
  pick,
  onResult,
}: {
  path: string
  fallback?: (body: TBody) => Promise<TResult>
  /** Override the X-Tenant-Id header (platform calls send none: null). */
  tenant?: string | null
  pick: (result: Record<string, unknown>) => TResult | undefined
  onResult: (result: TResult) => void
}) {
  const [state, setState] = useState<SiteImportProgressState>(IDLE)
  const abort = useRef<AbortController | null>(null)
  const latest = useRef({ onResult, pick, fallback })
  useEffect(() => {
    latest.current = { onResult, pick, fallback }
  })

  useEffect(() => () => abort.current?.abort(), [])

  const run = useCallback(
    async (body: TBody, label = '') => {
      abort.current?.abort()
      const ctrl = new AbortController()
      abort.current = ctrl
      setState({ ...IDLE, running: true, url: label, startedAt: Date.now() })
      let result: TResult | undefined
      let failure = null as string | null
      let refused = false

      const apply = (e: ImportStep | ImportPage | ImportFound) =>
        setState((s) => {
          if (e.type === 'step') {
            const i = s.steps.findIndex((x) => x.id === e.id)
            const steps = i === -1 ? [...s.steps, e] : s.steps.map((x, j) => (j === i ? e : x))
            return { ...s, steps }
          }
          if (e.type === 'page') return { ...s, pages: [...s.pages.filter((p) => p.url !== e.url), e] }
          return { ...s, found: [...s.found.filter((f) => f.field !== e.field), e] }
        })

      try {
        await streamEvents<ProgressEvent>(
          path,
          body,
          (e) => {
            if (e.type === 'result') result = latest.current.pick(e)
            else if (e.type === 'error') failure = e.message
            else apply(e)
          },
          { signal: ctrl.signal, tenant },
        )
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return
        // Refused before streaming started (bad address, rate limit, permissions): that answer stands.
        if (e instanceof ApiError && e.status >= 400 && e.status !== 404) {
          failure = e.message
          refused = true
        }
      }
      if (ctrl.signal.aborted) return

      const fb = latest.current.fallback
      if (result === undefined && !failure && !refused && fb) {
        // The stream broke or never started: do it the old way.
        try {
          result = await fb(body)
        } catch (e) {
          failure = (e as Error)?.message || 'That didn’t work. Try again.'
        }
      }
      if (ctrl.signal.aborted) return
      const done = result
      setState((s) => ({ ...s, running: false, finishedAt: Date.now(), error: done !== undefined ? null : failure ?? 'That didn’t work. Try again.' }))
      if (done !== undefined) latest.current.onResult(done)
    },
    [path, tenant],
  )

  const reset = useCallback(() => {
    abort.current?.abort()
    setState(IDLE)
  }, [])

  return { state, run, reset }
}
