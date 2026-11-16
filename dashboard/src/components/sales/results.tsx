import { useState } from 'react'
import { toast } from 'sonner'
import { EmptyState, ErrorBanner, KeyValue, Pagination, TableSkeleton } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { dateTime, duration, titleCase } from '@/lib/format'
import { pct, useAttempt, useAttempts, useCampaigns, useFunnel, useSalesMutations } from '@/lib/sales'

const amdText = (v: string | null) => (v ? titleCase(v.replace('machine-', 'machine: ').replace('-', ' ')) : '—')

export function Results() {
  const campaigns = useCampaigns().data?.campaigns ?? []
  const [campaignId, setCampaignId] = useState('all')
  const [outcome, setOutcome] = useState('all')
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<string | null>(null)
  const scope = campaignId === 'all' ? undefined : campaignId
  const f = useFunnel({ campaign_id: scope, days: 30 })
  const q = useAttempts({ campaign_id: scope, outcome: outcome === 'all' ? undefined : outcome, page })
  const funnel = f.data?.funnel
  const amd = f.data?.amd
  const top = funnel?.stages[0]?.count || 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <SimpleSelect size="sm" value={campaignId} onChange={(v) => { setCampaignId(v); setPage(1) }}
          options={[{ value: 'all', label: 'All campaigns' }, ...campaigns.map((c) => ({ value: c.id, label: c.name }))]} />
        <SimpleSelect size="sm" value={outcome} onChange={(v) => { setOutcome(v); setPage(1) }}
          options={[{ value: 'all', label: 'Every outcome' }, ...(f.data?.outcomes ?? []).map((o) => ({ value: o, label: titleCase(o) }))]} />
      </div>
      {f.error && <ErrorBanner error={f.error} />}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Funnel, last 30 days</CardTitle><CardDescription>From dial to booked demo. Trial and paid conversions arrive with billing.</CardDescription></CardHeader>
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
            <KeyValue label="Demos per person reached">{pct(funnel?.demo_rate)}</KeyValue>
            <KeyValue label="Asked not to be called">{funnel?.opt_outs ?? 0} ({pct(funnel?.opt_out_rate)})</KeyValue>
            <KeyValue label="Estimated call cost">${(funnel?.cost_usd ?? 0).toFixed(2)}</KeyValue>
            <KeyValue label="Cost per demo">{funnel?.cost_per_demo_usd == null ? '—' : `$${funnel.cost_per_demo_usd.toFixed(2)}`}</KeyValue>
            <KeyValue label="Machine detection correct">{amd?.labelled ? `${pct(amd.accuracy)} of ${amd.labelled} checked` : 'None checked yet'}</KeyValue>
            {!!amd?.hung_up_on_people && <p className="pt-1 text-xs text-destructive">Hung up on {amd.hung_up_on_people} real {amd.hung_up_on_people === 1 ? 'person' : 'people'}.</p>}
          </CardContent>
        </Card>
      </div>
      {(funnel?.by_persona.length ?? 0) > 1 && (
        <Card>
          <CardHeader><CardTitle>Script comparison</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>Script</TableHead><TableHead>Dialled</TableHead><TableHead>People reached</TableHead><TableHead>Demos</TableHead><TableHead>Demo rate</TableHead></TableRow></TableHeader>
              <TableBody>
                {funnel?.by_persona.map((p) => (
                  <TableRow key={p.persona_id}><TableCell className="font-medium">{p.persona_id}</TableCell><TableCell>{p.dialled}</TableCell><TableCell>{p.human}</TableCell><TableCell>{p.demo_booked}</TableCell><TableCell>{pct(p.demo_rate)}</TableCell></TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={6} cols={6} /> : (q.data?.attempts.length ?? 0) === 0 ? (
          <EmptyState title="No finished calls yet" />
        ) : (
          <>
            <Table>
              <TableHeader><TableRow><TableHead>When</TableHead><TableHead>Business</TableHead><TableHead>Answered by</TableHead><TableHead>Outcome</TableHead><TableHead>Length</TableHead><TableHead>Summary</TableHead></TableRow></TableHeader>
              <TableBody>
                {q.data?.attempts.map((a) => (
                  <TableRow key={a.id} className="cursor-pointer" onClick={() => setOpen(a.id)}>
                    <TableCell className="whitespace-nowrap text-muted-foreground">{dateTime(a.created_at)}</TableCell>
                    <TableCell className="font-medium">{a.business_name || a.to}</TableCell>
                    <TableCell>{amdText(a.amd)}{a.amd_label ? ' ✓' : ''}</TableCell>
                    <TableCell>{titleCase(a.outcome)}</TableCell>
                    <TableCell className="tabular-nums">{a.duration_sec ? duration(a.duration_sec) : '—'}</TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground">{a.summary || a.next_step || a.detail}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pagination page={q.data?.page ?? 1} pages={q.data?.pages ?? 1} total={q.data?.total ?? 0} onPage={setPage} label="calls" />
          </>
        )}
      </Card>
      <AttemptSheet id={open} outcomes={f.data?.outcomes ?? []} onClose={() => setOpen(null)} />
    </div>
  )
}

function AttemptSheet({ id, outcomes, onClose }: { id: string | null; outcomes: string[]; onClose: () => void }) {
  const q = useAttempt(id)
  const m = useSalesMutations()
  const a = q.data?.attempt
  const patch = (body: { outcome?: string; amd_label?: 'human' | 'machine' }) =>
    a && m.patchAttempt.mutate({ id: a.id, ...body }, { onSuccess: () => toast.success('Saved'), onError: (e) => toast.error(e.message) })
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
                <SimpleSelect size="sm" value={a.outcome ?? ''} onChange={(v) => patch({ outcome: v })} options={outcomes.map((o) => ({ value: o, label: titleCase(o) }))} />
              </KeyValue>
              <KeyValue label="Detector said">{amdText(a.amd)}</KeyValue>
              {a.amd && (
                <KeyValue label="Who really answered?">
                  <span className="space-x-1">
                    <Button size="xs" variant={a.amd_label === 'human' ? 'default' : 'outline'} onClick={() => patch({ amd_label: 'human' })}>A person</Button>
                    <Button size="xs" variant={a.amd_label === 'machine' ? 'default' : 'outline'} onClick={() => patch({ amd_label: 'machine' })}>A machine</Button>
                  </span>
                </KeyValue>
              )}
              <KeyValue label="Length">{a.duration_sec ? duration(a.duration_sec) : '—'}</KeyValue>
              <KeyValue label="Script">{a.persona_id}</KeyValue>
              {a.next_step && <KeyValue label="Next step">{a.next_step}</KeyValue>}
            </div>
            {a.summary && <p className="rounded-md bg-muted p-3">{a.summary}</p>}
            {q.data?.recording_url && <audio controls preload="none" className="w-full" src={q.data.recording_url} />}
            <section>
              <h3 className="mb-2 font-medium">Transcript</h3>
              {(q.data?.transcript.length ?? 0) === 0 ? <p className="text-muted-foreground">No conversation was recorded for this call.</p> : (
                <div className="space-y-2">
                  {q.data?.transcript.map((t, i) => (
                    <div key={i} className={t.role === 'assistant' ? 'text-muted-foreground' : ''}>
                      <span className="text-xs font-medium uppercase">{t.role === 'assistant' ? 'Us' : 'Them'}</span> {t.text}
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
