import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Pause, Pencil, Play, Plus, Scale } from 'lucide-react'
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
import { MODE_HELP, MODE_LABEL, useCampaigns, useSalesMutations, useSalesStatus, type Campaign, type CampaignInput, type CampaignMode } from '@/lib/sales'
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
  voicemail_policy: 'hang_up', vertical: '', notes: '',
}

export function Campaigns() {
  const q = useCampaigns()
  const m = useSalesMutations()
  const [editing, setEditing] = useState<Campaign | 'new' | null>(null)
  const [starting, setStarting] = useState<Campaign | null>(null)

  const start = (c: Campaign, confirmed = false) =>
    m.start.mutate({ id: c.id, legal_review_confirmed: confirmed }, {
      onSuccess: () => { toast.success(`${c.name} is running`); setStarting(null) },
      onError: (e) => (String(e.message).startsWith('legal_review_required') ? setStarting(c) : toast.error(e.message)),
    })

  return (
    <>
      <div className="mb-3 flex justify-end"><Button onClick={() => setEditing('new')}><Plus /> New campaign</Button></div>
      {q.error && <ErrorBanner error={q.error} />}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={3} cols={6} /> : (q.data?.campaigns.length ?? 0) === 0 ? (
          <EmptyState title="No campaigns yet" description="A campaign is a list of leads, a call type, calling hours and limits. Nothing is dialled until you start one." />
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
                    <div className="text-xs text-muted-foreground">{MODE_LABEL[c.mode]} · {c.calling_window.start}–{c.calling_window.end} lead's time · up to {c.max_concurrent} at once</div>
                  </TableCell>
                  <TableCell><Badge variant="secondary" className={cn('border-0', STATUS_STYLE[c.status])}>{titleCase(c.status)}</Badge></TableCell>
                  <TableCell className="tabular-nums">{c.progress?.to_dial ?? 0} <span className="text-muted-foreground">of {c.progress?.total_leads ?? 0}</span></TableCell>
                  <TableCell className="tabular-nums">{c.progress?.active_calls ?? 0}</TableCell>
                  <TableCell className="tabular-nums">{c.progress?.dialled_24h ?? 0} <span className="text-muted-foreground">/ {c.daily_cap}</span></TableCell>
                  <TableCell className="tabular-nums">{c.progress?.demos ?? 0}</TableCell>
                  <TableCell className="space-x-1 text-right">
                    <Button size="sm" variant="ghost" onClick={() => setEditing(c)}><Pencil /> Edit</Button>
                    {c.status === 'running' ? (
                      <Button size="sm" variant="outline" disabled={m.pause.isPending} onClick={() => m.pause.mutate(c.id, { onSuccess: () => toast.success('Paused. Calls in progress will finish.') })}><Pause /> Pause</Button>
                    ) : c.status !== 'completed' && (
                      <Button size="sm" disabled={m.start.isPending} onClick={() => start(c)}><Play /> Start</Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      {editing && <CampaignDialog campaign={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog open={!!starting} onOpenChange={(o) => !o && setStarting(null)} title="Has a lawyer reviewed this campaign?"
        confirmLabel="Yes, reviewed — start calling" loading={m.start.isPending} onConfirm={() => starting && start(starting, true)}
        description={<>Fines for unlawful telemarketing calls are charged per call. Before the first call, a telemarketing lawyer must have reviewed this campaign's call type, opening line and lead list for every place it will call. Your confirmation is recorded with your name and the time.</>}>
        <div className="flex items-start gap-2 rounded-md bg-muted p-3 text-sm"><Scale className="mt-0.5 size-4 shrink-0" /> {starting && MODE_LABEL[starting.mode]}</div>
      </ConfirmDialog>
    </>
  )
}

function CampaignDialog({ campaign, onClose }: { campaign: Campaign | null; onClose: () => void }) {
  const status = useSalesStatus()
  const m = useSalesMutations()
  const [form, setForm] = useState<CampaignInput>(campaign ? { ...BLANK, ...campaign } : BLANK)
  const [numbers, setNumbers] = useState((campaign?.from_numbers ?? []).join(', '))
  const [retries, setRetries] = useState((campaign?.retry_after_hours ?? [48, 96]).join(', '))
  const set = (patch: Partial<CampaignInput>) => setForm({ ...form, ...patch })
  const locked = !!campaign && campaign.status !== 'draft'
  const personas = status.data?.personas ?? []
  const pending = m.createCampaign.isPending || m.updateCampaign.isPending

  const save = () => {
    const body: CampaignInput = {
      ...form,
      from_numbers: numbers.split(/[,\s]+/).filter(Boolean),
      retry_after_hours: retries.split(/[,\s]+/).map(Number).filter((n) => n > 0),
      voicemail_policy: form.mode === 'ai_consented' ? form.voicemail_policy : 'hang_up',
    }
    const done = { onSuccess: () => { toast.success('Saved'); onClose() }, onError: (e: Error) => toast.error(e.message) }
    if (campaign) m.updateCampaign.mutate({ id: campaign.id, ...body }, done)
    else m.createCampaign.mutate(body, done)
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
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" className="sm:col-span-2"><Input value={form.name} placeholder="Austin plumbers — November" onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label="Call type" className="sm:col-span-2" hint={MODE_HELP[form.mode] + (locked ? ' Cannot be changed once the campaign has started.' : '')}>
            <SimpleSelect className="w-full" disabled={locked} value={form.mode} onChange={(v) => set({ mode: v as CampaignMode })}
              options={(Object.keys(MODE_LABEL) as CampaignMode[]).map((k) => ({ value: k, label: MODE_LABEL[k] }))} />
          </Field>
          <Field label="Caller ID numbers" className="sm:col-span-2" hint="Your dedicated outbound numbers, comma-separated. The one nearest the lead's area code is used.">
            <Input value={numbers} placeholder="+15125550100, +12145550100" onChange={(e) => setNumbers(e.target.value)} />
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
          {form.mode !== 'human_assisted' && (
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
          {form.mode === 'ai_consented' && (
            <Field label="When voicemail answers" className="sm:col-span-2">
              <SimpleSelect className="w-full" value={form.voicemail_policy} onChange={(v) => set({ voicemail_policy: v as CampaignInput['voicemail_policy'] })}
                options={[{ value: 'hang_up', label: 'Hang up' }, { value: 'leave_message', label: 'Leave a short message with a callback number' }]} />
            </Field>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={form.name.trim().length < 2 || pending} onClick={save}>{pending && <Loader2 className="animate-spin" />} Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
