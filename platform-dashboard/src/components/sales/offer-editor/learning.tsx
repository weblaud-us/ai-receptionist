import { useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, Brain, FlaskConical, Loader2, Pin, PinOff, Plus, Sparkles, StopCircle, Trash2, Undo2 } from 'lucide-react'
import { ConfirmDialog } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { ApiError } from '@receptionist/shared/lib/api'
import { dateTime } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useCan } from '@/lib/roles'
import { useConfirm } from '@/lib/use-confirm'
import { pct, type OfferSettings } from '@/lib/sales'
import {
  chanceText, EXPERIMENT_LABEL, playbookDiff, STATUS_LABEL, testProgress, useLearning, useLearningMutations,
  type ArmNumbers, type EntryInput, type LearningPage, type Playbook, type PlaybookEntry,
} from '@/lib/learning'
import { LearningCurve } from '@/components/sales/charts'
import { InsightsTab } from './insights'

/**
 * The Learning tab (SALES_LEARNING_PLAN.md): the playbook of lessons the agent uses, the live test of
 * the next one, the learning curve, and what prospects asked that the offer doesn't answer yet. The
 * backend decides from outcomes; people can pin, edit, stop, roll back or switch it off.
 */
export function LearningTab({ id, form, canEdit, onAdd }: {
  id: string
  form: OfferSettings
  canEdit: boolean
  onAdd: (patch: Partial<OfferSettings>) => void
}) {
  const q = useLearning(id)
  const mu = useLearningMutations(id)
  const can = useCan()
  const manager = can('manager') && canEdit
  const [editing, setEditing] = useState(false)
  const [confirmBaseline, setConfirmBaseline] = useState(false)
  const confirm = useConfirm()
  const page = q.data
  if (q.isLoading) return <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline animate-spin" /> Loading…</p>
  if (!page) return <p className="text-sm text-destructive">The learning page couldn't load.</p>
  const exp = page.experiment
  return (
    <div className="space-y-5 text-sm">
      <StatusBar page={page} manager={manager} onMode={(mode) => {
        const save = () => mu.setMode.mutate(mode, { onSuccess: () => toast.success('Saved') })
        if (mode !== 'autopilot') return save()
        confirm.ask({ title: 'Turn on autopilot?', confirmLabel: 'Turn on autopilot', run: save,
          description: 'The AI writes new coaching from your calls. Each change must pass the test calls, then runs on some live calls and is kept only if it does better. Openers, prices, claims and consent never change on their own.' })
      }} />
      <CampaignConsent page={page} manager={manager} onAllow={(campaignId, allowed) => {
        const save = () => mu.consent.mutate({ campaignId, allowed }, {
          onSuccess: () => toast.success(allowed ? 'This campaign may use lessons now' : 'This campaign uses the baseline now') })
        if (!allowed) return save()
        confirm.ask({ title: 'Let this campaign use learned lessons?', confirmLabel: 'Allow learning', run: save,
          description: 'From its next call, it may use the live playbook and take part in its tests, on top of what its legal review covered.' })
      }} />
      {exp && <TestCard exp={exp} page={page} manager={manager} stopping={mu.stopTest.isPending}
        onStop={() => mu.stopTest.mutate(undefined, { onSuccess: () => toast.success('Test stopped: the current playbook stays') })} />}
      <section className="space-y-2">
        <h3 className="font-medium">Learning curve</h3>
        <p className="text-xs text-muted-foreground">Each week: the share of businesses reached that took a real next step (a meeting, details sent with their OK, or a callback that was kept), and the share that booked. Lines mark when a playbook went live or was rolled back.</p>
        <LearningCurve curve={page.curve} />
      </section>
      <LivePlaybook page={page} manager={manager} onEdit={() => setEditing(true)} onBaseline={() => setConfirmBaseline(true)}
        onPin={(entryId, pinned) => page.live && mu.pin.mutate({ playbookId: page.live.id, entryId, pinned })}
        onRun={() => mu.runLearning.mutate(undefined, { onSuccess: (r) => toast.message(r.playbook ? `Playbook v${r.playbook.n} written: its test calls are running` : r.note || 'Nothing new to try yet') })}
        running={mu.runLearning.isPending} />
      {(page.unanswered?.length ?? 0) > 0 && (
        <section className="space-y-2">
          <h3 className="font-medium">Questions the agent couldn't answer</h3>
          <p className="text-xs text-muted-foreground">Only people add facts. Add an answer to the draft as an objection, check it, and publish a new version.</p>
          {page.unanswered!.map((u, i) => (
            <div key={i} className="flex items-center justify-between gap-2 rounded-md border p-2 text-xs">
              <span>“{u.question}” <span className="text-muted-foreground">· {u.count} call{u.count === 1 ? '' : 's'}</span></span>
              {canEdit && <Button size="xs" variant="outline" onClick={() => { onAdd({ objections: [...form.objections, { objection: u.question.slice(0, 120), response: '' }] }); toast.success('Added to the draft: write the answer in Conversation → Objections') }}><Plus /> Add to draft</Button>}
            </div>
          ))}
        </section>
      )}
      <History page={page} manager={manager} onMakeLive={(pid) => confirm.ask({
        title: 'Make this playbook live?', confirmLabel: 'Make live',
        description: 'Its test calls run first if it has not passed them; then every campaign that allows learning uses it from the next call.',
        run: () => mu.makeLive.mutate(pid, {
          onSuccess: (r) => toast.success(r.result === 'live' ? 'Calls use this playbook now' : 'Its test calls are running; it goes live when they pass') }),
      })} />
      <section className="space-y-2 border-t pt-4">
        <h3 className="font-medium">What prospects said</h3>
        <InsightsTab id={id} form={form} canEdit={canEdit} onAdd={onAdd} />
      </section>
      {editing && <PlaybookEditor page={page} onClose={() => setEditing(false)} save={mu.createPlaybook} />}
      {confirm.dialog}
      <ConfirmDialog open={confirmBaseline} onOpenChange={setConfirmBaseline} title="Stop using a playbook?" confirmLabel="Use the baseline"
        description="Calls go back to exactly what the legal review covered, with no lessons, from the next dial. Any running test stops."
        loading={mu.baseline.isPending}
        onConfirm={() => mu.baseline.mutate(undefined, { onSuccess: () => { toast.success('Calls use the baseline now'); setConfirmBaseline(false) } })} />
    </div>
  )
}

function StatusBar({ page, manager, onMode }: { page: LearningPage; manager: boolean; onMode: (m: LearningPage['learning']['mode']) => void }) {
  const s = page.status
  return (
    <section className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 font-medium"><Brain className="size-4" /> The agent learns from its calls</h3>
        {manager ? (
          <SimpleSelect size="sm" value={page.learning.mode} onChange={(v) => onMode(v as LearningPage['learning']['mode'])}
            options={[{ value: 'autopilot', label: 'Autopilot: write and test new lessons' }, { value: 'paused', label: 'Paused: keep the live lessons' },
              { value: 'off', label: 'Off: the reviewed baseline' }]} />
        ) : <Badge variant="outline">{page.learning.mode}</Badge>}
      </div>
      <p className="text-xs text-muted-foreground">
        The AI writes coaching (how to run the conversation) and every change must pass the test calls, then runs on about {Math.round(page.learning.share * 100)}% of calls and is kept only if more businesses take a real next step. Openers, prices, claims and consent never change on their own.
      </p>
      <div className="flex flex-wrap gap-2 text-xs">
        <Badge variant="outline">{page.live ? `Live: playbook v${page.live.n}` : 'Baseline: no playbook'}</Badge>
        <Badge variant="outline">Coach {s.coach ? 'on' : 'off'} · ${s.spent_today_usd.toFixed(2)} of ${s.daily_cap_usd.toFixed(2)} today</Badge>
        {!s.playbooks_enabled && <Badge variant="destructive">Playbooks are switched off (SALES_PLAYBOOKS_ENABLED)</Badge>}
        {!s.autopilot && <Badge variant="outline">The AI won't write new lessons (SALES_LEARNING_AUTOPILOT=false)</Badge>}
        {s.reason && <Badge variant="outline">{s.reason}</Badge>}
      </div>
    </section>
  )
}

function CampaignConsent({ page, manager, onAllow }: { page: LearningPage; manager: boolean; onAllow: (id: string, allowed: boolean) => void }) {
  const live = page.campaigns.filter((c) => c.learning_possible && c.status !== 'draft')
  if (live.length === 0) return null
  const missing = live.filter((c) => !c.learning_allowed)
  return (
    <section className="space-y-2">
      <h3 className="font-medium">Campaigns</h3>
      {missing.length > 0 && <p className="text-xs text-muted-foreground">A campaign uses lessons only when its legal sign-off allows it. The others keep the reviewed baseline.</p>}
      {live.map((c) => (
        <div key={c.id} className="flex items-center justify-between gap-2 rounded-md border p-2 text-xs">
          <span>{c.name} <span className="text-muted-foreground">· v{c.offer_version} · {c.status}</span></span>
          {c.learning_allowed ? (
            <span className="flex items-center gap-2"><Badge variant="outline">Uses lessons</Badge>
              {manager && <Button size="xs" variant="ghost" onClick={() => onAllow(c.id, false)}>Stop</Button>}</span>
          ) : c.signed_off ? (
            manager ? <Button size="xs" variant="outline" onClick={() => onAllow(c.id, true)}>Allow learning</Button> : <Badge variant="outline">Baseline</Badge>
          ) : <span className="text-muted-foreground">Not signed off yet</span>}
        </div>
      ))}
    </section>
  )
}

function ArmRow({ label, a }: { label: string; a: ArmNumbers | undefined }) {
  return (
    <tr className="border-b last:border-0">
      <td className="py-1 pr-2 font-medium">{label}</td>
      <td className="py-1 pr-2 tabular-nums">{a?.leads ?? 0}</td>
      <td className="py-1 pr-2 tabular-nums">{pct(a?.next_step_rate)}</td>
      <td className="py-1 pr-2 tabular-nums">{a?.meetings ?? 0}</td>
      <td className="py-1 pr-2 tabular-nums">{a?.opt_outs ?? 0}</td>
      <td className="py-1 pr-2 tabular-nums">{a?.early_hangups ?? 0}</td>
      <td className="py-1 tabular-nums">{(a?.severe_flags ?? 0) + (a?.other_flags ?? 0)}</td>
    </tr>
  )
}

function TestCard({ exp, page, manager, onStop, stopping }: { exp: NonNullable<LearningPage['experiment']>; page: LearningPage; manager: boolean; onStop: () => void; stopping: boolean }) {
  const [confirm, setConfirm] = useState(false)
  const ev = exp.last_eval
  const progress = testProgress(exp)
  const challenger = page.playbooks.find((p) => p.id === exp.challenger_id)
  return (
    <section className="space-y-2 rounded-md border border-sky-200 bg-sky-50/50 p-3 dark:border-sky-500/30 dark:bg-sky-500/5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 font-medium"><FlaskConical className="size-4" /> Testing playbook v{challenger?.n ?? '?'} on {Math.round(exp.share * 100)}% of calls</h3>
        <Badge variant="outline">{EXPERIMENT_LABEL[exp.status]}</Badge>
      </div>
      <p className="font-medium">{chanceText(ev?.p_better)}</p>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-sky-500" style={{ width: `${progress.pct}%` }} /></div>
      <p className="text-xs text-muted-foreground">
        {progress.next ? `${progress.done} of ${progress.next} businesses reached for the next check.` : 'At the last check.'} Started {dateTime(exp.started_at)}.
        {ev?.reason ? ` ${ev.reason}` : ''}
      </p>
      <table className="w-full text-left text-xs">
        <thead><tr className="text-muted-foreground"><th className="py-1 pr-2 font-normal"></th><th className="py-1 pr-2 font-normal">Reached</th><th className="py-1 pr-2 font-normal">Next step</th><th className="py-1 pr-2 font-normal">Meetings</th><th className="py-1 pr-2 font-normal">Opt-outs</th><th className="py-1 pr-2 font-normal">Early hang-ups</th><th className="py-1 font-normal">Flags</th></tr></thead>
        <tbody>
          <ArmRow label={exp.champion_id ? 'Current playbook' : 'Baseline'} a={ev?.champion} />
          <ArmRow label="New playbook" a={ev?.challenger} />
        </tbody>
      </table>
      {(ev?.guards?.length ?? 0) > 0 && ev!.guards!.map((g, i) => <p key={i} className="flex gap-1 text-xs text-destructive"><AlertTriangle className="size-3.5" />{g}</p>)}
      {ev?.coverage != null && ev.coverage < 0.7 && <p className="text-xs text-amber-700">Only {Math.round(ev.coverage * 100)}% of the new playbook's calls are checked so far.</p>}
      {manager && <Button size="sm" variant="outline" onClick={() => setConfirm(true)}><StopCircle /> Stop the test</Button>}
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Stop this test?" confirmLabel="Stop" loading={stopping}
        description="The new playbook is set aside and every call uses the current one again." onConfirm={() => { onStop(); setConfirm(false) }} />
    </section>
  )
}

function Entry({ e, manager, onPin }: { e: PlaybookEntry; manager: boolean; onPin?: (pinned: boolean) => void }) {
  return (
    <div className="rounded-md border p-2 text-xs">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-medium capitalize">{e.kind}</span>
        <span className="flex items-center gap-1">
          <Badge variant="outline">{e.origin === 'learned' ? 'Learned' : 'Written by a person'}</Badge>
          {e.evidence.leads > 0 && <Badge variant="outline">from {e.evidence.leads} businesses</Badge>}
          {manager && onPin && (
            <Button size="xs" variant="ghost" title={e.pinned ? 'Unpin: the AI may change it' : 'Pin: the AI never changes it'} onClick={() => onPin(!e.pinned)}>
              {e.pinned ? <Pin className="text-sky-600" /> : <PinOff />}
            </Button>
          )}
        </span>
      </div>
      <p><span className="text-muted-foreground">When</span> {e.when}, {e.do}</p>
      {e.say && <p className="mt-0.5 text-muted-foreground">For example: “{e.say}”</p>}
    </div>
  )
}

function LivePlaybook({ page, manager, onEdit, onBaseline, onPin, onRun, running }: {
  page: LearningPage; manager: boolean; onEdit: () => void; onBaseline: () => void; onPin: (entryId: string, pinned: boolean) => void
  onRun: () => void; running: boolean
}) {
  const live = page.live
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">{live ? `Live playbook v${live.n}` : 'No playbook yet: calls use the reviewed baseline'}</h3>
        {manager && (
          <span className="flex flex-wrap gap-1">
            <Button size="sm" variant="outline" onClick={onRun} disabled={running || !!page.experiment} title={page.experiment ? 'A test is running' : 'Ask the AI to write the next playbook from recent calls now'}>
              {running ? <Loader2 className="animate-spin" /> : <Sparkles />} Learn now
            </Button>
            <Button size="sm" variant="outline" onClick={onEdit}><Plus /> Edit lessons</Button>
            {live && <Button size="sm" variant="ghost" onClick={onBaseline}><Undo2 /> Baseline</Button>}
          </span>
        )}
      </div>
      {live && live.live_since && <p className="text-xs text-muted-foreground">Live since {dateTime(live.live_since)}.</p>}
      {(live?.entries ?? []).map((e) => <Entry key={e.id} e={e} manager={manager} onPin={(p) => onPin(e.id, p)} />)}
    </section>
  )
}

function History({ page, manager, onMakeLive }: { page: LearningPage; manager: boolean; onMakeLive: (id: string) => void }) {
  const [open, setOpen] = useState<string | null>(null)
  if (page.playbooks.length === 0) return null
  const byId = new Map(page.playbooks.map((p) => [p.id, p]))
  return (
    <section className="space-y-2">
      <h3 className="font-medium">History</h3>
      {page.playbooks.map((p: Playbook) => {
        const parent = p.parent ? byId.get(p.parent) : undefined
        const diff = playbookDiff(parent?.entries ?? [], p.entries).filter((d) => d.op !== 'same')
        return (
          <div key={p.id} className="rounded-md border p-2 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <button type="button" className="text-left font-medium hover:underline" onClick={() => setOpen(open === p.id ? null : p.id)}>
                v{p.n} · {STATUS_LABEL[p.status]} · {p.origin === 'learned' ? 'written by the AI' : `written by ${p.created_by}`} · {dateTime(p.created_at)}
              </button>
              {manager && ['retired', 'rolled_back', 'ready', 'failed_checks'].includes(p.status) && p.id !== page.learning.live_playbook_id && (
                <Button size="xs" variant="outline" onClick={() => onMakeLive(p.id)}>Make live</Button>
              )}
            </div>
            {p.why && <p className="text-muted-foreground">{p.why}</p>}
            {p.decision?.reason && <p className={cn(p.status === 'rolled_back' || p.status === 'failed_checks' ? 'text-destructive' : 'text-muted-foreground')}>{p.decision.reason}</p>}
            {open === p.id && (
              <ul className="mt-1 space-y-1">
                {diff.length === 0 && <li className="text-muted-foreground">No change from the version before.</li>}
                {diff.map((d, i) => (
                  <li key={i} className={cn(d.op === 'remove' ? 'text-destructive line-through' : d.op === 'add' ? 'text-emerald-700 dark:text-emerald-400' : '')}>
                    {d.op === 'add' ? '+ ' : d.op === 'remove' ? '− ' : '~ '}{d.entry.when}, {d.entry.do}
                    {(p.changes.find((c) => c.entry_id === d.entry.id)?.why) && <span className="text-muted-foreground"> — {p.changes.find((c) => c.entry_id === d.entry.id)?.why}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )
      })}
    </section>
  )
}

function PlaybookEditor({ page, onClose, save }: { page: LearningPage; onClose: () => void; save: ReturnType<typeof useLearningMutations>['createPlaybook'] }) {
  const [rows, setRows] = useState<EntryInput[]>(() => (page.live?.entries ?? []).map((e) => ({ id: e.id, kind: e.kind, when: e.when, do: e.do, say: e.say, pinned: e.pinned })))
  const [why, setWhy] = useState('')
  const [problems, setProblems] = useState<Record<string, string[]>>({})
  const update = (i: number, patch: Partial<EntryInput>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const submit = (goLive: boolean) => save.mutate({ entries: rows, why, go_live: goLive }, {
    onSuccess: () => { toast.success('Saved: its test calls are running'); onClose() },
    onError: (e) => {
      const detail = e instanceof ApiError ? (e.detail as { problems?: Record<string, string[]> } | undefined) : undefined
      if (detail?.problems) setProblems(detail.problems)
    },
  })
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit the lessons</DialogTitle>
          <DialogDescription>
            Coaching only: “when …, do …”, and an optional example question. No prices, figures, claims, names, links or anything about being an AI — the offer and the fixed rules own those. Saving runs the full test calls first.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {rows.map((r, i) => {
            const issues = r.id ? problems[r.id] : undefined
            return (
              <div key={i} className={cn('space-y-2 rounded-md border p-2', issues && 'border-destructive')}>
                <div className="flex items-center gap-2">
                  <SimpleSelect size="sm" value={r.kind} onChange={(v) => update(i, { kind: v as EntryInput['kind'] })}
                    options={page.kinds.map((k) => ({ value: k.value, label: k.label }))} />
                  <Button size="xs" variant="ghost" className="ml-auto text-destructive" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 /> Remove</Button>
                </div>
                <Input value={r.when} maxLength={120} placeholder="When… e.g. they say they already have a receptionist" onChange={(e) => update(i, { when: e.target.value })} />
                <Textarea rows={2} value={r.do} maxLength={240} placeholder="Do… e.g. ask what happens to calls when she's with a patient, then listen" onChange={(e) => update(i, { do: e.target.value })} />
                <Input value={r.say} maxLength={160} placeholder={`Example question (optional, at most ${page.limits.say_words} words)`} onChange={(e) => update(i, { say: e.target.value })} />
                {issues && <ul className="text-xs text-destructive">{issues.map((p, k) => <li key={k}>{p}</li>)}</ul>}
              </div>
            )
          })}
          {rows.length < page.limits.entries && (
            <Button size="sm" variant="outline" onClick={() => setRows([...rows, { kind: 'objection', when: '', do: '', say: '' }])}><Plus /> Add a lesson</Button>
          )}
          <Input value={why} maxLength={300} placeholder="Why this change (shown in the history)" onChange={(e) => setWhy(e.target.value)} />
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="outline" disabled={save.isPending} onClick={() => submit(false)} title="After its test calls pass, it is tried on a share of calls and kept only if it sells better">Try it on calls first</Button>
          <Button disabled={save.isPending} onClick={() => submit(true)}>{save.isPending && <Loader2 className="animate-spin" />} Test, then make live</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
