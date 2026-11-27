import { useState, type ReactNode } from 'react'
import { Copy, FilePlus2, Loader2, Sparkles } from 'lucide-react'
import { Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { SiteImportProgress } from '@receptionist/shared/components/site-import-progress'
import { Button } from '@receptionist/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { useProgressStream } from '@receptionist/shared/hooks/use-progress-stream'
import { cn } from '@receptionist/shared/lib/utils'
import {
  BRAND_LABEL, GENERATE_STREAM_PATH, GOAL_LABEL, generateOffer,
  type AiAssistStatus, type GeneratedOffer, type GenerateOfferInput, type Market, type OfferSettings, type OfferSummary,
} from '@/lib/sales'

const MARKETS: { value: Market; label: string }[] = [
  { value: 'AU', label: 'Australia' }, { value: 'GB', label: 'United Kingdom' }, { value: 'US', label: 'United States' }, { value: 'CA', label: 'Canada' },
]

function Option({ icon: Icon, title, body, onClick, disabled, recommended, children }: {
  icon: typeof Sparkles; title: string; body: string; onClick?: () => void; disabled?: boolean; recommended?: boolean; children?: ReactNode
}) {
  return (
    <div className={cn('rounded-lg border p-3', recommended && 'border-primary/40 bg-primary/5', disabled && 'opacity-60')}>
      <button type="button" disabled={disabled} onClick={onClick} className="flex w-full items-start gap-3 text-left disabled:cursor-not-allowed">
        <Icon className="mt-0.5 size-5 shrink-0 text-primary" />
        <span className="flex-1">
          <span className="block font-medium">{title}{recommended && <span className="ml-2 rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">Recommended</span>}</span>
          <span className="block text-sm text-muted-foreground">{body}</span>
        </span>
      </button>
      {children}
    </div>
  )
}

/** "New offer": draft it with AI (recommended), copy one, or start blank. */
export function NewOfferChooser({ open, onClose, offers, ai, onBlank, onCopy, onAi }: {
  open: boolean
  onClose: () => void
  offers: OfferSummary[]
  ai?: AiAssistStatus
  onBlank: () => void
  onCopy: (id: string) => void
  onAi: () => void
}) {
  const [copyFrom, setCopyFrom] = useState('')
  const aiOff = ai && !ai.enabled
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New offer</DialogTitle>
          <DialogDescription>An offer is everything a campaign says: what you sell, the questions, objection answers, scripts and emails.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Option icon={Sparkles} recommended title="Draft it with AI" disabled={aiOff}
            body={aiOff ? ai?.reason ?? 'The AI copilot is off.' : 'Give a website and a one-line brief. The AI fills every tab; you review it before anything is saved.'}
            onClick={onAi} />
          <Option icon={Copy} title="Copy an existing offer" body="Start from one you already have, then change what's different."
            disabled={offers.length === 0}>
            {offers.length > 0 && (
              <div className="mt-2 flex gap-2 pl-8">
                <SimpleSelect size="sm" className="flex-1" value={copyFrom} onChange={setCopyFrom}
                  options={[{ value: '', label: 'Pick an offer…' }, ...offers.map((o) => ({ value: o.id, label: o.name }))]} />
                <Button size="sm" variant="outline" disabled={!copyFrom} onClick={() => onCopy(copyFrom)}>Copy</Button>
              </div>
            )}
          </Option>
          <Option icon={FilePlus2} title="Blank" body="An empty offer with a safe opener to edit." onClick={onBlank} />
        </div>
      </DialogContent>
    </Dialog>
  )
}

/**
 * Draft a whole offer from a website and a brief (SALES_SUITE_PLAN.md Phase 3). The website is read
 * with live progress, then the AI writes each part. Nothing is saved: the editor opens with the draft.
 */
export function AiDraftDialog({ open, onClose, market, onMarket, onDrafted }: {
  open: boolean
  onClose: () => void
  market: Market
  onMarket: (m: Market) => void
  onDrafted: (result: GeneratedOffer) => void
}) {
  const [input, setInput] = useState<GenerateOfferInput>({
    brief: '', website: '', company_name: '', persona_name: '', brand: 'weblaud', call_goal: 'book_meeting', pricing_hint: '',
  })
  const set = (p: Partial<GenerateOfferInput>) => setInput({ ...input, ...p })
  const stream = useProgressStream<GenerateOfferInput, GeneratedOffer>({
    path: GENERATE_STREAM_PATH,
    tenant: null,
    fallback: (body) => generateOffer(body),
    pick: (e) => e.draft_result as GeneratedOffer | undefined,
    onResult: (r) => {
      onDrafted(r)
      stream.reset()
    },
  })
  const running = stream.state.running
  const go = () => void stream.run({ ...input, market }, input.website || 'your brief')

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !running && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Sparkles className="size-4 text-primary" /> Draft an offer with AI</DialogTitle>
          <DialogDescription>
            It reads your website for what you sell and real proof points, then writes every part of the offer. Facts it writes are marked for you to confirm, and nothing is saved until you click Save.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="What do you want to sell, and to whom?" className="sm:col-span-2" hint="One or two sentences. The more specific, the better the scripts.">
            <Textarea rows={3} autoFocus maxLength={1000} disabled={running} value={input.brief}
              placeholder="AI receptionists for dental groups in Sydney and Melbourne with three or more practices. Book a 15-minute demo."
              onChange={(e) => set({ brief: e.target.value })} />
          </Field>
          <Field label="Your website" hint="Optional, but it's the only source of proof points.">
            <Input value={input.website} disabled={running} placeholder="weblaud.com" onChange={(e) => set({ website: e.target.value })} />
          </Field>
          <Field label="Company the agent calls for" hint="Leave empty to take it from the site.">
            <Input value={input.company_name} disabled={running} maxLength={60} onChange={(e) => set({ company_name: e.target.value })} />
          </Field>
          <Field label="Market">
            <SimpleSelect className="w-full" value={market} onChange={(v) => onMarket(v as Market)} options={MARKETS} />
          </Field>
          <Field label="Brand">
            <SimpleSelect className="w-full" value={input.brand ?? 'other'} onChange={(v) => set({ brand: v as OfferSettings['brand'] })}
              options={(Object.keys(BRAND_LABEL) as OfferSettings['brand'][]).map((k) => ({ value: k, label: BRAND_LABEL[k] }))} />
          </Field>
          <Field label="Goal of the call">
            <SimpleSelect className="w-full" value={input.call_goal ?? 'book_meeting'} onChange={(v) => set({ call_goal: v as OfferSettings['call_goal'] })}
              options={(Object.keys(GOAL_LABEL) as OfferSettings['call_goal'][]).map((k) => ({ value: k, label: GOAL_LABEL[k] }))} />
          </Field>
          <Field label="Agent's name" hint="Optional.">
            <Input value={input.persona_name} disabled={running} maxLength={30} placeholder="Max" onChange={(e) => set({ persona_name: e.target.value })} />
          </Field>
          <Field label="Price policy" className="sm:col-span-2" hint="Optional. Without one, the agent never mentions a price.">
            <Input value={input.pricing_hint} disabled={running} maxLength={300} placeholder="Never say a price / “Plans start at $199 a month per practice”"
              onChange={(e) => set({ pricing_hint: e.target.value })} />
          </Field>
        </div>
        <SiteImportProgress state={stream.state}
          labels={{ running: 'Getting started…', failed: 'Couldn’t draft the offer', done: (pages, took) => (pages ? `Read ${pages} page${pages === 1 ? '' : 's'} and drafted the offer in ${took}` : `Drafted the offer in ${took}`) }} />
        <DialogFooter>
          <Button variant="outline" disabled={running} onClick={onClose}>Cancel</Button>
          <Button disabled={running || input.brief.trim().length < 10} onClick={go}>
            {running ? <Loader2 className="animate-spin" /> : <Sparkles />} {running ? 'Drafting…' : 'Draft the offer'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
