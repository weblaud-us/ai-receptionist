import { useState } from 'react'
import { toast } from 'sonner'
import { Archive, GitMerge, Loader2, Mail, Plus, Sparkles, Star, StopCircle } from 'lucide-react'
import { ConfirmDialog, Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { dateTime } from '@receptionist/shared/lib/format'
import { useCan } from '@/lib/roles'
import {
  DEAL_STAGE_LABEL, DEAL_STAGES, draftLeadEmail, searchSales, useCrmMutations, useCustomFields, useFollowUpMutations, usePeople,
  type Deal, type DealStage, type SalesLead, type ScheduledEmail,
} from '@/lib/sales'
import { useMarket } from './offer-editor/market'
import { money } from './crm-logic'

/** The CRM side of a lead (SALES_SUITE_PLAN.md Phases 15–17): owner, fit, deals, contacts, email, custom fields, merge. */
export function LeadCrm({ lead, deals, scheduled }: { lead: SalesLead; deals: Deal[]; scheduled: ScheduledEmail[] }) {
  const can = useCan()
  const people = usePeople().data?.people ?? []
  const f = useFollowUpMutations()
  const m = useCrmMutations()
  const [composing, setComposing] = useState(false)
  const [merging, setMerging] = useState(false)
  return (
    <div className="space-y-4">
      <section className="grid gap-2 sm:grid-cols-2">
        <Field label="Owner">
          <SimpleSelect size="sm" className="w-full" value={lead.owner_id ?? ''} disabled={!can('caller')}
            onChange={(v) => f.patchLead.mutate({ id: lead.id, owner_id: v }, { onSuccess: () => toast.success('Owner changed') })}
            options={[{ value: '', label: 'Nobody' }, ...people.map((p) => ({ value: p.id, label: p.name }))]} />
        </Field>
        <Field label="Fit for the offer">
          {lead.fit ? (
            <div className="text-sm" title={lead.fit.reasons.join('\n')}>
              <span className="font-medium tabular-nums">{lead.fit.score}</span>/100
              <span className="text-xs text-muted-foreground"> · {lead.fit.source === 'research' ? 'from their website' : 'from the lead record'}</span>
              {lead.fit.reasons.length > 0 && <div className="text-xs text-muted-foreground">{lead.fit.reasons.join(' · ')}</div>}
            </div>
          ) : <span className="text-sm text-muted-foreground">Scored when it's queued or researched</span>}
        </Field>
      </section>

      <Deals lead={lead} deals={deals} />
      <Contacts lead={lead} />

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="font-medium">Email</h3>
          {can('caller') && <Button size="xs" variant="outline" onClick={() => setComposing(true)}><Mail /> Write an email</Button>}
        </div>
        {scheduled.length > 0 ? (
          <div className="space-y-1 rounded-md border p-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-medium">Follow-ups scheduled</span>
              {can('caller') && <Button size="xs" variant="ghost" onClick={() => m.stopSequence.mutate(lead.id, { onSuccess: (r) => toast.success(`${r.cancelled} stopped`) })}><StopCircle /> Stop</Button>}
            </div>
            {scheduled.map((e) => <div key={e.id}>{e.step}. {e.subject} · {dateTime(e.send_after)}</div>)}
          </div>
        ) : <p className="text-xs text-muted-foreground">{lead.email_consent_at ? `Agreed to email ${dateTime(lead.email_consent_at)}.` : 'No consent to email yet: given on a call, recorded by a manager, or when they write to us.'}</p>}
        {lead.replied_at && <p className="text-xs">Last replied {dateTime(lead.replied_at)}</p>}
        {lead.whatsapp && <p className="text-xs text-muted-foreground">WhatsApp {lead.whatsapp}{lead.whatsapp_consent_at ? ` · agreed on a call ${dateTime(lead.whatsapp_consent_at)}` : ''}</p>}
        {lead.roi_estimate && <p className="text-xs text-muted-foreground">Told on a call: roughly {lead.roi_estimate.amount.toLocaleString()} {lead.roi_estimate.currency} {lead.roi_estimate.label} ({dateTime(lead.roi_estimate.at)})</p>}
      </section>

      <CustomFields lead={lead} />

      {can('manager') && (
        <Button size="sm" variant="outline" onClick={() => setMerging(true)}><GitMerge /> Merge a duplicate into this lead</Button>
      )}
      {composing && <ComposeDialog lead={lead} deals={deals} onClose={() => setComposing(false)} />}
      {merging && <MergeDialog lead={lead} onClose={() => setMerging(false)} />}
    </div>
  )
}

function Deals({ lead, deals }: { lead: SalesLead; deals: Deal[] }) {
  const can = useCan()
  const m = useCrmMutations()
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')
  const [amount, setAmount] = useState('')
  const [lost, setLost] = useState<{ deal: Deal; reason: string } | null>(null)
  const setStage = (d: Deal, stage: DealStage) => {
    if (stage === 'lost') return setLost({ deal: d, reason: '' })
    m.updateDeal.mutate({ id: d.id, stage }, { onSuccess: () => toast.success(DEAL_STAGE_LABEL[stage]) })
  }
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">Deals</h3>
        {can('caller') && <Button size="xs" variant="outline" onClick={() => setAdding(!adding)}><Plus /> New deal</Button>}
      </div>
      {adding && (
        <div className="flex gap-2">
          <Input value={title} placeholder={`${lead.business_name} · what they'd buy`} onChange={(e) => setTitle(e.target.value)} />
          <Input className="w-28" type="number" min={0} value={amount} placeholder="Amount" onChange={(e) => setAmount(e.target.value)} />
          <Button size="sm" disabled={title.trim().length < 2 || m.createDeal.isPending}
            onClick={() => m.createDeal.mutate({ lead_id: lead.id, title: title.trim(), amount: Number(amount) || 0 }, { onSuccess: () => { setAdding(false); setTitle(''); setAmount('') } })}>Add</Button>
        </div>
      )}
      {deals.length === 0 && !adding && <p className="text-xs text-muted-foreground">No deals yet. One opens by itself when a call ends interested or with a meeting.</p>}
      {deals.map((d) => (
        <div key={d.id} className="space-y-1 rounded-md border p-2 text-sm">
          <div className="flex items-center gap-2">
            <span className="flex-1 font-medium">{d.title}</span>
            <SimpleSelect size="sm" value={d.stage} disabled={!can('caller')} onChange={(v) => setStage(d, v as DealStage)}
              options={DEAL_STAGES.map((s) => ({ value: s, label: DEAL_STAGE_LABEL[s] }))} />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="tabular-nums">{money(d.amount, d.currency)}</span>
            <span>· {Math.round(d.probability * 100)}%</span>
            {d.expected_close && <span>· closes {d.expected_close}</span>}
            {d.lost_reason && <span>· {d.lost_reason}</span>}
            {d.proposal?.sent_at && <span>· proposal sent {d.proposal.sent_at}</span>}
          </div>
          {can('caller') && d.stage !== 'won' && d.stage !== 'lost' && (
            <div className="flex flex-wrap gap-2">
              <Input className="h-7 w-28 text-xs" type="number" min={0} defaultValue={d.amount}
                onBlur={(e) => Number(e.target.value) !== d.amount && m.updateDeal.mutate({ id: d.id, amount: Number(e.target.value) || 0 })} />
              <Input className="h-7 w-36 text-xs" type="date" defaultValue={d.expected_close ?? ''}
                onBlur={(e) => e.target.value !== (d.expected_close ?? '') && m.updateDeal.mutate({ id: d.id, expected_close: e.target.value })} />
              {!d.proposal?.sent_at && (
                <Button size="xs" variant="ghost" onClick={() => m.updateDeal.mutate({ id: d.id, stage: 'proposal', proposal: { sent_at: new Date().toISOString().slice(0, 10), amount: d.amount, link: '' } })}>Proposal sent</Button>
              )}
            </div>
          )}
        </div>
      ))}
      <Dialog open={!!lost} onOpenChange={(o) => !o && setLost(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader><DialogTitle>Why was it lost?</DialogTitle></DialogHeader>
          <Input autoFocus value={lost?.reason ?? ''} maxLength={300} onChange={(e) => lost && setLost({ ...lost, reason: e.target.value })} />
          <DialogFooter>
            <Button variant="outline" onClick={() => setLost(null)}>Cancel</Button>
            <Button disabled={(lost?.reason.trim().length ?? 0) < 3} onClick={() => { if (lost) m.updateDeal.mutate({ id: lost.deal.id, stage: 'lost', lost_reason: lost.reason.trim() }); setLost(null) }}>Mark lost</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function Contacts({ lead }: { lead: SalesLead }) {
  const can = useCan()
  const m = useCrmMutations()
  const [form, setForm] = useState<{ name: string; role: string; email: string; phone: string } | null>(null)
  const contacts = (lead.contacts ?? []).filter((c) => !c.archived)
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">People</h3>
        {can('caller') && <Button size="xs" variant="outline" onClick={() => setForm({ name: '', role: '', email: '', phone: '' })}><Plus /> Add person</Button>}
      </div>
      {contacts.map((c) => (
        <div key={c.id} className="flex items-start gap-2 rounded-md border p-2 text-sm">
          <div className="flex-1">
            <div className="font-medium">{c.name}{c.primary && <Badge variant="secondary" className="ml-2">main</Badge>}</div>
            <div className="text-xs text-muted-foreground">{[c.role, c.email, c.phone].filter(Boolean).join(' · ')}{c.email_consent_at ? ' · agreed to email' : ''}</div>
          </div>
          {can('caller') && !c.primary && <Button size="icon-xs" variant="ghost" aria-label="Make main contact" onClick={() => m.updateContact.mutate({ leadId: lead.id, id: c.id, primary: true })}><Star /></Button>}
          {can('caller') && <Button size="icon-xs" variant="ghost" aria-label="Archive" onClick={() => m.updateContact.mutate({ leadId: lead.id, id: c.id, archived: true })}><Archive /></Button>}
        </div>
      ))}
      {contacts.length === 0 && !form && <p className="text-xs text-muted-foreground">Only the main number so far. Add the people you speak to. The dialer only ever calls the lead's main number.</p>}
      {form && (
        <div className="grid gap-2 rounded-md border p-2 sm:grid-cols-2">
          <Input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Input placeholder="Role (e.g. practice manager)" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} />
          <Input placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <Input placeholder="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <div className="flex gap-2 sm:col-span-2">
            <Button size="sm" disabled={!form.name.trim() || m.addContact.isPending}
              onClick={() => m.addContact.mutate({ leadId: lead.id, ...form }, { onSuccess: () => setForm(null), onError: (e) => toast.error(e.message) })}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setForm(null)}>Cancel</Button>
          </div>
        </div>
      )}
    </section>
  )
}

function ComposeDialog({ lead, deals, onClose }: { lead: SalesLead; deals: Deal[]; onClose: () => void }) {
  const can = useCan()
  const m = useCrmMutations()
  const [market] = useMarket()
  const addresses = [lead.email, ...(lead.contacts ?? []).filter((c) => !c.archived).map((c) => c.email)].filter(Boolean) as string[]
  const [to, setTo] = useState(addresses[0] ?? '')
  const [subject, setSubject] = useState('')
  const [text, setText] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [consentNote, setConsentNote] = useState('')
  const consented = (to === lead.email && !!lead.email_consent_at) || (lead.contacts ?? []).some((c) => c.email === to && c.email_consent_at)
  const draft = (purpose: 'follow_up' | 'proposal' | 'reply') => {
    setDrafting(true)
    const deal = deals.find((d) => d.stage !== 'won' && d.stage !== 'lost')
    draftLeadEmail(lead.id, { purpose, deal_id: deal?.id, market })
      .then((r) => { setSubject(r.subject); setText(r.body) })
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setDrafting(false))
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Email {lead.business_name}</DialogTitle>
          <DialogDescription>Your legal footer and an unsubscribe link are added. Replies come back to this lead's timeline.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Field label="To">
            {addresses.length > 1 ? <SimpleSelect className="w-full" value={to} onChange={setTo} options={addresses.map((a) => ({ value: a, label: a }))} />
              : <Input value={to} onChange={(e) => setTo(e.target.value)} />}
          </Field>
          {!consented && (
            <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-sm">
              <p>No consent on record for this address. Commercial email needs it (AU Spam Act, UK PECR).</p>
              {can('manager') && (
                <div className="flex gap-2">
                  <Input value={consentNote} placeholder="Evidence: e.g. asked for pricing by email at the expo, 12 Nov" onChange={(e) => setConsentNote(e.target.value)} />
                  <Button size="sm" variant="outline" disabled={consentNote.trim().length < 5 || !to}
                    onClick={() => m.emailConsent.mutate({ leadId: lead.id, email: to, note: consentNote.trim() }, { onSuccess: () => toast.success('Consent recorded'), onError: (e) => toast.error(e.message) })}>Record</Button>
                </div>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-1">
            <span className="text-xs text-muted-foreground">Draft with AI:</span>
            {(['follow_up', 'proposal', 'reply'] as const).map((p) => (
              <Button key={p} size="xs" variant="ghost" className="text-primary" disabled={drafting} onClick={() => draft(p)}>
                {drafting ? <Loader2 className="animate-spin" /> : <Sparkles />} {p === 'follow_up' ? 'Follow-up' : p === 'proposal' ? 'Proposal' : 'Reply'}
              </Button>
            ))}
          </div>
          <Field label="Subject"><Input value={subject} maxLength={150} onChange={(e) => setSubject(e.target.value)} /></Field>
          <Field label="Message"><Textarea rows={8} value={text} maxLength={6000} onChange={(e) => setText(e.target.value)} /></Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!to || subject.trim().length < 2 || text.trim().length < 2 || m.sendEmail.isPending}
            onClick={() => m.sendEmail.mutate({ leadId: lead.id, to, subject: subject.trim(), text: text.trim() }, { onSuccess: () => { toast.success('Sent'); onClose() }, onError: (e) => toast.error(e.message) })}>
            {m.sendEmail.isPending && <Loader2 className="animate-spin" />} Send
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CustomFields({ lead }: { lead: SalesLead }) {
  const fields = (useCustomFields().data?.fields ?? []).filter((f) => f.applies_to === 'lead')
  const m = useCrmMutations()
  const can = useCan()
  if (!fields.length) return null
  const save = (key: string, value: unknown) =>
    m.setCustom.mutate({ leadId: lead.id, values: { [key]: value } }, { onError: (e) => toast.error(e.message) })
  return (
    <section className="space-y-2">
      <h3 className="font-medium">Details</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        {fields.map((f) => {
          const value = (lead.custom ?? {})[f.key]
          return (
            <Field key={f.id} label={f.label}>
              {f.type === 'select' ? (
                <SimpleSelect size="sm" className="w-full" value={String(value ?? '')} disabled={!can('caller')} onChange={(v) => save(f.key, v)}
                  options={[{ value: '', label: '—' }, ...f.options.map((o) => ({ value: o, label: o }))]} />
              ) : (
                <Input className="h-8" type={f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text'} disabled={!can('caller')}
                  defaultValue={String(value ?? '')} onBlur={(e) => e.target.value !== String(value ?? '') && save(f.key, e.target.value)} />
              )}
            </Field>
          )
        })}
      </div>
    </section>
  )
}

function MergeDialog({ lead, onClose }: { lead: SalesLead; onClose: () => void }) {
  const m = useCrmMutations()
  const [q, setQ] = useState(lead.business_name)
  const [results, setResults] = useState<SalesLead[] | null>(null)
  const [pick, setPick] = useState<SalesLead | null>(null)
  const find = () => searchSales(q).then((r) => setResults(r.leads.filter((l) => l.id !== lead.id))).catch((e: Error) => toast.error(e.message))
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Merge a duplicate into {lead.business_name}</DialogTitle>
          <DialogDescription>The other record's notes, tasks, deals, people and history move here. The stricter compliance state always wins: if either is on the do-not-call list, this one will be.</DialogDescription>
        </DialogHeader>
        <div className="flex gap-2">
          <Input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void find()} />
          <Button variant="outline" onClick={() => void find()}>Find</Button>
        </div>
        <div className="max-h-64 space-y-1 overflow-y-auto">
          {results?.map((r) => (
            <button key={r.id} type="button" onClick={() => setPick(r)} className="flex w-full justify-between rounded-md border p-2 text-left text-sm hover:border-primary/50">
              <span>{r.business_name}<span className="block text-xs text-muted-foreground">{r.phone}</span></span>
              <Badge variant="secondary">{r.status}</Badge>
            </button>
          ))}
          {results?.length === 0 && <p className="text-sm text-muted-foreground">No other leads match.</p>}
        </div>
        <ConfirmDialog open={!!pick} onOpenChange={(o) => !o && setPick(null)} title={`Merge ${pick?.business_name} into this lead?`}
          confirmLabel="Merge" loading={m.merge.isPending} description="The other record is kept as “merged” for the audit trail and is never called again."
          onConfirm={() => pick && m.merge.mutate({ keep: lead.id, merge: pick.id }, { onSuccess: () => { toast.success('Merged'); onClose() }, onError: (e) => toast.error(e.message) })} />
      </DialogContent>
    </Dialog>
  )
}
