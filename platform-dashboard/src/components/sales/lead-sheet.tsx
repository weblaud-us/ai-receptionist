/** One lead: its checks, details, memory, CRM, research, tasks, timeline, calls and consent. */
import { useState } from 'react'
import { toast } from 'sonner'
import { Ban, Brain, CheckCircle2, Loader2, Plus, RotateCcw, Search, ShieldCheck, X, XCircle } from 'lucide-react'
import { ConfirmDialog, Field, KeyValue } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { dateTime, timeAgo, titleCase } from '@receptionist/shared/lib/format'
import { failureText, STAGE_LABEL, useCampaigns, useFollowUpMutations, useSalesLead, useSalesMutations, type Activity, type GateDecision, type LeadPatch, type SalesLead, type SalesTask, type Stage } from '@/lib/sales'
import { useCan } from '@/lib/roles'
import { LeadCrm } from '@/components/sales/crm-lead'
import { cn } from '@receptionist/shared/lib/utils'
import { LeadStatus } from '@/components/sales/lead-status'
import { STAGE_MANUAL } from '@/components/sales/lead-logic'

function Checks({ gate }: { gate: GateDecision }) {
  return (
    <div className="space-y-1">
      {Object.entries(gate.checks).map(([name, result]) => (
        <div key={name} className="flex items-start gap-2 text-xs">
          {result.startsWith('fail') ? <XCircle className="mt-0.5 size-3.5 shrink-0 text-destructive" /> : <CheckCircle2 className={cn('mt-0.5 size-3.5 shrink-0', result === 'n/a' ? 'text-muted-foreground' : 'text-emerald-600')} />}
          <span><span className="font-medium">{titleCase(name)}</span> <span className="text-muted-foreground">{result.replace(/^(pass|fail):?\s*/, '') || (result === 'n/a' ? 'not needed' : '')}</span></span>
        </div>
      ))}
    </div>
  )
}

function ResearchCard({ lead }: { lead: SalesLead }) {
  const m = useSalesMutations()
  const r = lead.research
  const d = r?.data
  return (
    <section className="space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">What their website says</h3>
        <Button size="xs" variant="outline" disabled={!lead.website || m.researchLead.isPending || r?.status === 'running'}
          title={lead.website ? 'Read their website now (10–40 seconds)' : 'Add their website first'}
          onClick={() => m.researchLead.mutate(lead.id, { onSuccess: (x) => toast[x.research.status === 'done' ? 'success' : 'message'](x.research.status === 'done' ? 'Research updated' : x.research.error || 'Nothing found') })}>
          {m.researchLead.isPending ? <Loader2 className="animate-spin" /> : <Search />} {d ? 'Refresh' : 'Research now'}
        </Button>
      </div>
      {!lead.website ? <p className="text-xs text-muted-foreground">No website on file.</p>
        : !r ? <p className="text-xs text-muted-foreground">Not researched yet.</p>
        : r.status === 'pending' || r.status === 'running' ? <p className="text-xs text-muted-foreground">Queued: it will be read before the call.</p>
        : r.status !== 'done' || !d ? <p className="text-xs text-muted-foreground">Couldn't read it{r.error ? `: ${r.error}` : ''}.</p>
        : (
          <div className="space-y-1 text-xs">
            <p className="text-sm">{d.summary}</p>
            {d.hook && <p><span className="font-medium">Opening hook:</span> {d.hook}</p>}
            {d.likely_pains.length > 0 && <p><span className="font-medium">Likely problems:</span> {d.likely_pains.join('; ')}</p>}
            {d.services.length > 0 && <p><span className="font-medium">They offer:</span> {d.services.join(', ')}</p>}
            {d.size_signals && <p><span className="font-medium">Size:</span> {d.size_signals}</p>}
            <p className="text-muted-foreground">
              Website {d.tech_signals.website_quality || 'not rated'}{d.tech_signals.online_booking != null ? ` · online booking: ${d.tech_signals.online_booking ? 'yes' : 'no'}` : ''}{d.tech_signals.cms_guess ? ` · ${d.tech_signals.cms_guess}` : ''} · confidence {Math.round(d.confidence * 100)}% · read {r.fetched_at ? timeAgo(r.fetched_at) : ''}
            </p>
          </div>
        )}
    </section>
  )
}

export function LeadSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useSalesLead(id)
  const m = useSalesMutations()
  const f = useFollowUpMutations()
  const [confirmDnc, setConfirmDnc] = useState(false)
  const [requeue, setRequeue] = useState(false)
  const can = useCan()
  const lead = q.data?.lead
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{lead?.business_name || lead?.phone || 'Lead'}</SheetTitle>
          <SheetDescription>{lead ? `${lead.phone} · ${lead.source}` : ''}</SheetDescription>
        </SheetHeader>
        {lead && (
          <div className="space-y-5 px-4 pb-6 text-sm">
            <div>
              <KeyValue label="Status"><LeadStatus status={lead.status} /></KeyValue>
              <KeyValue label="Stage">{STAGE_LABEL[lead.stage] ?? lead.stage}</KeyValue>
              <KeyValue label="Where">{[lead.region_code, lead.country, lead.timezone || lead.timezones.join(' / ')].filter(Boolean).join(' · ') || 'Unknown'}</KeyValue>
              <KeyValue label="Line">{lead.line_type ? titleCase(lead.line_type) : 'Not checked'}</KeyValue>
              <KeyValue label="Attempts">{lead.attempts}{lead.next_eligible_at ? ` · next ${dateTime(lead.next_eligible_at)}` : ''}</KeyValue>
              {lead.cooldown_until && new Date(lead.cooldown_until) > new Date() && (
                <KeyValue label="Resting until">{dateTime(lead.cooldown_until)} <span className="text-xs text-muted-foreground">(said no to an offer)</span></KeyValue>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              {can('manager') && (
                <Button size="sm" variant="outline" disabled={m.checkLead.isPending}
                  title="Runs every compliance check now. A US number may need a paid line-type lookup."
                  onClick={() => m.checkLead.mutate(lead.id, { onSuccess: (r) => toast[r.decision.allowed ? 'success' : 'message'](r.decision.allowed ? 'Passes every check right now' : failureText(r.decision.failures[0])) })}>
                  {m.checkLead.isPending ? <Loader2 className="animate-spin" /> : <ShieldCheck />} Check now
                </Button>
              )}
              {can('manager') && !['dnc', 'bad_number', 'queued', 'in_progress', 'callback'].includes(lead.status) && (
                <Button size="sm" variant="outline" onClick={() => setRequeue(true)}><RotateCcw /> Call again</Button>
              )}
              {can('caller') && lead.status !== 'dnc' && <Button size="sm" variant="outline" className="text-destructive" onClick={() => setConfirmDnc(true)}><Ban /> Do not call</Button>}
            </div>
            <LeadDetails key={lead.id + (lead.stage ?? '')} lead={lead} pending={f.patchLead.isPending}
              onSave={(patch) => f.patchLead.mutate({ id: lead.id, ...patch }, { onSuccess: () => toast.success('Saved') })} />
            {lead.qualification && (
              <section className="space-y-1 rounded-md border p-3 text-xs">
                <h3 className="text-sm font-medium">From the last call</h3>
                <p>Interest {lead.qualification.interest}/5{lead.qualification.role ? ` · ${lead.qualification.role}` : ''}{lead.qualification.decision_maker ? ' · decides' : lead.qualification.decision_maker === false ? ' · not the decision maker' : ''}{lead.qualification.budget_signal !== 'unknown' ? ` · budget ${lead.qualification.budget_signal}` : ''}</p>
                {lead.qualification.pains.length > 0 && <p><span className="font-medium">Pains:</span> {lead.qualification.pains.join('; ')}</p>}
                {lead.qualification.current_solution && <p><span className="font-medium">Today they use:</span> {lead.qualification.current_solution}</p>}
                {lead.qualification.objections.length > 0 && <p><span className="font-medium">Objections:</span> {lead.qualification.objections.join('; ')}</p>}
                {lead.qualification.next_step && <p><span className="font-medium">Next step:</span> {lead.qualification.next_step}</p>}
                {(lead.qualification.people?.length ?? 0) > 0 && <p><span className="font-medium">Spoke to:</span> {lead.qualification.people!.map((p) => [p.name, p.role].filter(Boolean).join(', ')).join('; ')}</p>}
                {lead.qualification.best_time_to_reach && <p><span className="font-medium">Best time to reach them:</span> {lead.qualification.best_time_to_reach}</p>}
              </section>
            )}
            <MemoryCard lead={lead} />
            <LeadCrm lead={lead} deals={q.data?.deals ?? []} scheduled={q.data?.scheduled_emails ?? []} />
            <ResearchCard lead={lead} />
            <LeadTasks leadId={lead.id} tasks={q.data?.tasks ?? []} />
            <Timeline leadId={lead.id} activity={q.data?.activity ?? []} />
            {((lead.offer_history?.length ?? 0) > 0 || (lead.history?.length ?? 0) > 0) && (
              <section>
                <h3 className="mb-2 font-medium">Earlier campaigns</h3>
                <div className="space-y-1 text-xs text-muted-foreground">
                  {lead.offer_history?.map((h, i) => <div key={`o${i}`}>Not interested in {h.offer_id}{h.reason ? ` (“${h.reason}”)` : ''}, {dateTime(h.at)}</div>)}
                  {lead.history?.map((h, i) => <div key={`h${i}`}>{h.offer_id} v{h.offer_version}: {titleCase(h.status)} after {h.attempts} attempt{h.attempts === 1 ? '' : 's'}, until {dateTime(h.until)}</div>)}
                </div>
              </section>
            )}
            <section>
              <h3 className="mb-2 font-medium">Compliance checks</h3>
              {(lead.gate_log ?? []).length === 0 ? <p className="text-muted-foreground">Not checked yet.</p> : (
                <div className="space-y-3">
                  {(lead.gate_log ?? []).slice(0, 6).map((g, i) => (
                    <div key={i} className="rounded-md border p-2">
                      <div className="mb-1 flex items-center justify-between text-xs">
                        <span className={g.allowed ? 'font-medium text-emerald-600' : 'font-medium text-destructive'}>{g.allowed ? 'Allowed' : 'Not allowed'}</span>
                        <span className="text-muted-foreground">{dateTime(g.at)}</span>
                      </div>
                      <Checks gate={g} />
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section>
              <h3 className="mb-2 font-medium">Calls</h3>
              {(q.data?.attempts.length ?? 0) === 0 ? <p className="text-muted-foreground">Never called.</p> : q.data?.attempts.map((a) => (
                <div key={a.id} className="border-b py-2 last:border-0">
                  <div className="flex justify-between"><span className="font-medium">{titleCase(a.outcome ?? a.status)}</span><span className="text-xs text-muted-foreground">{timeAgo(a.created_at)}</span></div>
                  {a.summary && <p className="text-xs text-muted-foreground">{a.summary}</p>}
                </div>
              ))}
            </section>
            {(q.data?.consent.length ?? 0) > 0 && (
              <section>
                <h3 className="mb-2 font-medium">Consent</h3>
                {q.data?.consent.map((c, i) => (
                  <div key={i} className="text-xs">{titleCase(c.type)} via {c.source}, {dateTime(c.at)}{c.revoked_at ? ` — withdrawn ${dateTime(c.revoked_at)}` : ''}</div>
                ))}
              </section>
            )}
          </div>
        )}
        <ConfirmDialog open={confirmDnc} onOpenChange={setConfirmDnc} destructive title="Never call this number again?" confirmLabel="Add to do-not-call list"
          description="This is permanent. The number can't be taken off the list from the dashboard." loading={m.dncLead.isPending}
          onConfirm={() => lead && m.dncLead.mutate(lead.id, { onSuccess: () => { toast.success('Added to the do-not-call list'); setConfirmDnc(false) } })} />
        {requeue && lead && <RequeueDialog leadId={lead.id} onClose={() => setRequeue(false)} />}
      </SheetContent>
    </Sheet>
  )
}

function LeadDetails({ lead, pending, onSave }: { lead: SalesLead & { stage: Stage }; pending: boolean; onSave: (patch: LeadPatch) => void }) {
  const initial = { business_name: lead.business_name ?? '', contact_name: lead.contact_name ?? '', email: lead.email ?? '',
    website: lead.website ?? '', agent_notes: lead.agent_notes ?? '', stage: STAGE_MANUAL.includes(lead.stage) ? lead.stage : '' }
  const [form, setForm] = useState(initial)
  const dirty = JSON.stringify(form) !== JSON.stringify(initial)
  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch })
  return (
    <section className="grid gap-2 rounded-md border p-3 sm:grid-cols-2">
      <Field label="Business"><Input value={form.business_name} onChange={(e) => set({ business_name: e.target.value })} /></Field>
      <Field label="Contact"><Input value={form.contact_name} onChange={(e) => set({ contact_name: e.target.value })} /></Field>
      <Field label="Email"><Input type="email" value={form.email} onChange={(e) => set({ email: e.target.value })} /></Field>
      <Field label="Website" hint="This business's own website. With research on, the agent reads it before calling.">
        <Input value={form.website} placeholder="harbourdental.com.au" onChange={(e) => set({ website: e.target.value })} />
      </Field>
      <Field label="Notes for the agent" className="sm:col-span-2"
        hint={`What the agent should know about this business before it calls, e.g. “3 clinics, new owner since March, said they miss calls at lunch”. Used on the call, never read out. ${600 - form.agent_notes.length} characters left.`}>
        <Textarea rows={2} maxLength={600} value={form.agent_notes} placeholder="Anything that makes the call more relevant to them"
          onChange={(e) => set({ agent_notes: e.target.value })} />
      </Field>
      <Field label="Stage" className="sm:col-span-2" hint="Automatic follows the calls. Proposal, won and lost stay until you change them.">
        <SimpleSelect className="w-full" value={form.stage || 'auto'} onChange={(v) => set({ stage: v === 'auto' ? '' : v })}
          options={[{ value: 'auto', label: `Automatic (${STAGE_LABEL[lead.stage] ?? lead.stage})` },
            ...STAGE_MANUAL.map((s) => ({ value: s, label: STAGE_LABEL[s] }))]} />
      </Field>
      {dirty && (
        <div className="flex justify-end gap-2 sm:col-span-2">
          <Button size="sm" variant="ghost" onClick={() => setForm(initial)}>Undo</Button>
          <Button size="sm" disabled={pending} onClick={() => onSave(form)}>{pending && <Loader2 className="animate-spin" />} Save</Button>
        </div>
      )}
    </section>
  )
}

/** What the agent will know about earlier calls the next time it rings them (SALES_LEARNING_PLAN.md Phase 1). */
function MemoryCard({ lead }: { lead: SalesLead }) {
  const f = useFollowUpMutations()
  const can = useCan()
  const [confirm, setConfirm] = useState(false)
  const memory = lead.memory
  if (!memory) return null
  return (
    <section className="space-y-2 rounded-md border p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium"><Brain className="size-4" /> What the agent remembers</h3>
        {can('caller') && memory.lines.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setConfirm(true)}>Forget</Button>
        )}
      </div>
      {!memory.enabled ? (
        <p className="text-muted-foreground">Memory of earlier calls is switched off (SALES_LEAD_MEMORY_ENABLED).</p>
      ) : memory.lines.length === 0 ? (
        <p className="text-muted-foreground">
          Nothing yet{memory.cleared_at ? ` (forgotten ${dateTime(memory.cleared_at)})` : ''}. After a conversation, the agent picks up where it left off on the next call.
        </p>
      ) : (
        <>
          <ul className="list-disc space-y-1 pl-4">{memory.lines.map((line, i) => <li key={i}>{line}</li>)}</ul>
          <p className="text-muted-foreground">
            Built from the last {memory.days} days of calls for this company{memory.cleared_at ? `, since ${dateTime(memory.cleared_at)}` : ''}. The agent is told it may be wrong and never reads it out.
          </p>
        </>
      )}
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Forget earlier calls?" confirmLabel="Forget"
        description="The agent won't mention earlier calls with this business from now on. The calls themselves stay on record."
        loading={f.forgetMemory.isPending}
        onConfirm={() => f.forgetMemory.mutate(lead.id, { onSuccess: () => { toast.success('The agent will start fresh'); setConfirm(false) } })} />
    </section>
  )
}

function LeadTasks({ leadId, tasks }: { leadId: string; tasks: SalesTask[] }) {
  const f = useFollowUpMutations()
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const open = tasks.filter((t) => !t.done_at && !t.cancelled)
  return (
    <section>
      <h3 className="mb-2 font-medium">Follow-ups</h3>
      <div className="space-y-1">
        {open.map((t) => (
          <div key={t.id} className="flex items-center gap-2 text-sm">
            <Checkbox checked={false} onCheckedChange={() => f.updateTask.mutate({ id: t.id, done: true })} aria-label="Done" />
            <span className="flex-1">{t.title}</span>
            {t.due_at && <span className="text-xs text-muted-foreground">{dateTime(t.due_at)}</span>}
            <Button size="icon-xs" variant="ghost" aria-label="Cancel" onClick={() => f.updateTask.mutate({ id: t.id, cancelled: true })}><X /></Button>
          </div>
        ))}
        {open.length === 0 && <p className="text-xs text-muted-foreground">Nothing to do.</p>}
      </div>
      <div className="mt-2 flex gap-2">
        <Input value={title} placeholder="Send the proposal" onChange={(e) => setTitle(e.target.value)} />
        <Input type="datetime-local" className="w-48" value={due} onChange={(e) => setDue(e.target.value)} />
        <Button size="sm" variant="outline" disabled={title.trim().length < 2 || f.createTask.isPending}
          onClick={() => f.createTask.mutate({ lead_id: leadId, title: title.trim(), due_at: due ? new Date(due).toISOString() : null },
            { onSuccess: () => { setTitle(''); setDue('') } })}><Plus /> Add</Button>
      </div>
    </section>
  )
}

const ACTIVITY_ICON: Record<Activity['type'], string> = { call: '📞', note: '📝', email: '✉️', stage: '➜', task: '☑', meeting: '📅' }

function Timeline({ leadId, activity }: { leadId: string; activity: Activity[] }) {
  const f = useFollowUpMutations()
  const [note, setNote] = useState('')
  return (
    <section>
      <h3 className="mb-2 font-medium">Timeline</h3>
      <div className="mb-2 flex gap-2">
        <Textarea rows={2} value={note} placeholder="Add a note" onChange={(e) => setNote(e.target.value)} />
        <Button size="sm" variant="outline" className="self-end" disabled={!note.trim() || f.addNote.isPending}
          onClick={() => f.addNote.mutate({ id: leadId, text: note.trim() }, { onSuccess: () => setNote('') })}>Add</Button>
      </div>
      <div className="space-y-1.5">
        {activity.map((a) => (
          <div key={a.id} className="flex gap-2 text-xs">
            <span aria-hidden className="w-4 shrink-0 text-center">{ACTIVITY_ICON[a.type]}</span>
            <span className="flex-1 whitespace-pre-wrap">{a.text}</span>
            <span className="shrink-0 text-muted-foreground">{timeAgo(a.at)}</span>
          </div>
        ))}
        {activity.length === 0 && <p className="text-xs text-muted-foreground">Nothing yet.</p>}
      </div>
    </section>
  )
}

function RequeueDialog({ leadId, onClose }: { leadId: string; onClose: () => void }) {
  const m = useSalesMutations()
  const campaigns = (useCampaigns().data?.campaigns ?? []).filter((c) => c.status !== 'completed')
  const [campaignId, setCampaignId] = useState('')
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Call again in another campaign</DialogTitle>
          <DialogDescription>Their attempts start again and this campaign's results are kept in their history. A “not interested” offer is never pitched to them again, and they rest after any “no” before another offer calls.</DialogDescription>
        </DialogHeader>
        <Field label="Campaign"><SimpleSelect className="w-full" placeholder="Choose…" value={campaignId} onChange={setCampaignId} options={campaigns.map((c) => ({ value: c.id, label: c.name }))} /></Field>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!campaignId || m.queueLeads.isPending}
            onClick={() => m.queueLeads.mutate({ campaign_id: campaignId, lead_ids: [leadId] }, {
              onSuccess: (r) => {
                if (r.queued) toast.success('Queued')
                else toast.message(r.resting ? 'They said no recently, so they are resting.' : 'The compliance checks won’t allow this campaign to call them.')
                onClose()
              } })}>
            Queue
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
