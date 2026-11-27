/** The offer editor's field editors and side panels (used by form.tsx). */
import { useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Circle, ExternalLink, Loader2, Plus, Trash2 } from 'lucide-react'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { cn } from '@receptionist/shared/lib/utils'
import {
  type Objection, type OfferChannels, type OfferPreview, type OfferRoi,
  type ScriptVariant,
} from '@/lib/sales'
import { roiExample, spokenUrl } from './roi'
import { completeness, fieldId } from './readiness'

export function Banner({ tone, children }: { tone: 'danger' | 'info'; children: ReactNode }) {
  return (
    <div className={cn('flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm',
      tone === 'danger' ? 'border-destructive/40 bg-destructive/5 text-destructive' : 'border-primary/30 bg-primary/5')}>
      {tone === 'danger' && <AlertTriangle className="size-4 shrink-0" />}
      {children}
    </div>
  )
}

/** A labelled field with an "AI" chip while it holds AI text, and room for a ✨ action. */
export function F({ label, hint, className, children, ai, action, path }: {
  label: string; hint?: string; className?: string; children: ReactNode; ai?: boolean; action?: ReactNode; path: string
}) {
  return (
    <div className={cn('space-y-1.5', className)} data-field={path}>
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium" htmlFor={fieldId(path)}>{label}</label>
        {ai && <span className="rounded bg-primary/10 px-1.5 text-[10px] font-semibold uppercase tracking-wide text-primary" title="Written by AI. Read it before you save.">AI</span>}
        <span className="flex-1" />
        {action}
      </div>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** An AI-written fact must be confirmed before the offer can be published. */
export function FactConfirm({ unconfirmed, source, onConfirm }: { unconfirmed: boolean; source?: string; onConfirm: (ok: boolean) => void }) {
  if (!unconfirmed && !source) return null
  return (
    <div className={cn('flex flex-wrap items-center gap-2 text-xs', unconfirmed ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground')}>
      {unconfirmed && (
        <label className="flex cursor-pointer items-center gap-1.5">
          <Checkbox checked={false} onCheckedChange={(v) => v && onConfirm(true)} />
          AI-written: tick to confirm it's true
        </label>
      )}
      {source && <a className="inline-flex items-center gap-1 underline" href={source} target="_blank" rel="noopener noreferrer">source <ExternalLink className="size-3" /></a>}
    </div>
  )
}

export function PreviewPanel({ preview, error, loading, variants, variantId, onVariant, onGoTo, action }: {
  preview?: OfferPreview; error?: string; loading: boolean; variants: ScriptVariant[]; variantId?: string
  onVariant: (id: string) => void; onGoTo: (path: string) => void
  /** ✨ AI suggestions for the opener of the script shown */
  action?: ReactNode
}) {
  const problems = (preview?.issues ?? []).filter((i) => i.level === 'problem')
  const warnings = (preview?.issues ?? []).filter((i) => i.level === 'warning')
  return (
    <>
      <div className="space-y-2 rounded-lg border p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium">Opening line</h3>
          <span className="flex items-center gap-1">
            {loading && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
            {action}
          </span>
        </div>
        {variants.length > 1 && (
          <SimpleSelect size="sm" className="w-full" value={variantId ?? ''} onChange={onVariant}
            options={variants.map((v) => ({ value: v.id, label: v.label || v.id }))} />
        )}
        <p className="rounded-md bg-muted p-2 text-sm">“{preview?.opener || '…'}”</p>
      </div>
      {error ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>
      ) : problems.length > 0 ? (
        <div className="space-y-1 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <div className="flex items-center gap-1.5 font-medium text-destructive"><AlertTriangle className="size-4" /> Fix before publishing</div>
          <ul className="list-disc pl-4 text-destructive">
            {problems.map((p) => <li key={p.message}><button type="button" className="text-left hover:underline" onClick={() => onGoTo(p.field)}>{p.message}</button></li>)}
          </ul>
        </div>
      ) : preview && (
        <div className="flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 className="size-4" /> Ready to publish
        </div>
      )}
      {warnings.length > 0 && (
        <div className="space-y-1 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <div className="font-medium">Worth a look</div>
          <ul className="list-disc pl-4 text-muted-foreground">
            {warnings.map((w) => <li key={w.message}><button type="button" className="text-left hover:underline" onClick={() => onGoTo(w.field)}>{w.message}</button></li>)}
          </ul>
        </div>
      )}
    </>
  )
}

export function Checklist({ checks, onGoTo }: { checks: ReturnType<typeof completeness>['checks']; onGoTo: (path: string) => void }) {
  const open = checks.filter((c) => !c.done)
  if (open.length === 0) return null
  return (
    <div className="space-y-1 rounded-lg border p-3 text-sm">
      <h3 className="font-medium">To make it better</h3>
      <ul className="space-y-0.5">
        {open.map((c) => (
          <li key={c.id}>
            <button type="button" className="flex items-center gap-1.5 text-left text-muted-foreground hover:text-foreground" onClick={() => onGoTo(c.field)}>
              <Circle className={cn('size-3', c.required && 'text-destructive')} /> {c.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ListEditor({ label, hint, items, onChange, max = 6, path, ai, action, fact }: {
  label: string; hint?: string; items: string[]; onChange: (v: string[], removed?: number) => void; max?: number; path: string
  ai?: boolean; action?: ReactNode; fact?: (i: number) => { unconfirmed: boolean; source?: string; onConfirm: (ok: boolean) => void }
}) {
  return (
    <F label={label} hint={hint} path={path} ai={ai} action={action}>
      <div className="space-y-1.5" id={fieldId(path)} tabIndex={-1}>
        {items.map((item, i) => (
          <div key={i} className="space-y-1">
            <div className="flex gap-1.5">
              <Input id={fieldId(`${path}.${i}`)} value={item} maxLength={240} onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))} />
              <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i), i)}><Trash2 /></Button>
            </div>
            {fact && <FactConfirm {...fact(i)} />}
          </div>
        ))}
        {items.length < max && <Button type="button" size="sm" variant="outline" onClick={() => onChange([...items, ''])}><Plus /> Add</Button>}
      </div>
    </F>
  )
}

export function ObjectionsEditor({ items, onChange, ai, action }: { items: Objection[]; onChange: (v: Objection[]) => void; ai?: boolean; action?: ReactNode }) {
  const edit = (i: number, patch: Partial<Objection>) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  return (
    <F label="Objections and answers" path="objections" ai={ai} action={action} hint="“Is this a robot?”, “Send me an email” and “Call me later” are always handled.">
      <div className="space-y-2" id={fieldId('objections')} tabIndex={-1}>
        {items.map((o, i) => (
          <div key={i} className="grid gap-1.5 rounded-md border p-2 sm:grid-cols-[12rem_1fr_auto]">
            <Input id={fieldId(`objections.${i}.objection`)} value={o.objection} placeholder="We already have a developer" maxLength={120} onChange={(e) => edit(i, { objection: e.target.value })} />
            <Textarea id={fieldId(`objections.${i}.response`)} rows={2} value={o.response} placeholder="How the agent answers, in a sentence or two" maxLength={400} onChange={(e) => edit(i, { response: e.target.value })} />
            <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 /></Button>
          </div>
        ))}
        {items.length < 12 && <Button type="button" size="sm" variant="outline" onClick={() => onChange([...items, { objection: '', response: '' }])}><Plus /> Add objection</Button>}
      </div>
    </F>
  )
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 32) || 'script'

export function VariantsEditor({ items, onChange, ai, action }: { items: ScriptVariant[]; onChange: (v: ScriptVariant[]) => void; ai?: boolean; action: (v: ScriptVariant) => ReactNode }) {
  const edit = (i: number, patch: Partial<ScriptVariant>) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const add = () => {
    const base = items[0]
    let id = `script_${items.length + 1}`
    while (items.some((v) => v.id === id)) id = `${id}_b`
    onChange([...items, { ...base, id, label: `Script ${items.length + 1}` }])
  }
  return (
    <div className="space-y-3" id={fieldId('variants')} tabIndex={-1}>
      {items.map((v, i) => (
        <div key={v.id} className="space-y-2 rounded-lg border p-3">
          <div className="flex items-end gap-2">
            <F label="Script name" path={`variants.${i}.label`} className="flex-1" ai={ai} action={action(v)}>
              <Input id={fieldId(`variants.${i}.label`)} value={v.label} onChange={(e) => edit(i, { label: e.target.value })}
                onBlur={() => !v.id && edit(i, { id: slug(v.label) })} />
            </F>
            <span className="pb-2 font-mono text-xs text-muted-foreground">{v.id}</span>
            {items.length > 1 && <Button type="button" size="icon" variant="ghost" aria-label="Remove script" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 /></Button>}
          </div>
          <F label="AI opener (spoken word for word)" path={`variants.${i}.cold_opener`}><Textarea id={fieldId(`variants.${i}.cold_opener`)} rows={3} maxLength={360} value={v.cold_opener} onChange={(e) => edit(i, { cold_opener: e.target.value })} /></F>
          <F label="First question" path={`variants.${i}.first_question`} hint="Asked right after the opener, if they’re happy to talk. Keep it easy to answer."><Input id={fieldId(`variants.${i}.first_question`)} maxLength={200} value={v.first_question} onChange={(e) => edit(i, { first_question: e.target.value })} /></F>
          <F label="Pitch" path={`variants.${i}.pitch`} hint="One or two sentences; the agent tailors it to what they said."><Textarea id={fieldId(`variants.${i}.pitch`)} rows={2} maxLength={400} value={v.pitch} onChange={(e) => edit(i, { pitch: e.target.value })} /></F>
          <F label="Opener for a person (human-assisted calls)" path={`variants.${i}.human_opener`} hint="Shown to you on the power dialer."><Textarea id={fieldId(`variants.${i}.human_opener`)} rows={2} maxLength={360} value={v.human_opener} onChange={(e) => edit(i, { human_opener: e.target.value })} /></F>
        </div>
      ))}
      {items.length < 4 && <Button type="button" size="sm" variant="outline" onClick={add}><Plus /> Add a script to A/B test</Button>}
    </div>
  )
}

export function FollowUpsEditor({ items, onChange }: { items: { after_days: number; subject: string; body: string }[]; onChange: (v: { after_days: number; subject: string; body: string }[]) => void }) {
  const edit = (i: number, patch: Partial<{ after_days: number; subject: string; body: string }>) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  return (
    <div className="space-y-2 rounded-lg border p-3" id={fieldId('follow_ups')} tabIndex={-1}>
      <h3 className="text-sm font-medium">Follow-up emails</h3>
      <p className="text-xs text-muted-foreground">
        Sent only to people who agreed to email (they asked for the information on a call, or booked a meeting) and stopped when they reply, unsubscribe, book, or the deal closes. Never to people who didn't answer.
      </p>
      {items.map((f, i) => (
        <div key={i} className="space-y-1.5 rounded-md border p-2">
          <div className="flex items-end gap-2">
            <F label="Days after" path={`follow_ups.${i}.after_days`} className="w-24">
              <Input type="number" min={1} max={30} value={f.after_days} onChange={(e) => edit(i, { after_days: Number(e.target.value) || 1 })} />
            </F>
            <F label="Subject" path={`follow_ups.${i}.subject`} className="flex-1">
              <Input id={fieldId(`follow_ups.${i}.subject`)} value={f.subject} maxLength={150} onChange={(e) => edit(i, { subject: e.target.value })} />
            </F>
            <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 /></Button>
          </div>
          <Textarea id={fieldId(`follow_ups.${i}.body`)} rows={4} value={f.body} maxLength={4000} onChange={(e) => edit(i, { body: e.target.value })} />
        </div>
      ))}
      {items.length < 3 && (
        <Button type="button" size="sm" variant="outline"
          onClick={() => onChange([...items, { after_days: items.length ? Math.min(30, items[items.length - 1].after_days + 3) : 2, subject: 'Following up from {company}', body: 'Hi {first_name},\n\nJust checking whether you had any questions after our call.' }])}>
          <Plus /> Add a follow-up email
        </Button>
      )}
    </div>
  )
}

export function EmailEditor({ title, value, onChange, path, ai }: { title: string; value: { subject: string; body: string }; onChange: (v: { subject: string; body: string }) => void; path: string; ai?: boolean }) {
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <h3 className="flex items-center gap-2 text-sm font-medium">{title}{ai && <span className="rounded bg-primary/10 px-1.5 text-[10px] font-semibold uppercase text-primary">AI</span>}</h3>
      <F label="Subject" path={`${path}.subject`}><Input id={fieldId(`${path}.subject`)} value={value.subject} maxLength={150} onChange={(e) => onChange({ ...value, subject: e.target.value })} /></F>
      <F label="Body" path={`${path}.body`}><Textarea id={fieldId(`${path}.body`)} rows={6} value={value.body} maxLength={4000} onChange={(e) => onChange({ ...value, body: e.target.value })} /></F>
    </div>
  )
}

const PER_OPTIONS = [{ value: 'week', label: 'per week' }, { value: 'month', label: 'per month' }, { value: 'once', label: 'a plain number' }]

export function RoiEditor({ roi, currency, pricingMode, onChange, fact, onRemoveFactor, action }: {
  roi: OfferRoi; currency: string; pricingMode: 'never' | 'range'; onChange: (r: OfferRoi) => void
  fact: (i: number) => { unconfirmed: boolean; source?: string; onConfirm: (ok: boolean) => void }; onRemoveFactor: (i: number) => void
  /** ✨ AI suggestions for the questions and figures */
  action?: ReactNode
}) {
  const [examples, setExamples] = useState<string[]>([])
  const edit = (patch: Partial<OfferRoi>) => onChange({ ...roi, ...patch })
  const editInput = (i: number, patch: Partial<OfferRoi['inputs'][number]>) => edit({ inputs: roi.inputs.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
  const editFactor = (i: number, patch: Partial<OfferRoi['factors'][number]>) => edit({ factors: roi.factors.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
  const example = roiExample(roi, roi.inputs.map((_, i) => ((examples[i] ?? '') === '' ? NaN : Number(examples[i]))))
  return (
    <div className="space-y-3 rounded-lg border p-3" id={fieldId('roi')} tabIndex={-1} data-field="roi">
      <div className="flex items-center gap-2">
        <h3 className="flex-1 text-sm font-medium">What it’s worth to them (ROI)</h3>
        {action}
        <label className="flex items-center gap-2 text-sm">Use on calls <Switch checked={roi.enabled} onCheckedChange={(v) => edit({ enabled: !!v })} /></label>
      </div>
      <p className="text-xs text-muted-foreground">
        The agent asks for a few of their numbers, a calculator multiplies them by your figures, and the agent says the result as a rough
        estimate, never a promise. The agent never works a number out itself. Example: missed calls a week × value of a new patient ×
        share of calls you’d answer × share that book.
      </p>
      {roi.enabled && (
        <>
          <F label="Questions the agent asks them" path="roi.inputs"
            hint="Asked out loud, one at a time, in this order. Only one can be per week or per month; a weekly answer is turned into a monthly figure. “Try” is an example answer for the preview below; it isn't saved.">
            <div className="space-y-1.5">
              {roi.inputs.map((x, i) => (
                <div key={i} className="grid gap-1.5 rounded-md border p-2 sm:grid-cols-[1fr_11rem_9rem_5rem_auto]">
                  <Input id={fieldId(`roi.inputs.${i}.question`)} value={x.question} maxLength={160} placeholder="Roughly how many calls go unanswered in a week?"
                    onChange={(e) => editInput(i, { question: e.target.value })} />
                  <Input value={x.label} maxLength={40} placeholder="missed calls a week" title="A short name for the answer" onChange={(e) => editInput(i, { label: e.target.value })} />
                  <SimpleSelect className="w-full" value={x.per} onChange={(v) => editInput(i, { per: v as 'week' | 'month' | 'once' })} options={PER_OPTIONS} />
                  <Input type="number" min={0} value={examples[i] ?? ''} placeholder="Try" title="An example answer, for the preview only"
                    onChange={(e) => setExamples((ex) => { const n = [...ex]; n[i] = e.target.value; return n })} />
                  <Button type="button" size="icon" variant="ghost" aria-label="Remove"
                    onClick={() => { edit({ inputs: roi.inputs.filter((_, j) => j !== i) }); setExamples((ex) => ex.filter((_, j) => j !== i)) }}><Trash2 /></Button>
                </div>
              ))}
              {roi.inputs.length < 4 && (
                <Button type="button" size="sm" variant="outline"
                  onClick={() => edit({ inputs: [...roi.inputs, { question: '', label: '', per: roi.inputs.some((y) => y.per !== 'once') ? 'once' : 'week', max: 1_000_000 }] })}>
                  <Plus /> Add a question
                </Button>
              )}
            </div>
          </F>
          <F label="Your figures" path="roi.factors"
            hint="Your own numbers, multiplied in, e.g. “share of those calls we answer: 80 %”. They're claims, like proof points: only use ones you can back up.">
            <div className="space-y-1.5">
              {roi.factors.map((f, i) => (
                <div key={i} className="space-y-1">
                  <div className="grid gap-1.5 sm:grid-cols-[1fr_7rem_6rem_auto]">
                    <Input id={fieldId(`roi.factors.${i}`)} value={f.label} maxLength={60} placeholder="share of those calls we answer" onChange={(e) => editFactor(i, { label: e.target.value })} />
                    <Input type="number" min={0} step="any" value={f.value} onChange={(e) => editFactor(i, { value: Number(e.target.value) })} />
                    <SimpleSelect className="w-full" value={f.percent ? 'pct' : 'num'} onChange={(v) => editFactor(i, { percent: v === 'pct' })}
                      options={[{ value: 'pct', label: '%' }, { value: 'num', label: 'number' }]} />
                    <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => { edit({ factors: roi.factors.filter((_, j) => j !== i) }); onRemoveFactor(i) }}><Trash2 /></Button>
                  </div>
                  <FactConfirm {...fact(i)} />
                </div>
              ))}
              {roi.factors.length < 4 && <Button type="button" size="sm" variant="outline" onClick={() => edit({ factors: [...roi.factors, { label: '', value: 50, percent: true }] })}><Plus /> Add a figure</Button>}
            </div>
          </F>
          <div className="grid gap-3 sm:grid-cols-2">
            <F label="What the result is" path="roi.result_label" hint="Said after the amount: “…roughly four thousand dollars of extra revenue a month.”">
              <Input id={fieldId('roi.result_label')} value={roi.result_label} maxLength={60} placeholder="extra revenue a month" onChange={(e) => edit({ result_label: e.target.value })} />
            </F>
            <F label={`What it costs them a month (${currency}, optional)`} path="roi.monthly_cost"
              hint={pricingMode === 'range' ? 'When the estimate is well above it, the agent adds “that’s about N times what it costs”.' : 'Only used when the agent may mention a price (Price, above).'}>
              <Input id={fieldId('roi.monthly_cost')} type="number" min={0} value={roi.monthly_cost ?? ''}
                onChange={(e) => edit({ monthly_cost: e.target.value === '' ? null : Number(e.target.value) })} />
            </F>
          </div>
          <p className={cn('rounded-md p-2', example != null ? 'bg-muted text-sm' : 'text-xs text-muted-foreground')}>
            {example != null
              ? <>The agent would say: “Using your numbers, that’s roughly {example.toLocaleString()} {currency} of {roi.result_label || 'value'}. It’s only an estimate, not a promise.” (spoken in words)</>
              : 'Type an example answer next to each question (“Try”) to preview what the agent would say.'}
          </p>
        </>
      )}
    </div>
  )
}

export function ChannelsEditor({ channels, website, onChange }: { channels: OfferChannels; website: string; onChange: (c: OfferChannels) => void }) {
  const edit = (patch: Partial<OfferChannels>) => onChange({ ...channels, ...patch })
  const page = channels.booking_url || website
  return (
    <div className="space-y-3 rounded-lg border p-3" id={fieldId('channels')} tabIndex={-1} data-field="channels">
      <h3 className="text-sm font-medium">If they won’t book on the call</h3>
      <p className="text-xs text-muted-foreground">
        The agent first asks to send the details by email{channels.whatsapp ? ' or WhatsApp' : ''}. If they don’t want anything sent, it tells
        them where they can book a time themselves. It never offers SMS.
      </p>
      <F label="Page they can book on themselves" path="channels.booking_url"
        hint={`Said out loud as the last option${page ? `: “${spokenUrl(page)}”` : ''}. Leave empty to use your website${website ? ` (${website})` : ''}. Pick a short address that opens a page where they can choose a time.`}>
        <Input id={fieldId('channels.booking_url')} value={channels.booking_url} maxLength={200} placeholder={website || 'weblaud.com/book'}
          onChange={(e) => edit({ booking_url: e.target.value.trim() })} />
      </F>
      <label className="flex items-center gap-2 text-sm">
        <Switch checked={channels.whatsapp} onCheckedChange={(v) => edit({ whatsapp: !!v })} /> Offer to send the details on WhatsApp
      </label>
      <p className="text-xs text-muted-foreground">
        The agent asks whether the number it called is on WhatsApp, or takes another one and reads it back. Their OK is saved on the lead,
        your approved WhatsApp template is sent with the booking page, and a follow-up task is created for your team. Nothing is sent during test calls.
      </p>
      {channels.whatsapp && (
        <div className="grid gap-3 sm:grid-cols-2">
          <F label="WhatsApp template name" path="channels.whatsapp_template"
            hint="Create it in Meta's WhatsApp Manager and wait for approval. Its text can use {{1}} their first name, {{2}} your company and {{3}} the booking page.">
            <Input id={fieldId('channels.whatsapp_template')} value={channels.whatsapp_template} maxLength={80} placeholder="weblaud_meeting_link"
              onChange={(e) => edit({ whatsapp_template: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') })} />
          </F>
          <F label="Template language" path="channels.whatsapp_language" hint="The language code the template was approved in, e.g. en, en_AU or en_GB.">
            <Input id={fieldId('channels.whatsapp_language')} value={channels.whatsapp_language} maxLength={10} placeholder="en_AU"
              onChange={(e) => edit({ whatsapp_language: e.target.value.trim() })} />
          </F>
        </div>
      )}
    </div>
  )
}
