import { useState } from 'react'
import { Check, Loader2, Sparkles } from 'lucide-react'
import { Button } from '@receptionist/shared/components/ui/button'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@receptionist/shared/components/ui/dropdown-menu'
import { Input } from '@receptionist/shared/components/ui/input'
import { cn } from '@receptionist/shared/lib/utils'
import { wordDiff } from '@/lib/offer-diff'
import { assistOffer, type AiAssistStatus, type AssistResult, type AssistSection, type Market, type OfferSettings } from '@/lib/sales'
import { applyChoices, choicesFor, currentOpener, openerPatch } from './suggestions'

export interface AssistAction {
  label: string
  section: AssistSection
  action?: 'add' | 'rewrite'
  target?: string
}

/**
 * ✨ next to a section: ask the AI for a suggestion, see it before/after, keep what you like.
 * Nothing is saved until the draft is.
 */
export function AiAssist({ actions, form, market, ai, onApply, className }: {
  actions: AssistAction[]
  form: OfferSettings
  market: Market
  ai?: AiAssistStatus
  onApply: (patch: Partial<OfferSettings>, unconfirmed: string[]) => void
  className?: string
}) {
  const [asking, setAsking] = useState<AssistAction | null>(null)
  const [instruction, setInstruction] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<AssistResult | null>(null)
  const [kept, setKept] = useState<Set<string>>(new Set())
  const [picked, setPicked] = useState(0)

  const off = ai && !ai.enabled
  const choices = result ? choicesFor(asking?.section ?? 'basics', form, result.patch) : []
  const options = result?.options

  const run = (a: AssistAction) => {
    setLoading(true)
    setError('')
    assistOffer({ offer: form, section: a.section, action: a.action ?? 'add', target: a.target, instruction, market })
      .then((r) => {
        setResult(r)
        setPicked(0)
        setKept(new Set(choicesFor(a.section, form, r.patch).map((c) => c.key)))
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }
  const close = () => {
    setAsking(null)
    setResult(null)
    setError('')
    setInstruction('')
  }
  const apply = () => {
    if (!result) return
    if (options) {
      const choice = options[picked]
      if (choice) onApply(openerPatch(form, asking?.target, choice.opener), [])
      close()
      return
    }
    const out = applyChoices(form, result.patch, kept, result.ai_unconfirmed)
    onApply(out.patch, out.unconfirmed)
    close()
  }

  const button = (onClick?: () => void) => (
    <Button type="button" size="xs" variant="ghost" className={cn('text-primary', className)} disabled={off} onClick={onClick}
      title={off ? ai?.reason : 'Ask the AI for a suggestion. You see it before anything changes.'}>
      <Sparkles /> AI
    </Button>
  )
  const trigger = button()

  return (
    <>
      {actions.length === 1 ? (
        button(() => setAsking(actions[0]))
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger render={trigger} />
          <DropdownMenuContent align="end">
            {actions.map((a) => <DropdownMenuItem key={`${a.section}-${a.action}-${a.target}`} onClick={() => setAsking(a)}>{a.label}</DropdownMenuItem>)}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      <Dialog open={!!asking} onOpenChange={(o) => !o && close()}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><Sparkles className="size-4 text-primary" /> {asking?.label}</DialogTitle>
            <DialogDescription>
              The AI reads everything already in this offer (company, website, what you sell…) and writes a suggestion. You choose what to keep; nothing is saved until you save the draft.
            </DialogDescription>
          </DialogHeader>
          {!result ? (
            <div className="space-y-3">
              <Input value={instruction} maxLength={300} placeholder="Optional: what should it focus on? e.g. “front-desk time at busy practices”"
                onChange={(e) => setInstruction(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && asking && !loading && run(asking)} />
              <p className="text-xs text-muted-foreground">
                Optional. Tell it what to focus on or what you sell in a few words, e.g. “AI phone agents for dental practices”. The more the offer already says, the better the suggestion.
              </p>
              {error && <p className="text-sm text-destructive">{error}</p>}
            </div>
          ) : options ? (
            <div className="max-h-[55vh] space-y-2 overflow-y-auto pr-1" role="radiogroup" aria-label="Openers to choose from">
              {result.notes && <p className="text-sm text-muted-foreground">{result.notes}</p>}
              {currentOpener(form, asking?.target) && (
                <p className="text-xs text-muted-foreground">Now: “{currentOpener(form, asking?.target)}”</p>
              )}
              {options.map((o, i) => (
                <button key={o.opener} type="button" role="radio" aria-checked={picked === i} onClick={() => setPicked(i)}
                  className={cn('flex w-full gap-3 rounded-lg border p-3 text-left text-sm', picked === i && 'border-primary/40 bg-primary/5')}>
                  <span className={cn('mt-0.5 size-4 shrink-0 rounded-full border', picked === i && 'border-[5px] border-primary')} />
                  <span className="min-w-0 flex-1 space-y-1">
                    <span className="block font-medium">{o.angle}</span>
                    <span className="block whitespace-pre-wrap leading-relaxed">“{o.opener}”</span>
                  </span>
                </button>
              ))}
              <p className="text-xs text-muted-foreground">Each one already says it’s an AI, names you and offers the opt-out. Pick one, then edit it if you like.</p>
            </div>
          ) : choices.length === 0 ? (
            <p className="text-sm text-muted-foreground">The AI didn’t suggest anything different this time. Try again, or tell it what to focus on.</p>
          ) : (
            <div className="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
              {result.notes && <p className="text-sm text-muted-foreground">{result.notes}</p>}
              {choices.map((c) => (
                <label key={c.key} className={cn('flex cursor-pointer gap-3 rounded-lg border p-3 text-sm', kept.has(c.key) && 'border-primary/40 bg-primary/5')}>
                  <Checkbox className="mt-0.5" checked={kept.has(c.key)}
                    onCheckedChange={(on) => setKept((s) => { const n = new Set(s); if (on) n.add(c.key); else n.delete(c.key); return n })} />
                  <span className="min-w-0 flex-1 space-y-1">
                    <span className="block font-medium">{c.label}</span>
                    <DiffText before={c.before} after={c.after} />
                  </span>
                </label>
              ))}
              {result.ai_unconfirmed.length > 0 && (
                <p className="text-xs text-amber-700 dark:text-amber-400">Facts the AI changed stay marked “AI-written” until you confirm they’re true.</p>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={close}>Cancel</Button>
            {!result ? (
              <Button disabled={loading} onClick={() => asking && run(asking)}>{loading ? <Loader2 className="animate-spin" /> : <Sparkles />} Suggest</Button>
            ) : (
              <>
                <Button variant="outline" disabled={loading} onClick={() => asking && run(asking)}>{loading && <Loader2 className="animate-spin" />} Try again</Button>
                {options
                  ? <Button disabled={!options[picked]} onClick={apply}><Check /> Use this opener</Button>
                  : <Button disabled={kept.size === 0} onClick={apply}><Check /> Apply {kept.size || ''}</Button>}
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** Before → after, with the changed words highlighted. */
export function DiffText({ before, after }: { before: string; after: string }) {
  if (!before) return <span className="block whitespace-pre-wrap text-foreground">{after}</span>
  return (
    <span className="block whitespace-pre-wrap leading-relaxed">
      {wordDiff(before, after).map((p, i) => (
        <span key={i} className={p.op === 'add' ? 'rounded-sm bg-emerald-500/15 text-emerald-800 dark:text-emerald-300' : p.op === 'del' ? 'rounded-sm bg-destructive/10 text-destructive line-through' : ''}>
          {p.text}
        </span>
      ))}
    </span>
  )
}
