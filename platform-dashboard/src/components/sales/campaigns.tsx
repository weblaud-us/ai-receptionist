import { useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, Loader2, Pause, Pencil, Play, Plus, Scale } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { titleCase } from '@receptionist/shared/lib/format'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import {
  DEFAULT_GUARDRAILS, MATERIAL_FIELDS, MODE_HELP, MODE_LABEL, useCampaigns, useOffers, useSalesMutations, useSalesStatus,
  type Campaign, type CampaignDraft, type CampaignInput, type CampaignMode, type Guardrails,
} from '@/lib/sales'
import { LaunchDialog, QuickSetupDialog } from '@/components/sales/launch'
import { useCan } from '@/lib/roles'
import { cn } from '@receptionist/shared/lib/utils'

const STATUS_STYLE: Record<string, string> = {
  draft: 'bg-slate-200 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300',
  running: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  paused: 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300',
  completed: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
}
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const BLANK: CampaignInput = {
  name: '', mode: 'ai_cold_b2b_us', persona_ids: ['sales_v1'], from_numbers: [],
  calling_window: { start: '09:30', end: '17:00', days: [1, 2, 3, 4, 5] },
  max_attempts: 3, retry_after_hours: [48, 96], max_concurrent: 2, daily_cap: 150,
  voicemail_policy: 'hang_up', vertical: '', notes: '', offer_id: null, offer_version: null, variant_ids: [],
  guardrails: DEFAULT_GUARDRAILS, order: 'fit', owner_id: '', retry_timing: 'smart', script_split: 'auto',
}
const E164 = /^\+[1-9]\d{7,14}$/
const BUILT_IN = '__builtin__'

/** The legally material settings, normalised like the server's models.material_snapshot. */
const material = (c: Partial<CampaignInput>) =>
  JSON.stringify(MATERIAL_FIELDS.map((k) => {
    const v = c[k as keyof CampaignInput]
    return k === 'from_numbers' ? [...((v as string[]) ?? [])].sort() : Array.isArray(v) ? v : v || null
  }))

export function Campaigns({ quickOffer, onQuickDone }: { quickOffer?: string | null; onQuickDone?: () => void } = {}) {
  const can = useCan()
  const q = useCampaigns()
  const m = useSalesMutations()
  const offers = useOffers(true).data?.offers ?? []
  const latest: Record<string, number | null> = Object.fromEntries(offers.map((o) => [o.id, o.published_version]))
  const offerName = (c: Campaign) => (c.offer_id ? `${offers.find((o) => o.id === c.offer_id)?.name ?? c.offer_id} v${c.offer_version}` : 'LaudLine (built-in)')
  const [editing, setEditing] = useState<Campaign | 'new' | null>(null)
  const [draft, setDraft] = useState<{ draft: CampaignDraft; leads: { source?: string; tag?: string } } | null>(null)
  const [starting, setStarting] = useState<Campaign | null>(null)
  const [launching, setLaunching] = useState<Campaign | null>(null)
  const [allowLearning, setAllowLearning] = useState(true)
  const learns = (c: Campaign | null) => !!c?.offer_id && c.mode !== 'human_assisted'

  const start = (c: Campaign, confirmed = false) =>
    m.start.mutate({ id: c.id, legal_review_confirmed: confirmed, ...(confirmed && learns(c) ? { allow_learning: allowLearning } : {}) }, {
      onSuccess: () => { toast.success(`${c.name} is running`); setStarting(null); setLaunching(null) },
      onError: (e) => {
        if (String(e.message).startsWith('legal_review_required')) {
          setLaunching(null)
          setStarting(c)
        } else toast.error(e.message)
      },
    })

  return (
    <>
      {can('manager') && <div className="mb-3 flex justify-end"><Button onClick={() => setEditing('new')}><Plus /> New campaign</Button></div>}
      {q.error && <ErrorBanner error={q.error} />}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={3} cols={6} /> : (q.data?.campaigns.length ?? 0) === 0 ? (
          <EmptyState title="No campaigns yet" description="A campaign is an offer to sell, a list of leads, a call type, calling hours and limits. Nothing is dialled until you start one." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Campaign</TableHead><TableHead>Status</TableHead><TableHead>To dial</TableHead>
                <TableHead>On calls</TableHead><TableHead>Dialled (24 h)</TableHead><TableHead>Demos</TableHead><TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {q.data?.campaigns.map((c) => (
                <TableRow key={c.id}>
                  <TableCell>
                    <div className="font-medium">{c.name}</div>
                    <div className="text-xs text-muted-foreground">{offerName(c)} · {MODE_LABEL[c.mode]} · {c.calling_window.start}–{c.calling_window.end} lead's time · up to {c.max_concurrent} at once</div>
                    {c.script_split === 'auto' && c.scripts?.how === 'auto' && (
                      <div className="text-xs text-muted-foreground">
                        Script split: {Object.entries(c.scripts.alloc).map(([v, share]) => `${v} ${Math.round(share * 100)}%`).join(' · ')}
                        {(() => {
                          const lead = Object.entries(c.scripts.p_best ?? {}).sort((a, b) => b[1] - a[1])[0]
                          return lead && lead[1] >= 0.9 ? ` · ${lead[0]} likely better (${Math.round(lead[1] * 100)}%)` : ''
                        })()}
                      </div>
                    )}
                    {c.status === 'paused' && c.paused_reason && (
                      <div className="mt-1 flex items-start gap-1 text-xs text-destructive"><AlertTriangle className="mt-0.5 size-3 shrink-0" /> Paused itself: {c.paused_reason.detail}</div>
                    )}
                    {c.offer_id && latest[c.offer_id] && latest[c.offer_id]! > (c.offer_version ?? 0) && (
                      <div className="text-xs text-amber-700 dark:text-amber-400">v{latest[c.offer_id]} of this offer is published. Edit to move to it (needs a fresh legal review).</div>
                    )}
                  </TableCell>
                  <TableCell><Badge variant="secondary" className={cn('border-0', STATUS_STYLE[c.status])}>{titleCase(c.status)}</Badge></TableCell>
                  <TableCell className="tabular-nums">{c.progress?.to_dial ?? 0} <span className="text-muted-foreground">of {c.progress?.total_leads ?? 0}</span></TableCell>
                  <TableCell className="tabular-nums">{c.progress?.active_calls ?? 0}</TableCell>
                  <TableCell className="tabular-nums">{c.progress?.dialled_24h ?? 0} <span className="text-muted-foreground">/ {c.daily_cap}</span></TableCell>
                  <TableCell className="tabular-nums">{c.progress?.demos ?? 0}</TableCell>
                  <TableCell className="space-x-1 text-right">
                    {can('manager') && <>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(c)}><Pencil /> Edit</Button>
                    {c.status === 'running' ? (
                      <Button size="sm" variant="outline" disabled={m.pause.isPending} onClick={() => m.pause.mutate(c.id, { onSuccess: () => toast.success('Paused. Calls in progress will finish.') })}><Pause /> Pause</Button>
                    ) : c.status !== 'completed' && (
                      <Button size="sm" disabled={m.start.isPending} onClick={() => setLaunching(c)}><Play /> Start</Button>
                    )}
                    </>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      {editing && <CampaignDialog campaign={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
      {draft && <CampaignDialog campaign={null} draft={draft.draft} leads={draft.leads} onClose={() => setDraft(null)} />}
      <QuickSetupDialog offerId={quickOffer ?? null} offerName={offers.find((o) => o.id === quickOffer)?.name ?? 'this offer'}
        onClose={() => onQuickDone?.()} onPick={(d, leads) => { onQuickDone?.(); setDraft({ draft: d, leads }) }} />
      <LaunchDialog campaign={launching} starting={m.start.isPending} onClose={() => setLaunching(null)} onContinue={(c) => start(c)} />
      <ConfirmDialog open={!!starting} onOpenChange={(o) => !o && setStarting(null)} title="Has a lawyer reviewed this campaign?"
        confirmLabel="Yes, reviewed — start calling" loading={m.start.isPending} onConfirm={() => starting && start(starting, true)}
        description={<>Fines for unlawful telemarketing calls are charged per call. Before the first call, a telemarketing lawyer must have reviewed this campaign's call type, opening line and lead list for every place it will call. Your confirmation is recorded with your name and the time.</>}>
        <div className="flex items-start gap-2 rounded-md bg-muted p-3 text-sm"><Scale className="mt-0.5 size-4 shrink-0" /> {starting && MODE_LABEL[starting.mode]}</div>
        {learns(starting) && (
          <label className="mt-3 flex items-start gap-2 text-sm">
            <Checkbox className="mt-0.5" checked={allowLearning} onCheckedChange={(v) => setAllowLearning(!!v)} />
            <span>Let the agent use lessons from earlier calls. <span className="text-muted-foreground">Coaching on how to run the conversation, tested before use. Openers, prices, claims and consent never change. Recorded with this sign-off.</span></span>
          </label>
        )}
      </ConfirmDialog>
    </>
  )
}

function CampaignDialog({ campaign, draft, leads, onClose }: {
  campaign: Campaign | null
  /** From quick-setup: settings worked out from the rules, with why. */
  draft?: CampaignDraft
  leads?: { source?: string; tag?: string }
  onClose: () => void
}) {
  const status = useSalesStatus()
  const m = useSalesMutations()
  const offers = (useOffers(true).data?.offers ?? []).filter((o) => o.published && (o.status !== 'archived' || o.id === campaign?.offer_id))
  const [form, setForm] = useState<CampaignInput>(() => {
    if (campaign) return { ...BLANK, ...campaign, guardrails: { ...DEFAULT_GUARDRAILS, ...(campaign.guardrails ?? {}) } }
    if (draft) {
      const { lead_count: _n, countries: _c, reasons: _r, warnings: _w, ...rest } = draft
      void _n; void _c; void _r; void _w
      return { ...BLANK, ...rest }
    }
    return BLANK
  })
  const [numbers, setNumbers] = useState((campaign?.from_numbers ?? draft?.from_numbers ?? []).join(', '))
  const [retries, setRetries] = useState((campaign?.retry_after_hours ?? draft?.retry_after_hours ?? [48, 96]).join(', '))
  const [queueAfter, setQueueAfter] = useState(!!draft)
  const g = form.guardrails ?? DEFAULT_GUARDRAILS
  const setG = (patch: Partial<Guardrails>) => set({ guardrails: { ...g, ...patch } })
  const set = (patch: Partial<CampaignInput>) => setForm({ ...form, ...patch })
  const locked = !!campaign && campaign.status !== 'draft'
  const personas = status.data?.personas ?? []
  const pending = m.createCampaign.isPending || m.updateCampaign.isPending

  const offer = offers.find((o) => o.id === form.offer_id) ?? null
  const fromNumbers = numbers.split(/[,\s]+/).filter(Boolean)
  const badNumbers = fromNumbers.filter((n) => !E164.test(n))
  const willNeedReview = !!campaign?.legal_review && material(campaign) !== material({ ...form, from_numbers: fromNumbers })
  const pickOffer = (id: string) => {
    if (id === BUILT_IN) return set({ offer_id: null, offer_version: null, variant_ids: [] })
    const o = offers.find((x) => x.id === id)
    set({ offer_id: id, offer_version: o?.published?.version ?? null, variant_ids: o?.published?.variants.slice(0, 1).map((v) => v.id) ?? [] })
  }
  const toggleVariant = (id: string) => {
    const has = form.variant_ids.includes(id)
    const next = has ? form.variant_ids.filter((v) => v !== id) : [...form.variant_ids, id].slice(-2)
    if (next.length) set({ variant_ids: next })
  }

  const save = () => {
    const body: CampaignInput = {
      ...form,
      from_numbers: fromNumbers,
      retry_after_hours: retries.split(/[,\s]+/).map(Number).filter((n) => n > 0),
      voicemail_policy: form.mode === 'ai_consented' ? form.voicemail_policy : 'hang_up',
    }
    const done = { onSuccess: () => { toast.success('Saved'); onClose() } }
    if (campaign) m.updateCampaign.mutate({ id: campaign.id, ...body }, done)
    else m.createCampaign.mutate(body, {
      onSuccess: (r) => {
        toast.success('Saved')
        if (draft && queueAfter) {
          m.queueLeads.mutate({ campaign_id: r.campaign.id, ...(leads ?? {}) }, {
            onSuccess: (q) => toast.success(`${q.queued} lead${q.queued === 1 ? '' : 's'} queued${q.blocked ? `, ${q.blocked} blocked by the checks` : ''}`),
          })
        }
        onClose()
      },
    })
  }
  const togglePersona = (id: string) => {
    const has = form.persona_ids.includes(id)
    const next = has ? form.persona_ids.filter((p) => p !== id) : [...form.persona_ids, id].slice(-2)
    if (next.length) set({ persona_ids: next })
  }
  const toggleDay = (d: number) => {
    const days = form.calling_window.days.includes(d) ? form.calling_window.days.filter((x) => x !== d) : [...form.calling_window.days, d]
    if (days.length) set({ calling_window: { ...form.calling_window, days: days.sort() } })
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{campaign ? 'Edit campaign' : 'New campaign'}</DialogTitle>
          <DialogDescription>Every lead passes the compliance checks before each call. These settings can only make calling narrower than the law allows, never wider.</DialogDescription>
        </DialogHeader>
        {draft && (
          <div className="space-y-1 rounded-md border border-primary/30 bg-primary/5 p-2 text-sm">
            <div className="font-medium">Why these settings</div>
            <ul className="list-disc pl-4 text-muted-foreground">{draft.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            {draft.warnings.map((w) => <p key={w} className="text-amber-700 dark:text-amber-400">{w}</p>)}
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" className="sm:col-span-2"><Input value={form.name} placeholder="Agencies in Austin — AI automation, December" onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label="What this campaign sells" className="sm:col-span-2" hint="Offers are written and published on the Offers tab. A campaign keeps the version it was started with.">
            <SimpleSelect className="w-full" value={form.offer_id ?? BUILT_IN} onChange={pickOffer}
              options={[{ value: BUILT_IN, label: 'LaudLine AI receptionist (built-in scripts)' },
                ...offers.map((o) => ({ value: o.id, label: `${o.name} · v${o.published?.version}` }))]} />
          </Field>
          {offer && offer.published && offer.published.version !== form.offer_version && (
            <div className="sm:col-span-2 flex flex-wrap items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-sm">
              This campaign uses v{form.offer_version}; v{offer.published.version} is published.
              <Button size="xs" variant="outline" onClick={() => set({ offer_version: offer.published!.version })}>Use v{offer.published.version}</Button>
            </div>
          )}
          <Field label="Call type" className="sm:col-span-2" hint={MODE_HELP[form.mode] + (locked ? ' Cannot be changed once the campaign has started.' : '')}>
            <SimpleSelect className="w-full" disabled={locked} value={form.mode} onChange={(v) => set({ mode: v as CampaignMode })}
              options={(Object.keys(MODE_LABEL) as CampaignMode[]).map((k) => ({ value: k, label: MODE_LABEL[k] }))} />
          </Field>
          <Field label="Caller ID numbers" className="sm:col-span-2" hint="Your dedicated outbound numbers in international format, comma-separated. Use local numbers for the country you call (an AU number for AU leads); the one nearest the lead is used.">
            <Input value={numbers} placeholder="+61290000000, +442071234567" aria-invalid={badNumbers.length > 0} onChange={(e) => setNumbers(e.target.value)} />
            {badNumbers.length > 0 && <p className="text-xs text-destructive">Not in international format: {badNumbers.join(', ')} (e.g. +61290000000)</p>}
          </Field>
          <Field label="Call from (lead's local time)"><Input type="time" value={form.calling_window.start} onChange={(e) => set({ calling_window: { ...form.calling_window, start: e.target.value } })} /></Field>
          <Field label="Until"><Input type="time" value={form.calling_window.end} onChange={(e) => set({ calling_window: { ...form.calling_window, end: e.target.value } })} /></Field>
          <Field label="Days" className="sm:col-span-2" hint="Legal calling hours for the lead's state always apply as well.">
            <div className="flex flex-wrap gap-1">
              {DAYS.map((d, i) => (
                <Button key={d} type="button" size="sm" variant={form.calling_window.days.includes(i + 1) ? 'default' : 'outline'} onClick={() => toggleDay(i + 1)}>{d}</Button>
              ))}
            </div>
          </Field>
          <Field label="Calls at once" hint="Start at 1–2."><Input type="number" min={1} max={10} value={form.max_concurrent} onChange={(e) => set({ max_concurrent: Number(e.target.value) })} /></Field>
          <Field label="Calls per day (most)"><Input type="number" min={1} value={form.daily_cap} onChange={(e) => set({ daily_cap: Number(e.target.value) })} /></Field>
          <Field label="Attempts per lead"><Input type="number" min={1} max={6} value={form.max_attempts} onChange={(e) => set({ max_attempts: Number(e.target.value) })} /></Field>
          <Field label="Hours between attempts" hint="e.g. 48, 96"><Input value={retries} onChange={(e) => setRetries(e.target.value)} /></Field>
          {offer?.published && (
            <Field label="Scripts" className="sm:col-span-2" hint="Pick two to split leads 50/50 and compare meeting rates.">
              <div className="space-y-2">
                {offer.published.variants.map((v) => (
                  <label key={v.id} className="flex cursor-pointer items-center gap-2 rounded-md border p-2 text-sm">
                    <Checkbox checked={form.variant_ids.includes(v.id)} onCheckedChange={() => toggleVariant(v.id)} />
                    <span className="font-medium">{v.label}</span>
                  </label>
                ))}
              </div>
            </Field>
          )}
          {!offer && form.mode !== 'human_assisted' && (
            <Field label="Script" className="sm:col-span-2" hint="Pick two to split leads 50/50 and compare demo rates.">
              <div className="space-y-2">
                {personas.map((p) => (
                  <label key={p.id} className="flex cursor-pointer items-start gap-2 rounded-md border p-2 text-sm">
                    <Checkbox className="mt-0.5" checked={form.persona_ids.includes(p.id)} onCheckedChange={() => togglePersona(p.id)} />
                    <span><span className="font-medium">{p.label}</span><span className="block text-xs text-muted-foreground">“{p.opener}”</span></span>
                  </label>
                ))}
              </div>
            </Field>
          )}
          <Field label="Kind of business called" hint="Optional, e.g. dental, agency, restaurant. Helps the agent's wording.">
            <Input value={form.vertical} maxLength={40} onChange={(e) => set({ vertical: e.target.value })} />
          </Field>
          <Field label="Context for the agent" className="sm:col-span-2" hint="Optional notes it reads before each call, e.g. “These are agencies we met at the Sydney expo.” It can't change the rules.">
            <Textarea rows={2} maxLength={1000} value={form.notes} onChange={(e) => set({ notes: e.target.value })} />
          </Field>
          <Field label="Who to call first">
            <SimpleSelect className="w-full" value={form.order ?? 'fit'} onChange={(v) => set({ order: v as 'fit' | 'fifo' })}
              options={[{ value: 'fit', label: 'Best fit for the offer first' }, { value: 'fifo', label: 'In the order they were added' }]} />
          </Field>
          <Field label="When to try again" hint="Smart: a retry moves (by up to a day) to an hour when businesses there tend to pick up, always inside your calling hours and the legal ones. Callbacks keep the time they agreed.">
            <SimpleSelect className="w-full" value={form.retry_timing ?? 'fixed'} onChange={(v) => set({ retry_timing: v as 'fixed' | 'smart' })}
              options={[{ value: 'smart', label: 'Smart: when they pick up' }, { value: 'fixed', label: 'Exactly after the retry delay' }]} />
          </Field>
          {form.offer_id && form.variant_ids.length > 1 && (
            <Field label="Script split" hint="Auto: after 30 businesses reached on each script, more calls go to the one getting more real next steps (never under 20% each). Both scripts are already signed off, so this needs no new review.">
              <SimpleSelect className="w-full" value={form.script_split ?? 'even'} onChange={(v) => set({ script_split: v as 'even' | 'auto' })}
                options={[{ value: 'auto', label: 'Auto: more calls to the winner' }, { value: 'even', label: 'Even split' }]} />
            </Field>
          )}
          <div className="space-y-2 rounded-md border p-3 sm:col-span-2">
            <label className="flex items-center gap-2 text-sm font-medium">
              <Checkbox checked={g.enabled} onCheckedChange={(v) => setG({ enabled: !!v })} /> Pause by itself when something looks wrong
            </label>
            {g.enabled && (
              <div className="grid gap-2 text-sm sm:grid-cols-2">
                <Field label="Opt-outs above (% of people reached)"><Input type="number" min={1} max={100} value={Math.round(g.max_optout_rate * 100)} onChange={(e) => setG({ max_optout_rate: Math.max(1, Number(e.target.value)) / 100 })} /></Field>
                <Field label="Answered below (% of calls)"><Input type="number" min={0} max={100} value={Math.round(g.min_answer_rate * 100)} onChange={(e) => setG({ min_answer_rate: Number(e.target.value) / 100 })} /></Field>
                <Field label="Failed calls above (%)"><Input type="number" min={1} max={100} value={Math.round(g.max_error_rate * 100)} onChange={(e) => setG({ max_error_rate: Math.max(1, Number(e.target.value)) / 100 })} /></Field>
                <Field label="Cost per meeting above (US$, optional)"><Input type="number" min={1} value={g.max_cost_per_meeting_usd ?? ''} onChange={(e) => setG({ max_cost_per_meeting_usd: e.target.value ? Number(e.target.value) : null })} /></Field>
                <p className="text-xs text-muted-foreground sm:col-span-2">Measured over the last {g.window_hours} hours, after enough calls to tell. You get an email (SALES_ALERT_EMAILS). Changing these never needs a new legal review.</p>
              </div>
            )}
          </div>
          {draft && (
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <Checkbox checked={queueAfter} onCheckedChange={(v) => setQueueAfter(!!v)} /> Queue the {draft.lead_count} lead{draft.lead_count === 1 ? '' : 's'} when it's saved
            </label>
          )}
          {form.mode === 'ai_consented' && (
            <Field label="When voicemail answers" className="sm:col-span-2">
              <SimpleSelect className="w-full" value={form.voicemail_policy} onChange={(v) => set({ voicemail_policy: v as CampaignInput['voicemail_policy'] })}
                options={[{ value: 'hang_up', label: 'Hang up' }, { value: 'leave_message', label: 'Leave a short message with a callback number' }]} />
            </Field>
          )}
        </div>
        {willNeedReview && (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-sm">
            <Scale className="mt-0.5 size-4 shrink-0" />
            You changed what is said or how the calls are made. Saving clears the lawyer's sign-off{campaign?.status === 'running' ? ', so pause the campaign first' : ''}; you'll confirm a new review when you start it.
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={form.name.trim().length < 2 || badNumbers.length > 0 || (!!form.offer_id && !form.variant_ids.length) || pending} onClick={save}>{pending && <Loader2 className="animate-spin" />} Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
