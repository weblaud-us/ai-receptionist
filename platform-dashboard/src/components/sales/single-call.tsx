/**
 * Single calls (SALES_SINGLE_CALL_PLAN.md): call one business now, without a campaign. The AI talks.
 *
 *   1. Business   pick a lead, or type in a new business (website and/or name and phone, any country;
 *                 "they don't have a website" when they have none)
 *   2. Preparing  the AI reads its website, fills in empty details and suggests what to sell
 *   3. Review     everything the call will do; "Approve & call" is the sign-off
 *   4. Live       listen, take over or end it; then the outcome, and a note
 *
 * The server decides who the AI may call and re-checks everything when you approve: this screen only
 * shows what it worked out. A sales manager may override a failed check that is one of our own rules
 * (the test calls, business hours where no law sets them, the gaps between calls): ticking Override
 * works the review out again with it, and the call then needs a reason. A law's checks have no override.
 */
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, ArrowLeft, CheckCircle2, Globe, Loader2, Mic, PhoneCall, PhoneOff, Search, ShieldAlert, Sparkles, XCircle } from 'lucide-react'
import { ConfirmDialog, Field, KeyValue } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Input } from '@receptionist/shared/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { dateTime, titleCase } from '@receptionist/shared/lib/format'
import { CallAudio } from '@/components/sales/call-audio'
import { CallTimer, Transcript } from '@/components/sales/call-panel'
import { ALL_COUNTRIES, isCountry } from '@/lib/countries'
import { MODE_LABEL, WRAP_OUTCOMES, endedText, useAttempt, useFollowUpMutations, useSalesLeads, useSalesMutations, type JoinInfo, type SalesLead } from '@/lib/sales'
import { FIELD_LABEL, refusal, useSingleCall, type CallCheck, type CallPrep, type Country, type NewBusiness, type Override, type PlacedCall, type SingleCallReview } from '@/lib/single-call'
import { useTranscript } from '@/lib/transcript'

const LAST_COUNTRY = 'single-call.country'
function lastCountry(): Country {
  try {
    const saved = localStorage.getItem(LAST_COUNTRY) ?? ''
    return isCountry(saved) ? saved : 'AU'
  } catch {
    return 'AU'
  }
}
function rememberCountry(code: Country) {
  try {
    localStorage.setItem(LAST_COUNTRY, code)
  } catch { /* private mode: the default is fine */ }
}
/** A page on one of these is not a website of their own (backend: research.SOCIAL_SITES). */
const SOCIAL = /^(https?:\/\/)?([^/]*\.)?(facebook\.com|fb\.com|fb\.me|instagram\.com|instagr\.am|tiktok\.com|linkedin\.com|x\.com|twitter\.com|youtube\.com|linktr\.ee|wa\.me|whatsapp\.com|t\.me|g\.page|maps\.app\.goo\.gl|yelp\.com)(\/|$)/i
const isSocial = (url?: string) => SOCIAL.test((url ?? '').trim())
const OUTCOME_TEXT: Record<string, string> = {
  no_answer: 'No answer', busy: 'Busy', failed: 'Call failed', voicemail: 'Voicemail', machine: 'Machine / phone menu',
  uncertain: 'Unclear who answered', no_conversation: 'No conversation', gatekeeper: 'Reached a gatekeeper',
  not_interested: 'Not interested (this offer)', callback: 'Call back later', interested: 'Interested',
  demo_booked: 'Meeting booked', dnc: 'Asked not to be called', wrong_number: 'Wrong number',
  blocked_by_gate: 'Stopped by the compliance check', dispatch_failed: 'Could not start the call',
}

type Step =
  | { kind: 'business'; found?: { website: string; name: string; error: string } }
  | { kind: 'preparing' }
  | { kind: 'refused'; prep: CallPrep }
  | { kind: 'review'; prep: CallPrep; review: SingleCallReview }
  | { kind: 'live'; placed: PlacedCall; title: string; leadId: string }

/** Opened from the Dialer page's URL: `?call=new`, or `?call=<lead id>` to start at Preparing. */
export function SingleCallSheet({ call, onClose }: { call: string | null; onClose: () => void }) {
  return (
    <Sheet open={call !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Call one business</SheetTitle>
          <SheetDescription>The AI reads the business, suggests what to sell and makes the call, once you've checked it.</SheetDescription>
        </SheetHeader>
        {call !== null && <Flow key={call} start={call} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  )
}

function Flow({ start, onClose }: { start: string; onClose: () => void }) {
  const s = useSingleCall()
  const [step, setStep] = useState<Step>(start === 'new' ? { kind: 'business' } : { kind: 'preparing' })

  const prepare = (body: { lead_id: string } | { business: NewBusiness }) =>
    s.prepare.mutate(body, {
      onSuccess: (prep) => {
        if (prep.needs_phone) {
          setStep({ kind: 'business', found: { website: prep.found?.website ?? '', name: prep.found?.business_name ?? '', error: prep.site_error ?? '' } })
        } else if (!prep.callable || !prep.review) setStep({ kind: 'refused', prep })
        else setStep({ kind: 'review', prep, review: prep.review })
      },
      onError: () => setStep('business' in body ? { kind: 'business' } : { kind: 'refused', prep: {} }),
    })

  // A lead picked elsewhere ("Call now"): get it ready straight away, once.
  const prepareRef = useRef(prepare)
  useEffect(() => {
    prepareRef.current = prepare
  })
  const started = useRef(false)
  useEffect(() => {
    if (start === 'new' || started.current) return
    started.current = true
    prepareRef.current({ lead_id: start })
  }, [start])

  if (step.kind === 'business') {
    return <BusinessStep found={step.found} pending={s.prepare.isPending}
      onLead={(id) => { setStep({ kind: 'preparing' }); prepare({ lead_id: id }) }}
      onBusiness={(b) => { setStep({ kind: 'preparing' }); prepare({ business: b }) }} />
  }
  if (step.kind === 'preparing') {
    return (
      <div className="flex flex-col items-center gap-3 px-4 py-16 text-center text-sm" role="status" aria-live="polite">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
        <div className="font-medium">Getting the call ready…</div>
        <p className="max-w-sm text-muted-foreground">Reading their website, choosing what to sell and checking the rules. This can take up to a minute.</p>
      </div>
    )
  }
  if (step.kind === 'refused') {
    const lead = step.prep.lead
    return (
      <div className="space-y-4 px-4 pb-6 text-sm">
        {lead && <div><div className="font-medium">{lead.business_name || lead.phone}</div><div className="font-mono text-xs text-muted-foreground">{lead.phone}</div></div>}
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" />
          <p>{step.prep.reason || 'This business could not be got ready for a call.'}</p>
        </div>
        <div className="flex gap-2">
          {start === 'new' && <Button variant="outline" onClick={() => setStep({ kind: 'business' })}><ArrowLeft /> Pick another business</Button>}
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </div>
      </div>
    )
  }
  if (step.kind === 'review') {
    const leadId = step.review.lead_id
    return <ReviewStep prep={step.prep} review={step.review} onCancel={onClose}
      onReview={(review) => setStep({ ...step, review })}
      onPrepareAgain={() => { setStep({ kind: 'preparing' }); prepare({ lead_id: leadId }) }}
      onPlaced={(placed, name) => setStep({ kind: 'live', placed, leadId: step.review.lead_id, title: `Calling ${name}` })} />
  }
  return <LiveStep placed={step.placed} title={step.title} leadId={step.leadId} onClose={onClose} />
}

// ── 1. Business ──────────────────────────────────────────────────────────────

function BusinessStep({ found, pending, onLead, onBusiness }: {
  found?: { website: string; name: string; error: string }
  pending: boolean
  onLead: (id: string) => void
  onBusiness: (b: NewBusiness) => void
}) {
  const [q, setQ] = useState('')
  const search = useDebounced(q.trim(), 300)
  const leads = useSalesLeads({ q: search || undefined, sort: 'updated', page: 1 })
  const [b, setB] = useState<NewBusiness>({ website: found?.website ?? '', no_website: false, business_name: found?.name ?? '',
    phone: '', country: lastCountry(), contact_name: '' })
  const set = (patch: Partial<NewBusiness>) => setB((x) => ({ ...x, ...patch }))
  const ready = b.no_website ? !!b.phone.trim() : !!(b.website.trim() || b.phone.trim()) && (!found || !!b.phone.trim())
  const callable = (l: SalesLead) => !['dnc', 'bad_number', 'in_progress', 'merged'].includes(l.status)

  return (
    <div className="space-y-6 px-4 pb-6 text-sm">
      <section className="space-y-2">
        <h3 className="font-medium">A business you already have</h3>
        <div className="relative">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
          <Input className="pl-8" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name or phone number" aria-label="Search your leads" />
        </div>
        <ul className="divide-y rounded-md border" aria-label="Leads">
          {(leads.data?.leads ?? []).slice(0, 8).map((l) => (
            <li key={l.id}>
              <button type="button" disabled={pending || !callable(l)} onClick={() => onLead(l.id)}
                className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted disabled:opacity-50">
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{l.business_name || l.phone}</span>
                  <span className="block truncate font-mono text-xs text-muted-foreground">{l.phone} · {l.country}</span>
                </span>
                <span className="text-xs text-muted-foreground">{titleCase(l.status.replace('_', ' '))}</span>
              </button>
            </li>
          ))}
          {leads.data && leads.data.leads.length === 0 && <li className="px-3 py-2 text-xs text-muted-foreground">No lead matches. Add it below.</li>}
        </ul>
      </section>

      <section className="space-y-3">
        <h3 className="font-medium">Or a new business</h3>
        {found && (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs">
            {found.error ? `Couldn't read that website: ${found.error}. ` : "We couldn't find a phone number on their website. "}Type the number in.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Field label="Website" hint={b.no_website ? 'The AI can offer to build them one.'
              : isSocial(b.website) ? "A social media page: it counts as no website of their own." : 'The AI reads it to fill in the rest.'}>
              <Input value={b.no_website ? '' : b.website} disabled={b.no_website} onChange={(e) => set({ website: e.target.value })}
                placeholder={b.no_website ? 'No website' : 'harbourdental.com.au'} aria-label="Website" />
            </Field>
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={b.no_website} onCheckedChange={(v) => set({ no_website: !!v })} aria-label="They don't have a website" />
              They don't have a website
            </label>
          </div>
          <Field label="Phone number" hint={found && !b.no_website ? 'Needed: their website has none.'
            : b.no_website ? 'Needed when they have no website.' : 'Optional when the website lists it.'}>
            <Input value={b.phone} onChange={(e) => set({ phone: e.target.value })} placeholder="02 9123 4567" autoFocus={!!found} aria-label="Phone number" />
          </Field>
          <Field label="Business name"><Input value={b.business_name} onChange={(e) => set({ business_name: e.target.value })} /></Field>
          <Field label="Country" hint="A number starting with + and the country code is read as that country's.">
            <SimpleSelect className="w-full" value={b.country} onChange={(v) => { set({ country: v }); rememberCountry(v) }} options={ALL_COUNTRIES} />
          </Field>
          <Field label="Contact name (optional)"><Input value={b.contact_name} onChange={(e) => set({ contact_name: e.target.value })} /></Field>
        </div>
        <Button disabled={pending || !ready} onClick={() => onBusiness(b.no_website ? { ...b, website: '' } : b)}>
          {pending ? <Loader2 className="animate-spin" /> : <Sparkles />} Get the call ready
        </Button>
      </section>
    </div>
  )
}

// ── 3. Review ────────────────────────────────────────────────────────────────

type Details = Pick<SalesLead, 'business_name' | 'contact_name' | 'email' | 'website' | 'vertical'> & { agent_notes: string; no_website: boolean }

function detailsOf(lead: SalesLead | undefined): Details {
  return { business_name: lead?.business_name ?? '', contact_name: lead?.contact_name ?? '', email: lead?.email ?? '',
    website: lead?.website ?? '', vertical: lead?.vertical ?? '', agent_notes: lead?.agent_notes ?? '',
    no_website: !!lead?.no_website }
}

function CheckIcon({ status }: { status: CallCheck['status'] }) {
  const cls = 'mt-0.5 size-3.5 shrink-0'
  if (status === 'pass') return <CheckCircle2 className={`${cls} text-emerald-600`} />
  if (status === 'warn') return <AlertTriangle className={`${cls} text-amber-600`} />
  if (status === 'overridden') return <ShieldAlert className={`${cls} text-amber-600`} />
  return <XCircle className={`${cls} text-destructive`} />
}

const labels = (checks: CallCheck[]) => checks.map((c) => c.label).join('; ')

/** Under the button: why it can't be pressed yet, or what pressing it records. Never just greyed out. */
function approveHint({ review, dirty, locked, reasonOk }: { review: SingleCallReview; dirty: boolean; locked: CallCheck[]; reasonOk: boolean }) {
  if (dirty) return 'Save the details first: the call uses what is saved.'
  const n = review.overrides.length
  if (review.can_call && n) {
    return reasonOk ? `You're overriding ${n === 1 ? 'a check' : `${n} checks`}. Your name, your reason and what you overrode are kept with the call.`
      : "Say why you're overriding, then call."
  }
  if (review.can_call) return 'Approving records that you checked this call, as it is shown here.'
  if (locked.length) {
    return `This call can't go ahead now. ${labels(locked)}: ${locked.length === 1 ? "this check can't" : "these checks can't"} be overridden. `
      + 'Only our own rules can be overridden: not the law, what the business asked for, or a missing setting.'
  }
  return review.may_override ? 'Tick Override on the checks you want to skip, or wait until they pass.'
    : 'A sales manager can override these checks. Or wait until they pass.'
}

function ReviewStep({ prep, review, onReview, onPrepareAgain, onPlaced, onCancel }: {
  prep: CallPrep
  review: SingleCallReview
  onReview: (r: SingleCallReview) => void
  /** "No website" changed: what to sell may change too, so the AI suggests again. */
  onPrepareAgain: () => void
  onPlaced: (p: PlacedCall, name: string) => void
  onCancel: () => void
}) {
  const s = useSingleCall()
  const f = useFollowUpMutations()
  const lead = prep.lead
  const [saved, setSaved] = useState<Details>(() => detailsOf(lead))
  const [details, setDetails] = useState<Details>(saved)
  const [problem, setProblem] = useState('')
  const [reason, setReason] = useState('')
  const dirty = JSON.stringify(details) !== JSON.stringify(saved)
  const filled = new Set(prep.ai_filled ?? [])
  const research = lead?.research?.status === 'done' ? lead.research.data : null
  const offer = review.offers.find((o) => o.id === review.offer.id)
  const busy = s.review.isPending || f.patchLead.isPending || s.place.isPending

  const again = (change: { offer_id?: string; variant_id?: string; from_number?: string; overrides?: Override[] }) => {
    const offerId = change.offer_id ?? review.offer.id
    const variants = review.offers.find((o) => o.id === offerId)?.variants ?? []
    const variantId = change.variant_id ?? (variants.some((v) => v.id === review.variant_id) ? review.variant_id : variants[0]?.id ?? '')
    setProblem('')
    s.review.mutate({ lead_id: review.lead_id, offer_id: offerId, variant_id: variantId, from_number: change.from_number ?? review.from_number,
      overrides: change.overrides ?? review.overrides }, { onSuccess: onReview })
  }
  const setOverride = (key: Override, on: boolean) =>
    again({ overrides: on ? [...review.overrides, key] : review.overrides.filter((k) => k !== key) })
  const overriding = review.overrides.length > 0
  const reasonOk = reason.trim().length >= 3
  const locked = review.checks.filter((c) => c.status === 'fail' && !c.override)
  const saveDetails = () =>
    f.patchLead.mutate({ id: review.lead_id, ...details }, {
      onSuccess: () => {
        if (details.no_website !== saved.no_website) onPrepareAgain()
        else { setSaved(details); again({}) }
      },
    })
  const approve = () =>
    s.place.mutate({ ...review, override_reason: overriding ? reason.trim() : '' }, {
      onSuccess: (placed) => onPlaced(placed, saved.business_name || lead?.phone || 'them'),
      onError: (e) => {
        const r = refusal(e)
        setProblem(r.message)
        if (r.review) onReview(r.review)
      },
    })
  const fromSite = (key: keyof Details) => (filled.has(key) ? 'From their website: check it.' : undefined)

  return (
    <div className="space-y-5 px-4 pb-6 text-sm">
      <div className="flex flex-wrap items-baseline gap-2">
        <div className="text-base font-medium">{saved.business_name || lead?.phone}</div>
        <span className="font-mono text-xs text-muted-foreground">{lead?.phone}</span>
        {prep.created && <Badge variant="outline">New lead</Badge>}
      </div>
      {(prep.site_error || prep.research_note) && (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs">{[prep.site_error && `Couldn't read their website: ${prep.site_error}.`, prep.research_note].filter(Boolean).join(' ')}</p>
      )}

      <section className="space-y-3 rounded-md border p-3">
        <h3 className="font-medium">The business</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Business name" hint={fromSite('business_name')}>
            <Input aria-label="Business name" value={details.business_name} onChange={(e) => setDetails({ ...details, business_name: e.target.value })} />
          </Field>
          <Field label="Contact" hint={fromSite('contact_name')}>
            <Input aria-label="Contact" value={details.contact_name} onChange={(e) => setDetails({ ...details, contact_name: e.target.value })} />
          </Field>
          <Field label="Email" hint={fromSite('email')}>
            <Input aria-label="Email" value={details.email} onChange={(e) => setDetails({ ...details, email: e.target.value })} />
          </Field>
          <Field label="Kind of business" hint={fromSite('vertical')}>
            <Input aria-label="Kind of business" value={details.vertical} onChange={(e) => setDetails({ ...details, vertical: e.target.value })} />
          </Field>
          <div className="space-y-1.5 sm:col-span-2">
            <Field label="Website" hint={!details.no_website && isSocial(details.website) ? "A social media page: it counts as no website of their own." : undefined}>
              <Input aria-label="Website" value={details.website} onChange={(e) => setDetails({ ...details, website: e.target.value })} />
            </Field>
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={details.no_website} onCheckedChange={(v) => setDetails({ ...details, no_website: !!v })} aria-label="They don't have a website of their own" />
              They don't have a website of their own
            </label>
          </div>
          <Field label="Notes for the agent" className="sm:col-span-2" hint="What the AI should know about them. It's part of what you approve.">
            <Textarea aria-label="Notes for the agent" rows={2} maxLength={600} value={details.agent_notes} onChange={(e) => setDetails({ ...details, agent_notes: e.target.value })} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {filled.size > 0 && <span>Filled in from their website: {[...filled].map((k) => FIELD_LABEL[k] ?? k).join(', ')}.</span>}
          {dirty && <Button size="sm" variant="outline" disabled={busy} onClick={saveDetails}>{f.patchLead.isPending && <Loader2 className="animate-spin" />} Save details</Button>}
        </div>
      </section>

      {research && (
        <section className="space-y-1 rounded-md border p-3 text-xs">
          <h3 className="flex items-center gap-1.5 text-sm font-medium"><Globe className="size-4" /> What their website says</h3>
          <p className="text-sm">{research.summary}</p>
          {research.likely_pains.length > 0 && <p><span className="font-medium">Likely problems:</span> {research.likely_pains.join('; ')}</p>}
          {research.hook && <p><span className="font-medium">Something to mention:</span> {research.hook}</p>}
        </section>
      )}

      <section className="space-y-3 rounded-md border p-3">
        <h3 className="font-medium">The call</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="What to sell">
            <SimpleSelect className="w-full" value={review.offer.id} disabled={busy} onChange={(v) => again({ offer_id: v })}
              options={review.offers.map((o) => ({ value: o.id, label: `${o.name} · v${o.version}${o.mode ? '' : ' (the AI may not call them for this)'}` }))} />
          </Field>
          <Field label="Script">
            <SimpleSelect className="w-full" value={review.variant_id} disabled={busy} onChange={(v) => again({ variant_id: v })}
              options={(offer?.variants ?? []).map((v) => ({ value: v.id, label: v.label }))} />
          </Field>
        </div>
        {prep.suggestion && prep.suggestion.offer_id === review.offer.id && prep.suggestion.why && (
          <p className="flex items-start gap-1.5 text-xs text-muted-foreground"><Sparkles className="mt-0.5 size-3.5 shrink-0" />
            {prep.suggestion.source === 'ai' ? 'Suggested by the AI: ' : ''}{prep.suggestion.why}</p>
        )}
        <KeyValue label="Call type">{review.mode === 'ai_cold_b2b_us' ? 'AI cold call to a business' : review.mode ? MODE_LABEL[review.mode] : 'Not allowed'}</KeyValue>
        <p className="-mt-1 text-xs text-muted-foreground">{review.mode_reason}</p>
        <Field label="Caller ID">
          {review.caller_ids.length > 0
            ? <SimpleSelect className="w-full" value={review.from_number} disabled={busy} onChange={(v) => again({ from_number: v })}
                options={review.caller_ids.map((n) => ({ value: n, label: n }))} />
            : <p className="text-xs text-destructive">No caller ID for this country yet.</p>}
        </Field>
        {review.opener && (
          <div className="space-y-1">
            <div className="text-xs font-medium">{review.callback ? 'They asked for this call. The AI opens with:' : 'The AI opens with:'}</div>
            <blockquote className="rounded-md bg-muted p-2 text-sm">“{review.opener}”</blockquote>
          </div>
        )}
        {review.local_times.length > 0 && (
          <p className="text-xs text-muted-foreground">Where they are: {review.local_times.map((t) => `${t.text} (${t.tz.split('/').pop()?.replace('_', ' ')})`).join(', ')}</p>
        )}
      </section>

      <section className="space-y-2 rounded-md border p-3">
        <h3 className="font-medium">Checks</h3>
        <ul className="space-y-1" aria-label="Checks">
          {review.checks.map((c) => (
            <li key={c.id} className="flex items-start gap-2 text-xs">
              <CheckIcon status={c.status} />
              <span className="min-w-0 flex-1">
                <span className="font-medium">{c.label}</span>{c.detail && <span className="text-muted-foreground"> · {c.detail}</span>}
                {c.status === 'overridden' && <span className="font-medium text-amber-700 dark:text-amber-500"> · Overridden</span>}
              </span>
              {c.override && (review.may_override
                ? (
                  <label className="flex shrink-0 items-center gap-1.5 font-medium">
                    <Checkbox checked={c.status === 'overridden'} disabled={busy} onCheckedChange={(v) => setOverride(c.override!, !!v)}
                      aria-label={`Override: ${c.label}`} />
                    Override
                  </label>
                )
                : <span className="shrink-0 text-muted-foreground">A sales manager can override this</span>)}
            </li>
          ))}
        </ul>
        {!review.can_call && review.retry_at && <p className="text-xs">You can call them from {dateTime(review.retry_at)}.</p>}
        {overriding && (
          <Field label="Why you're overriding" hint="Kept with the call, with your name and the checks you overrode.">
            <Textarea aria-label="Why you're overriding" rows={2} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. They asked us to call after 6 pm; I tested this script myself this morning" />
          </Field>
        )}
      </section>

      {problem && <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive" role="alert">{problem}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={!review.can_call || dirty || busy || (overriding && !reasonOk)} onClick={approve}>
          {s.place.isPending ? <Loader2 className="animate-spin" /> : overriding ? <ShieldAlert /> : <PhoneCall />} {overriding ? 'Override & call' : 'Approve & call'}
        </Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
        <p className="w-full text-xs text-muted-foreground">{approveHint({ review, dirty, locked, reasonOk })}</p>
      </div>
    </div>
  )
}

// ── 4. Live ──────────────────────────────────────────────────────────────────

function LiveStep({ placed, title, leadId, onClose }: { placed: PlacedCall; title: string; leadId: string; onClose: () => void }) {
  const m = useSalesMutations()
  const [join, setJoin] = useState<JoinInfo>(placed.join)
  const [left, setLeft] = useState(false)
  const [ask, setAsk] = useState<'takeover' | 'end' | null>(null)
  const { segments, onTranscript } = useTranscript()
  const q = useAttempt(placed.attempt_id, 3000)
  const attempt = q.data?.attempt
  const done = attempt?.status === 'done'
  const id = placed.attempt_id

  return (
    <div className="space-y-3 px-4 pb-6 text-sm">
      {!left && (
        <CallAudio key={join.token} join={join} title={title} onLeave={() => setLeft(true)} onTranscript={onTranscript}
          onHangUp={join.mode === 'takeover' ? () => m.hangUp.mutate(id) : undefined} />
      )}
      {!done && (
        <div className="flex flex-wrap gap-2">
          {join.mode === 'listen' && <Button size="sm" variant="outline" disabled={m.join.isPending} onClick={() => setAsk('takeover')}><Mic /> Take over</Button>}
          <Button size="sm" variant="ghost" className="text-destructive" disabled={m.hangUp.isPending} onClick={() => setAsk('end')}><PhoneOff /> End call</Button>
        </div>
      )}
      <CallTimer attemptId={id} running={!done} />
      {attempt?.status === 'dispatched' && <NotStarted since={attempt.created_at} />}
      <Transcript segments={segments} empty="What they and the AI say appears here as they speak." />
      {done && attempt && <AfterCall attemptId={id} leadId={leadId} outcome={attempt.outcome} summary={attempt.summary}
        nextStep={attempt.next_step} detail={attempt.detail} onClose={onClose} />}
      <ConfirmDialog open={!!ask} onOpenChange={(o) => !o && setAsk(null)} destructive={ask === 'end'}
        title={ask === 'end' ? 'End the call?' : 'Take over the call?'}
        description={ask === 'end' ? 'This hangs up on them straight away.' : 'The AI goes quiet and they hear you instead. Allow the microphone when asked.'}
        confirmLabel={ask === 'end' ? 'End the call' : 'Take over'} loading={m.join.isPending || m.hangUp.isPending}
        onConfirm={() => {
          if (ask === 'end') m.hangUp.mutate(id, { onSuccess: (r) => toast.success(endedText(r)) })
          else m.join.mutate({ id, mode: 'takeover' }, { onSuccess: (j) => { setJoin(j); setLeft(false) } })
          setAsk(null)
        }} />
    </div>
  )
}

/** A call the AI hasn't started after a while: the server cancels it at 2 minutes (dialer.SINGLE_PICKUP). */
const SLOW_START_MS = 20_000

function NotStarted({ since }: { since: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  if (now - new Date(since).getTime() < SLOW_START_MS) return null
  return <p className="text-xs text-muted-foreground" role="status">The AI hasn't started this call yet. If it doesn't within 2 minutes, it's cancelled and nothing is dialled.</p>
}

function AfterCall({ attemptId, leadId, outcome, summary, nextStep, detail, onClose }: {
  attemptId: string; leadId: string; outcome: string | null; summary: string | null; nextStep: string | null; detail?: string
  onClose: () => void
}) {
  const m = useSalesMutations()
  const f = useFollowUpMutations()
  const [fix, setFix] = useState('')
  const [callbackAt, setCallbackAt] = useState('')
  const [note, setNote] = useState('')
  const pending = m.wrapUp.isPending || f.addNote.isPending
  const save = () => {
    const ok = () => { toast.success('Saved'); setFix(''); setNote('') }
    if (fix) {
      m.wrapUp.mutate({ id: attemptId, outcome: fix, notes: note.trim(),
        callback_at: fix === 'callback' && callbackAt ? new Date(callbackAt).toISOString() : null }, { onSuccess: ok })
    } else if (note.trim()) f.addNote.mutate({ id: leadId, text: note.trim() }, { onSuccess: ok })
  }
  return (
    <section className="space-y-2 rounded-md border border-primary p-3">
      <h3 className="font-medium">How it went</h3>
      <KeyValue label="Outcome">{OUTCOME_TEXT[outcome ?? ''] ?? titleCase((outcome ?? 'unknown').replaceAll('_', ' '))}</KeyValue>
      {detail && <p className="text-xs text-muted-foreground">{detail}</p>}
      {summary && <p>{summary}</p>}
      {nextStep && <p className="text-xs"><span className="font-medium">Next step:</span> {nextStep}</p>}
      <div className="grid gap-2 pt-2">
        <SimpleSelect className="w-full" size="sm" placeholder="Correct the outcome (optional)" value={fix} onChange={setFix} options={WRAP_OUTCOMES} />
        {fix === 'callback' && <Field label="Call back at (your time)"><Input type="datetime-local" value={callbackAt} onChange={(e) => setCallbackAt(e.target.value)} /></Field>}
        <Textarea rows={2} value={note} placeholder="Add a note for the team (optional)" onChange={(e) => setNote(e.target.value)} />
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={pending || (!fix && !note.trim()) || (fix === 'callback' && !callbackAt)} onClick={save}>
            {pending && <Loader2 className="animate-spin" />} Save
          </Button>
          <Button size="sm" onClick={onClose}>Done</Button>
        </div>
      </div>
    </section>
  )
}
