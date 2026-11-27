import { useState } from 'react'
import { ArrowRight, Check, ClipboardCheck, Loader2 } from 'lucide-react'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { cn } from '@receptionist/shared/lib/utils'
import { pathLabel } from '@/lib/offer-diff'
import { reviewOffer, type AiAssistStatus, type Market, type OfferReview, type OfferSettings, type ReviewFinding } from '@/lib/sales'
import { DiffText } from './assist'

const SEVERITY_STYLE: Record<ReviewFinding['severity'], string> = {
  high: 'bg-destructive/10 text-destructive',
  medium: 'bg-amber-500/15 text-amber-800 dark:text-amber-300',
  low: 'bg-muted text-muted-foreground',
}
const AREA_LABEL: Record<ReviewFinding['area'], string> = {
  clarity: 'Clarity', spoken: 'Said aloud', claims: 'Claims', compliance: 'Compliance', flow: 'Conversation',
}

function valueAt(form: OfferSettings, path: string): string {
  let cur: unknown = form
  for (const part of path.split('.')) {
    if (Array.isArray(cur)) cur = cur[Number(part)]
    else if (cur && typeof cur === 'object') cur = (cur as Record<string, unknown>)[part]
    else return ''
  }
  return typeof cur === 'string' ? cur : ''
}

/**
 * The AI reviewer (SALES_SUITE_PLAN.md Phase 6): a score and findings with fixes to apply one at a
 * time. Advisory only: publishing is decided by the fixed checks, not by this.
 */
export function ReviewPanel({ id, form, market, ai, saved, savedCurrent, onApplyFix, onGoTo, disabled }: {
  id: string | null
  form: OfferSettings
  market: Market
  ai?: AiAssistStatus
  /** The last review stored with the saved draft. */
  saved?: OfferReview | null
  /** Whether the stored review matches the saved draft (from the API). */
  savedCurrent?: boolean
  onApplyFix: (path: string, value: string, unconfirmed: boolean) => void
  onGoTo: (path: string) => void
  disabled?: boolean
}) {
  const [fresh, setFresh] = useState<{ review: OfferReview; snapshot: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [applied, setApplied] = useState<Set<number>>(new Set())
  const review = fresh?.review ?? saved ?? null
  const outOfDate = fresh ? fresh.snapshot !== JSON.stringify(form) : !!saved && !savedCurrent

  const run = () => {
    setLoading(true)
    setError('')
    const snapshot = JSON.stringify(form)
    reviewOffer(id ?? 'new', { offer: form, market })
      .then((r) => {
        setFresh({ review: r, snapshot })
        setApplied(new Set())
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }
  const off = ai && !ai.enabled

  return (
    <div className="space-y-2 rounded-lg border p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 font-medium"><ClipboardCheck className="size-4" /> AI review</h3>
        <Button size="xs" variant="outline" disabled={loading || off || disabled} onClick={run} title={off ? ai?.reason : undefined}>
          {loading && <Loader2 className="animate-spin" />} {review ? 'Review again' : 'Review'}
        </Button>
      </div>
      {error && <p className="text-destructive">{error}</p>}
      {!review ? (
        <p className="text-xs text-muted-foreground">A demanding sales manager and a careful compliance officer read the offer: unbacked claims, pressure, lines too long to say. Advice only.</p>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <span className={cn('text-2xl font-semibold tabular-nums', review.score >= 80 ? 'text-emerald-600' : review.score >= 60 ? 'text-amber-600' : 'text-destructive')}>{review.score}</span>
            <span className="text-xs text-muted-foreground">/ 100{outOfDate && ' · out of date: you changed the offer since'}</span>
          </div>
          {review.summary && <p className="text-muted-foreground">{review.summary}</p>}
          <ul className="max-h-[40vh] space-y-2 overflow-y-auto">
            {review.findings.map((f, i) => {
              const current = f.fix ? valueAt(form, f.fix.field) : ''
              return (
                <li key={i} className="space-y-1 rounded-md border p-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="secondary" className={cn('border-0', SEVERITY_STYLE[f.severity])}>{f.severity}</Badge>
                    <span className="text-xs text-muted-foreground">{AREA_LABEL[f.area]}{f.field !== 'offer' && ` · ${pathLabel(f.field)}`}</span>
                  </div>
                  <p>{f.issue}</p>
                  {f.fix && !applied.has(i) && <div className="rounded bg-muted/60 p-1.5 text-xs"><DiffText before={current} after={f.fix.value} /></div>}
                  <div className="flex gap-1">
                    {f.field !== 'offer' && <Button size="xs" variant="ghost" onClick={() => onGoTo(f.field)}>Go to <ArrowRight /></Button>}
                    {f.fix && (applied.has(i) ? (
                      <span className="inline-flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400"><Check className="size-3" /> Applied</span>
                    ) : (
                      <Button size="xs" variant="outline" disabled={disabled} onClick={() => {
                        onApplyFix(f.fix!.field, f.fix!.value, f.fix!.unconfirmed)
                        setApplied((s) => new Set(s).add(i))
                      }}>Apply fix</Button>
                    ))}
                  </div>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </div>
  )
}
