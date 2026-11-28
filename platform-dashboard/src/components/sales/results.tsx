import { useState } from 'react'
import { toast } from 'sonner'
import { ConfirmDialog, EmptyState, ErrorBanner, KeyValue, Pagination, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateTime, duration, titleCase } from '@receptionist/shared/lib/format'
import { pct, useAttempt, useAttempts, useCampaigns, useFunnel, useSalesMutations } from '@/lib/sales'
import { CoachCard } from '@/components/sales/coach-card'
import { callFlags, scoreTone } from '@/lib/learning'
import { useCan } from '@/lib/roles'
import { cn } from '@receptionist/shared/lib/utils'

type Labels = Record<string, string>
const outcomeText = (labels: Labels, o: string | null) => (o ? labels[o] ?? titleCase(o) : '—')

const amdText = (v: string | null) => (v ? titleCase(v.replace('machine-', 'machine: ').replace('-', ' ')) : '—')

export function Results() {
  const campaigns = useCampaigns().data?.campaigns ?? []
  const [campaignId, setCampaignId] = useState('all')
  const [outcome, setOutcome] = useState('all')
  const [days, setDays] = useState('30')
  const [unlabelled, setUnlabelled] = useState(false)
  const [flagged, setFlagged] = useState(false)
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<string | null>(null)
  const scope = campaignId === 'all' ? undefined : campaignId
  const f = useFunnel({ campaign_id: scope, days: Number(days) })
  const q = useAttempts({ campaign_id: scope, outcome: outcome === 'all' ? undefined : outcome, unlabelled: unlabelled || undefined,
    flagged: flagged || undefined, page })
  const funnel = f.data?.funnel
  const amd = f.data?.amd
  const top = funnel?.stages[0]?.count || 0
  const labels = f.data?.outcome_labels ?? {}

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <SimpleSelect size="sm" value={campaignId} onChange={(v) => { setCampaignId(v); setPage(1) }}
          options={[{ value: 'all', label: 'All campaigns' }, { value: 'single', label: 'Single calls' },
            ...campaigns.map((c) => ({ value: c.id, label: c.name }))]} />
        <SimpleSelect size="sm" value={outcome} onChange={(v) => { setOutcome(v); setPage(1) }}
          options={[{ value: 'all', label: 'Every outcome' }, ...(f.data?.outcomes ?? []).map((o) => ({ value: o, label: outcomeText(labels, o) }))]} />
        <SimpleSelect size="sm" value={days} onChange={setDays}
          options={[{ value: '7', label: 'Last 7 days' }, { value: '30', label: 'Last 30 days' }, { value: '90', label: 'Last 90 days' }]} />
        <label className="flex items-center gap-2 text-sm text-muted-foreground" title="Calls where nobody has checked who really answered. Machine detection must be right on 90% of 100 checked calls.">
          <Switch checked={unlabelled} onCheckedChange={(v) => { setUnlabelled(v); setPage(1) }} /> Needs a check: who answered?
        </label>
        <label className="flex items-center gap-2 text-sm text-muted-foreground" title="Calls where a fixed-rule check or the AI coach found a problem, such as a price said against the policy.">
          <Switch checked={flagged} onCheckedChange={(v) => { setFlagged(v); setPage(1) }} /> Flagged
        </label>
      </div>
      {f.error && <ErrorBanner error={f.error} />}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Funnel, last {days} days</CardTitle>
            <CardDescription>
              From dial to booked meeting.
              {f.data?.truncated && ` Counts the latest ${(f.data.counted_calls ?? 0).toLocaleString()} calls only: pick fewer days for the full picture.`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {(funnel?.stages ?? []).map((s) => (
              <div key={s.key} className="grid grid-cols-[11rem_1fr_3rem] items-center gap-3 text-sm">
                <span className="text-muted-foreground">{s.label}</span>
                <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${top ? (s.count / top) * 100 : 0}%` }} /></div>
                <span className="text-right font-medium tabular-nums">{s.count}</span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Rates and cost</CardTitle></CardHeader>
          <CardContent>
            <KeyValue label="A person answered">{pct(funnel?.answer_rate)}</KeyValue>
            <KeyValue label="Meetings per person reached">{pct(funnel?.demo_rate)}</KeyValue>
            <KeyValue label="A real next step"><span title="A meeting, details sent with their OK, or a callback that was kept, per business reached">{pct(funnel?.next_step_rate)}{funnel?.reached ? ` of ${funnel.reached}` : ''}</span></KeyValue>
            <KeyValue label="Asked not to be called">{funnel?.opt_outs ?? 0} ({pct(funnel?.opt_out_rate)})</KeyValue>
            <KeyValue label="Estimated call cost">${(funnel?.cost_usd ?? 0).toFixed(2)}</KeyValue>
            <KeyValue label="Cost per meeting">{funnel?.cost_per_demo_usd == null ? '—' : `$${funnel.cost_per_demo_usd.toFixed(2)}`}</KeyValue>
            <KeyValue label="Machine detection correct">{amd?.labelled ? `${pct(amd.accuracy)} of ${amd.labelled} checked` : 'None checked yet'}</KeyValue>
            {!!amd?.hung_up_on_people && <p className="pt-1 text-xs text-destructive">Hung up on {amd.hung_up_on_people} real {amd.hung_up_on_people === 1 ? 'person' : 'people'}.</p>}
          </CardContent>
        </Card>
      </div>
      {(funnel?.by_variant?.length ?? 0) > 1 && (
        <Card>
          <CardHeader><CardTitle>Script comparison</CardTitle><CardDescription>By offer, version and script. Meeting rate is meetings per person reached.</CardDescription></CardHeader>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>Offer · version · script</TableHead><TableHead>Dialled</TableHead><TableHead>People reached</TableHead><TableHead>Meetings</TableHead><TableHead>Meeting rate</TableHead><TableHead>Next step rate</TableHead></TableRow></TableHeader>
              <TableBody>
                {funnel?.by_variant.map((p) => (
                  <TableRow key={`${p.offer_id}@${p.offer_version}:${p.variant_id}`}><TableCell className="font-medium">{p.label}</TableCell><TableCell>{p.dialled}</TableCell><TableCell>{p.human}</TableCell><TableCell>{p.demo_booked}</TableCell><TableCell>{pct(p.demo_rate)}</TableCell><TableCell>{pct(p.next_step_rate)}</TableCell></TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      <Card className="gap-0 py-0">
        {q.error ? <div className="p-4"><ErrorBanner error={q.error} onRetry={() => void q.refetch()} /></div>
          : q.isLoading ? <TableSkeleton rows={6} cols={6} /> : (q.data?.attempts.length ?? 0) === 0 ? (
          <EmptyState title="No finished calls yet" />
        ) : (
          <>
            <Table>
              <TableHeader><TableRow><TableHead>When</TableHead><TableHead>Business</TableHead><TableHead>Answered by</TableHead><TableHead>Outcome</TableHead><TableHead>Length</TableHead><TableHead title="The AI coach's score out of 100; a red dot means a check found a problem">Coach</TableHead><TableHead>Summary</TableHead></TableRow></TableHeader>
              <TableBody>
                {q.data?.attempts.map((a) => (
                  <TableRow key={a.id} className="cursor-pointer" tabIndex={0} aria-label={`Open the call with ${a.business_name || a.to}`}
                    onClick={() => setOpen(a.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(a.id) } }}>
                    <TableCell className="whitespace-nowrap text-muted-foreground">{dateTime(a.created_at)}</TableCell>
                    <TableCell className="font-medium">{a.business_name || a.to}</TableCell>
                    <TableCell>{amdText(a.amd)}{a.amd_label ? ' ✓' : ''}</TableCell>
                    <TableCell>{outcomeText(labels, a.outcome)}</TableCell>
                    <TableCell className="tabular-nums">{a.duration_sec ? duration(a.duration_sec) : '—'}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {a.review?.status === 'done' ? <span className={cn('rounded-full px-1.5 py-0.5 text-xs font-medium tabular-nums', scoreTone(a.review.overall))}>{a.review.overall ?? '—'}</span> : <span className="text-muted-foreground">—</span>}
                      {callFlags(a).length > 0 && <span className="ml-1.5 inline-block size-2 rounded-full bg-rose-500 align-middle" title="Flagged" role="img" aria-label="Flagged" />}
                    </TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground">{a.summary || a.next_step || a.detail}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pagination page={q.data?.page ?? 1} pages={q.data?.pages ?? 1} total={q.data?.total ?? 0} onPage={setPage} label="calls" />
          </>
        )}
      </Card>
      <AttemptSheet id={open} manual={f.data?.manual_outcomes ?? []} labels={labels} onClose={() => setOpen(null)} />
    </div>
  )
}

function AttemptSheet({ id, manual, labels, onClose }: { id: string | null; manual: string[]; labels: Labels; onClose: () => void }) {
  const q = useAttempt(id)
  const m = useSalesMutations()
  const a = q.data?.attempt
  const [confirmDnc, setConfirmDnc] = useState(false)
  const canFix = useCan()('manager')
  const patch = (body: { outcome?: string; amd_label?: 'human' | 'machine'; confirm_dnc?: boolean }, after?: () => void) =>
    a && m.patchAttempt.mutate({ id: a.id, ...body }, { onSuccess: () => { toast.success('Saved'); after?.() } })
  // System outcomes (blocked by the check, nobody joined…) and "do not call" are shown, never picked here.
  const settable = !!a?.outcome && manual.includes(a.outcome)
  const showTurn = (turn: number) => document.getElementById(`call-turn-${turn}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{q.data?.lead?.business_name || a?.to || 'Call'}</SheetTitle>
          <SheetDescription>{a ? `${dateTime(a.created_at)} · from ${a.from} · attempt ${a.attempt_no}` : ''}</SheetDescription>
        </SheetHeader>
        {a && (
          <div className="space-y-5 px-4 pb-6 text-sm">
            <div>
              <KeyValue label="Outcome">
                {a.outcome === 'dnc' ? <span className="font-medium text-destructive">{outcomeText(labels, 'dnc')}</span> : !canFix ? outcomeText(labels, a.outcome) : (
                  <SimpleSelect size="sm" value={settable ? a.outcome ?? '' : ''} placeholder={outcomeText(labels, a.outcome)}
                    onChange={(v) => patch({ outcome: v })} options={manual.map((o) => ({ value: o, label: outcomeText(labels, o) }))} />
                )}
              </KeyValue>
              <KeyValue label="Detector said">{amdText(a.amd)}</KeyValue>
              {a.amd && canFix && (
                <KeyValue label="Who really answered?">
                  <span className="space-x-1">
                    <Button size="xs" variant={a.amd_label === 'human' ? 'default' : 'outline'} onClick={() => patch({ amd_label: 'human' })}>A person</Button>
                    <Button size="xs" variant={a.amd_label === 'machine' ? 'default' : 'outline'} onClick={() => patch({ amd_label: 'machine' })}>A machine</Button>
                  </span>
                </KeyValue>
              )}
              <KeyValue label="Length">{a.duration_sec ? duration(a.duration_sec) : '—'}</KeyValue>
              <KeyValue label="Script">{a.offer_id ? `${a.offer_id} v${a.offer_version} · ${a.variant_id}` : a.persona_id}</KeyValue>
              {a.qualification && (
                <KeyValue label="Notes">
                  <span className="block text-right text-xs">
                    Interest {a.qualification.interest}/5{a.qualification.role ? ` · ${a.qualification.role}` : ''}{a.qualification.decision_maker ? ' · decides' : ''}
                    {a.qualification.pains.length > 0 && <span className="block text-muted-foreground">Pains: {a.qualification.pains.join('; ')}</span>}
                    {a.qualification.timeline && <span className="block text-muted-foreground">Timing: {a.qualification.timeline}</span>}
                  </span>
                </KeyValue>
              )}
              {a.next_step && <KeyValue label="Next step">{a.next_step}</KeyValue>}
              {a.outcome !== 'dnc' && canFix && (
                <div className="pt-2">
                  <Button size="sm" variant="destructive" onClick={() => setConfirmDnc(true)}>They asked not to be called</Button>
                </div>
              )}
            </div>
            {a.summary && <p className="rounded-md bg-muted p-3">{a.summary}</p>}
            {a.mode !== 'human_assisted' && <CoachCard attempt={a} onTurn={showTurn} />}
            <section>
              <h3 className="mb-2 font-medium">Transcript</h3>
              {(q.data?.transcript.length ?? 0) === 0 ? <p className="text-muted-foreground">No conversation was recorded for this call.</p> : (
                <div className="space-y-2">
                  {q.data?.transcript.map((t, i) => (
                    <div key={i} id={`call-turn-${i}`} className={cn('scroll-mt-20', t.role === 'assistant' ? 'text-muted-foreground' : '')}>
                      <span className="text-xs font-medium uppercase">{t.role === 'assistant' ? 'Us' : 'Them'}</span> {t.text}
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </SheetContent>
      <ConfirmDialog open={confirmDnc} onOpenChange={setConfirmDnc} destructive title="Never call this number again?"
        description="It goes on the do-not-call list for every campaign and offer, for good. Use this only when they asked not to be called; for a plain “not interested”, pick that outcome instead."
        confirmLabel="Add to do-not-call" loading={m.patchAttempt.isPending}
        onConfirm={() => patch({ outcome: 'dnc', confirm_dnc: true }, () => setConfirmDnc(false))} />
    </Sheet>
  )
}
