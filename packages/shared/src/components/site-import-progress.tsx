import { useEffect, useState } from 'react'
import { AlertTriangle, Check, ChevronRight, FileText, Globe, Loader2, Sparkles, X } from 'lucide-react'
import type { SiteImportProgressState } from '../hooks/use-site-import-stream'
import { cn } from '../lib/utils'

const pathOf = (url: string) => {
  try {
    const u = new URL(url)
    return (u.pathname === '/' ? u.host : u.pathname.replace(/\/$/, '')) || u.host
  } catch {
    return url
  }
}
const seconds = (ms: number) => `${Math.max(1, Math.round(ms / 1000))}s`

/** A clock that ticks while the import runs. */
function useElapsed(state: SiteImportProgressState) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!state.running) return
    const t = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(t)
  }, [state.running])
  if (!state.startedAt) return 0
  return (state.finishedAt ?? now) - state.startedAt
}

/**
 * What the website import is doing, as it does it, like an assistant "thinking": the steps, each page
 * as it is read, then what was found. While running it is open; when it finishes it folds into one
 * line ("Read 6 pages in 23s") that opens again on click.
 */
export function SiteImportProgress({ state, className, labels }: {
  state: SiteImportProgressState
  className?: string
  /** Other jobs that read a site and then do more (e.g. drafting a sales offer) say what they did. */
  labels?: { running?: string; failed?: string; done?: (pagesRead: number, took: string) => string }
}) {
  const elapsed = useElapsed(state)
  const [open, setOpen] = useState(true)
  // Open while working, folded when done (reset while rendering when the run starts or stops).
  const [wasRunning, setWasRunning] = useState(state.running)
  if (wasRunning !== state.running) {
    setWasRunning(state.running)
    setOpen(state.running)
  }

  if (!state.startedAt) return null
  const read = state.pages.filter((p) => p.ok).length
  const words = state.pages.reduce((n, p) => n + (p.ok ? p.words : 0), 0)
  const current = [...state.steps].reverse().find((s) => s.status === 'running')
  const failed = !state.running && !!state.error

  const title = state.running
    ? current?.label ?? labels?.running ?? 'Reading your website…'
    : failed
      ? labels?.failed ?? 'Couldn’t finish reading the website'
      : labels?.done
        ? labels.done(read, seconds(elapsed))
        : `Read ${read} page${read === 1 ? '' : 's'} in ${seconds(elapsed)}`

  return (
    <div className={cn('overflow-hidden rounded-lg border bg-muted/20 text-sm', className)} aria-live="polite">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left hover:bg-muted/40"
        aria-expanded={open}
      >
        {state.running ? (
          <Loader2 className="size-4 shrink-0 animate-spin text-primary" />
        ) : failed ? (
          <AlertTriangle className="size-4 shrink-0 text-amber-600" />
        ) : (
          <Sparkles className="size-4 shrink-0 text-primary" />
        )}
        <span className={cn('min-w-0 flex-1 truncate font-medium', state.running && 'animate-pulse')}>{title}</span>
        <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
          {words > 0 && `${words.toLocaleString()} words · `}
          {seconds(elapsed)}
        </span>
        <ChevronRight className={cn('size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')} />
      </button>

      {open && (
        <div className="space-y-3 border-t px-3 py-3">
          <ol className="relative space-y-2 border-l border-border pl-4">
            {state.steps.map((s) => (
              <li key={s.id} className="relative animate-in fade-in slide-in-from-bottom-1 duration-300">
                <span className="absolute -left-[1.4rem] top-0.5 flex size-4 items-center justify-center rounded-full bg-background">
                  {s.status === 'running' ? (
                    <Loader2 className="size-3.5 animate-spin text-primary" />
                  ) : s.status === 'warn' ? (
                    <AlertTriangle className="size-3.5 text-amber-600" />
                  ) : (
                    <Check className="size-3.5 text-primary" />
                  )}
                </span>
                <span className={cn(s.status === 'running' ? 'text-foreground' : 'text-muted-foreground')}>{s.label}</span>
                {s.detail && <span className="block truncate text-xs text-muted-foreground/80">{s.detail}</span>}
              </li>
            ))}
          </ol>

          {state.pages.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Pages</p>
              <ul className="grid gap-1 sm:grid-cols-2">
                {state.pages.map((p) => (
                  <li key={p.url} className="flex min-w-0 items-center gap-2 rounded-md bg-background/60 px-2 py-1.5 animate-in fade-in duration-300"
                    title={p.url}>
                    {p.ok ? <FileText className="size-3.5 shrink-0 text-muted-foreground" /> : <X className="size-3.5 shrink-0 text-muted-foreground/60" />}
                    <span className={cn('min-w-0 flex-1 truncate', !p.ok && 'text-muted-foreground line-through')}>
                      {p.title || pathOf(p.url)}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {p.ok ? `${p.words.toLocaleString()} words` : 'couldn’t open'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {state.found.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Found</p>
              <ul className="flex flex-wrap gap-1.5">
                {state.found.map((f) => (
                  <li key={f.field}
                    className={cn('inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs animate-in fade-in zoom-in-95 duration-300',
                      f.ok ? 'border-primary/30 bg-primary/5' : 'border-amber-500/40 bg-amber-500/5 text-amber-700 dark:text-amber-400')}>
                    {f.ok ? <Check className="size-3 shrink-0 text-primary" /> : <AlertTriangle className="size-3 shrink-0" />}
                    <span className="font-medium">{f.label}</span>
                    <span className="truncate text-muted-foreground">{f.ok ? f.value : 'not found'}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {state.running && state.steps.length === 0 && (
            <p className="flex items-center gap-2 text-muted-foreground"><Globe className="size-4" /> Connecting to {state.url}…</p>
          )}
          {failed && <p className="text-amber-700 dark:text-amber-400">{state.error}</p>}
        </div>
      )}
    </div>
  )
}
