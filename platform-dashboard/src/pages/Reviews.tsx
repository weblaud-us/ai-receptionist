import { useState } from 'react'
import { toast } from 'sonner'
import { Check, ClipboardCheck, FlaskConical, Loader2, X } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { dateTime, duration } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import {
  REVIEW_ISSUES,
  useEvalCases,
  useReviewCall,
  useReviewMutations,
  useReviewQueue,
  useReviewStats,
  type QueuedCall,
} from '@/lib/platform'

const splitTools = (v: string) => v.split(',').map((s) => s.trim()).filter(Boolean)

function ReviewSheet({ call, onClose }: { call: QueuedCall | null; onClose: () => void }) {
  const q = useReviewCall(call?.tenant_id ?? null, call?.call_id ?? null)
  const m = useReviewMutations()
  const [verdict, setVerdict] = useState<'good' | 'issue'>('good')
  const [issues, setIssues] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [expected, setExpected] = useState('')
  const [makeCase, setMakeCase] = useState(false)
  const [title, setTitle] = useState('')
  const [mustCall, setMustCall] = useState('')
  const [mustNot, setMustNot] = useState('')
  const transcript = q.data?.call.transcript ?? []

  const submit = async () => {
    if (!call) return
    try {
      await m.review.mutateAsync({ tenant_id: call.tenant_id, call_id: call.call_id, verdict, issues, notes, expected })
      if (makeCase) {
        await m.makeCase.mutateAsync({ tenant_id: call.tenant_id, call_id: call.call_id, title, expected,
          must_call: splitTools(mustCall), must_not_call: splitTools(mustNot) })
        toast.success('Reviewed — eval case drafted (approve it under Eval cases)')
      } else {
        toast.success('Reviewed')
      }
      onClose()
    } catch {
      // the global mutation error toast has already said what went wrong
    }
  }

  const busy = m.review.isPending || m.makeCase.isPending
  return (
    <Sheet open={!!call} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{call?.business}</SheetTitle>
          <SheetDescription>{call && `${dateTime(call.started_at)} · ${duration(call.duration_s)} · ${call.outcome ?? 'completed'}`}</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4 pb-6 text-sm">
          {call?.summary && <p className="rounded-md bg-muted p-3">{call.summary}</p>}
          {q.isLoading && <Skeleton className="h-40" />}
          <div className="space-y-2">
            {transcript.map((t, i) => (
              <div key={i} className={cn('max-w-[85%] rounded-lg px-3 py-2', t.role === 'user' ? 'bg-muted' : 'ml-auto bg-primary/10')}>
                <div className="mb-0.5 text-[11px] uppercase text-muted-foreground">{t.role === 'user' ? 'Caller' : 'Assistant'}</div>
                {t.text}
              </div>
            ))}
            {!q.isLoading && transcript.length === 0 && <p className="text-muted-foreground">Transcript purged.</p>}
          </div>
          <div className="space-y-3 border-t pt-4">
            <div className="flex gap-2">
              <Button variant={verdict === 'good' ? 'default' : 'outline'} onClick={() => setVerdict('good')}><Check /> Good call</Button>
              <Button variant={verdict === 'issue' ? 'destructive' : 'outline'} onClick={() => setVerdict('issue')}><X /> Something went wrong</Button>
            </div>
            {verdict === 'issue' && (
              <>
                <div className="flex flex-wrap gap-1.5">
                  {REVIEW_ISSUES.map((i) => (
                    <Button key={i} size="sm" variant={issues.includes(i) ? 'default' : 'outline'}
                      onClick={() => setIssues(issues.includes(i) ? issues.filter((x) => x !== i) : [...issues, i])}>{i}</Button>
                  ))}
                </div>
                <Field label="What happened" hint="Then fix it: prompt, tool, or a Knowledge entry for this business.">
                  <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
                </Field>
                <Field label="What it should have done">
                  <Textarea rows={2} value={expected} onChange={(e) => setExpected(e.target.value)} />
                </Field>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={makeCase} onChange={(e) => setMakeCase(e.target.checked)} /> Make it an eval test case
                </label>
                {makeCase && (
                  <div className="grid gap-3 rounded-md border p-3">
                    <Field label="Case title"><Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Quoted a price it didn't have" /></Field>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Field label="Must call (tools)" hint="comma-separated"><Input value={mustCall} onChange={(e) => setMustCall(e.target.value)} placeholder="take_message" /></Field>
                      <Field label="Must not call"><Input value={mustNot} onChange={(e) => setMustNot(e.target.value)} placeholder="book_appointment" /></Field>
                    </div>
                    <p className="text-xs text-muted-foreground">“What it should have done” becomes the judge’s rubric. Numbers, e-mails and the caller’s name are redacted; check the draft before approving.</p>
                  </div>
                )}
              </>
            )}
            <Button onClick={() => void submit()} disabled={busy || (makeCase && (title.length < 3 || expected.length < 10))}>
              {busy && <Loader2 className="animate-spin" />} Save review
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}

function Queue() {
  const q = useReviewQueue()
  const stats = useReviewStats()
  const [open, setOpen] = useState<QueuedCall | null>(null)
  const calls = q.data?.calls ?? []
  return (
    <div className="mt-3 space-y-4">
      {stats.data && Object.keys(stats.data.issues).length > 0 && (
        <p className="text-sm text-muted-foreground">
          Issues in the last 30 days: {Object.entries(stats.data.issues).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`).join(' · ')}
        </p>
      )}
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.isLoading && <Skeleton className="h-64" />}
      {!q.isLoading && calls.length === 0 && <EmptyState icon={<ClipboardCheck className="size-8" />} title="Nothing to review" description="Every pilot call and a sample of the rest land here." />}
      {calls.length > 0 && (
        <Card>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>When</TableHead><TableHead>Business</TableHead><TableHead>Outcome</TableHead><TableHead className="text-right">Length</TableHead><TableHead className="text-right">p50</TableHead></TableRow></TableHeader>
              <TableBody>
                {calls.map((c) => (
                  <TableRow key={c.call_id} className="cursor-pointer" onClick={() => setOpen(c)}>
                    <TableCell>{dateTime(c.started_at)}</TableCell>
                    <TableCell>{c.business} {c.pilot && <Badge variant="secondary" className="ml-1">pilot</Badge>}</TableCell>
                    <TableCell>{c.outcome ?? 'completed'}{c.after_hours && <span className="text-xs text-muted-foreground"> · after hours</span>}</TableCell>
                    <TableCell className="text-right tabular-nums">{duration(c.duration_s)}</TableCell>
                    <TableCell className="text-right tabular-nums">{c.latency?.p50_s ? `${c.latency.p50_s.toFixed(1)} s` : '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      <ReviewSheet key={open?.call_id ?? 'none'} call={open} onClose={() => setOpen(null)} />
    </div>
  )
}

function Cases() {
  const q = useEvalCases()
  const m = useReviewMutations()
  const cases = q.data?.cases ?? []
  return (
    <div className="mt-3 space-y-3">
      <p className="text-sm text-muted-foreground">
        Approved cases are written to <code>tests/evals/cases/</code> by <code>python -m scripts.export_eval_cases</code> and replayed against the real LLM on every model or prompt change.
      </p>
      {q.error && <ErrorBanner error={q.error} />}
      {cases.length === 0 && !q.isLoading && <EmptyState icon={<FlaskConical className="size-8" />} title="No eval cases yet" description="Mark a reviewed call as an eval case to start the suite." />}
      {cases.map((c) => (
        <Card key={c.id}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              {c.title}
              <Badge variant={c.status === 'approved' ? 'default' : c.status === 'rejected' ? 'outline' : 'secondary'}>{c.status}</Badge>
            </CardTitle>
            <CardDescription>{c.vertical} · {c.author} · {dateTime(c.created_at)}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <ol className="list-decimal space-y-1 pl-5">{c.caller_turns.map((t, i) => <li key={i}>{t}</li>)}</ol>
            <p><span className="font-medium">Expect:</span> {c.expect.intent}</p>
            {(c.expect.must_call.length > 0 || c.expect.must_not_call.length > 0) && (
              <p className="text-xs text-muted-foreground">
                {c.expect.must_call.length > 0 && `must call ${c.expect.must_call.join(', ')} `}
                {c.expect.must_not_call.length > 0 && `· must not call ${c.expect.must_not_call.join(', ')}`}
              </p>
            )}
            {c.status === 'draft' && (
              <div className="flex gap-2 pt-1">
                <Button size="sm" onClick={() => m.setCaseStatus.mutate({ id: c.id, status: 'approved' })}>Approve</Button>
                <Button size="sm" variant="outline" onClick={() => m.setCaseStatus.mutate({ id: c.id, status: 'rejected' })}>Reject</Button>
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

/** Phase 7, Task 7.1: review transcripts; every failure becomes a fix and an eval case. */
export default function Reviews() {
  const [tab, setTab] = useState('queue')
  return (
    <>
      <PageHeader title="Transcript review" description="Weeks 1–2 of a pilot: read every call. After that: a sample. Each failure → a fix → an eval case." />
      <Tabs value={tab} onValueChange={(v) => setTab(String(v))}>
        <TabsList>
          <TabsTrigger value="queue">Queue</TabsTrigger>
          <TabsTrigger value="cases">Eval cases</TabsTrigger>
        </TabsList>
        <TabsContent value="queue"><Queue /></TabsContent>
        <TabsContent value="cases"><Cases /></TabsContent>
      </Tabs>
    </>
  )
}
