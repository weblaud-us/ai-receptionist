import { useState } from 'react'
import { toast } from 'sonner'
import { Download, Loader2, MessageSquareText, Trash2 } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, KeyValue, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { dateTime, timeAgo, titleCase } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import {
  DEMO_STATUS_LABEL, downloadDemoLeadsCsv, sourceText, talkTime, useDemoLead, useDemoLeadMutations, useDemoLeads,
  type DemoLeadStatus,
} from '@/lib/demo-leads'

const STATUS_STYLE: Record<DemoLeadStatus, string> = {
  new: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
  contacted: 'bg-violet-100 text-violet-800 dark:bg-violet-500/20 dark:text-violet-300',
  qualified: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  not_a_fit: 'bg-muted text-muted-foreground',
}
const REGION_LABEL: Record<string, string> = { au: 'Australia', uk: 'United Kingdom' }
const DAYS = [
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
  { value: '365', label: 'Last year' },
]

function StatusBadge({ status }: { status: DemoLeadStatus }) {
  return <Badge variant="secondary" className={cn('border-0', STATUS_STYLE[status])}>{DEMO_STATUS_LABEL[status] ?? titleCase(status)}</Badge>
}

/** People who tried the landing page's live demo (signed in with Google). Platform administrators only. */
export default function DemoLeads() {
  const [status, setStatus] = useState('all')
  const [days, setDays] = useState('90')
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const q = useDebounced(search, 300)
  const params = { status: status === 'all' ? undefined : status, q, days }
  const leads = useDemoLeads(params)
  const counts = leads.data?.counts
  const off = leads.data && !Object.values(leads.data.enabled).some(Boolean)

  const exportCsv = async () => {
    setExporting(true)
    try {
      await downloadDemoLeadsCsv(params)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Demo leads"
        description="Everyone who signed in with Google and talked to the live demo on the landing page, with what they said."
        actions={<Button variant="outline" disabled={exporting || !leads.data?.leads.length} onClick={exportCsv}>
          {exporting ? <Loader2 className="animate-spin" /> : <Download />} Export CSV
        </Button>}
      />
      {off && (
        <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-700 dark:text-amber-300">
          The live demo is switched off on this server (WEB_DEMO_ENABLED, the demo practices, LiveKit or the Google client id). Existing leads are still shown.
        </p>
      )}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input className="max-w-xs" placeholder="Search name, e-mail or practice" value={search} onChange={(e) => setSearch(e.target.value)} />
        <SimpleSelect size="sm" value={status} onChange={setStatus}
          options={[{ value: 'all', label: 'All leads' },
            ...(Object.keys(DEMO_STATUS_LABEL) as DemoLeadStatus[]).map((s) => ({ value: s, label: `${DEMO_STATUS_LABEL[s]}${counts ? ` (${counts[s] ?? 0})` : ''}` }))]} />
        <SimpleSelect size="sm" value={days} onChange={setDays} options={DAYS} />
      </div>
      {leads.error && <ErrorBanner error={leads.error} />}
      <Card className="gap-0 py-0">
        {leads.isLoading ? <TableSkeleton rows={6} cols={6} /> : (leads.data?.leads.length ?? 0) === 0 ? (
          <EmptyState title="No demo leads yet" icon={<MessageSquareText className="size-8" />}
            description="When someone signs in on the landing page and talks to the demo, they appear here with a summary of the call." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>Person</TableHead><TableHead>Practice</TableHead><TableHead>Status</TableHead><TableHead>Demos</TableHead><TableHead>Talk time</TableHead><TableHead>Last seen</TableHead><TableHead>Source</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {leads.data?.leads.map((l) => (
                <TableRow key={l.id} className="cursor-pointer" onClick={() => setOpen(l.id)}>
                  <TableCell><div className="font-medium">{l.name || '—'}</div><div className="text-xs text-muted-foreground">{l.email}</div></TableCell>
                  <TableCell>
                    <div>{l.practice_name || (l.hosted_domain ? l.hosted_domain : '—')}</div>
                    <div className="text-xs text-muted-foreground">{REGION_LABEL[l.region] ?? l.region.toUpperCase()}</div>
                  </TableCell>
                  <TableCell><StatusBadge status={l.status} /></TableCell>
                  <TableCell className="tabular-nums">{l.demo_count}</TableCell>
                  <TableCell className="tabular-nums">{talkTime(l.talk_seconds)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{timeAgo(l.last_seen_at)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{sourceText(l)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      <LeadSheet id={open} onClose={() => setOpen(null)} />
    </>
  )
}

function LeadSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useDemoLead(id)
  const m = useDemoLeadMutations()
  const lead = q.data?.lead
  const [confirmDelete, setConfirmDelete] = useState(false)

  const setStatus = (status: string) =>
    lead && m.update.mutate({ id: lead.id, status: status as DemoLeadStatus })

  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{lead?.name || lead?.email || 'Demo lead'}</SheetTitle>
          <SheetDescription>{lead ? `${lead.email}${lead.practice_name ? ` · ${lead.practice_name}` : ''}` : ''}</SheetDescription>
        </SheetHeader>
        {q.error && <div className="px-4"><ErrorBanner error={q.error} /></div>}
        {lead && (
          <div className="space-y-5 px-4 pb-6 text-sm">
            <div>
              <KeyValue label="Status">
                <SimpleSelect size="sm" value={lead.status} onChange={setStatus} disabled={m.update.isPending}
                  options={(Object.keys(DEMO_STATUS_LABEL) as DemoLeadStatus[]).map((s) => ({ value: s, label: DEMO_STATUS_LABEL[s] }))} />
              </KeyValue>
              <KeyValue label="Region">{REGION_LABEL[lead.region] ?? lead.region}</KeyValue>
              <KeyValue label="Workspace domain">{lead.hosted_domain || 'Personal Google account'}</KeyValue>
              <KeyValue label="Demos">{lead.demo_count} started · {lead.calls} reached the agent · {talkTime(lead.talk_seconds)}</KeyValue>
              <KeyValue label="First seen">{dateTime(lead.first_seen_at)}</KeyValue>
              <KeyValue label="Came from">{sourceText(lead)}{lead.page ? ` · ${lead.page}` : ''}</KeyValue>
            </div>
            <Notes key={lead.id} id={lead.id} saved={lead.notes ?? ''} />
            <section>
              <h3 className="mb-2 font-medium">Demo calls</h3>
              {(q.data?.sessions.length ?? 0) === 0 ? (
                <p className="text-muted-foreground">Signed in, but no call reached the agent yet.</p>
              ) : q.data?.sessions.map((s) => (
                <details key={s.call_id} className="mb-2 rounded-md border p-3" open={q.data?.sessions[0]?.call_id === s.call_id}>
                  <summary className="cursor-pointer list-none">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{titleCase(s.outcome)} · {talkTime(s.duration_s)}</span>
                      <span className="text-xs text-muted-foreground">{dateTime(s.started_at)}</span>
                    </div>
                    {s.summary && <p className="mt-1 text-xs text-muted-foreground">{s.summary}</p>}
                  </summary>
                  {s.tools_used.length > 0 && <p className="mt-2 font-mono text-[11px] text-muted-foreground">{s.tools_used.join(' → ')}</p>}
                  <div className="mt-2 space-y-1.5">
                    {!s.transcript_available ? (
                      <p className="text-xs text-muted-foreground">The transcript has been deleted (demo calls are kept for a limited time).</p>
                    ) : s.transcript.map((t, i) => (
                      <p key={i} className="text-xs leading-relaxed">
                        <span className={cn('font-medium', t.role === 'assistant' ? 'text-primary' : 'text-foreground')}>
                          {t.role === 'assistant' ? 'Agent' : 'Visitor'}:
                        </span>{' '}{t.text}
                      </p>
                    ))}
                  </div>
                </details>
              ))}
            </section>
            <Button size="sm" variant="outline" className="text-destructive" onClick={() => setConfirmDelete(true)}><Trash2 /> Delete lead</Button>
          </div>
        )}
        <ConfirmDialog open={confirmDelete} onOpenChange={setConfirmDelete} destructive title="Delete this lead?" confirmLabel="Delete lead and calls"
          description="Removes the lead and the transcripts of their demo calls, for example after an erasure request. This can't be undone."
          loading={m.remove.isPending}
          onConfirm={() => lead && m.remove.mutate(lead.id, {
            onSuccess: () => { toast.success('Lead deleted'); setConfirmDelete(false); onClose() },
          })} />
      </SheetContent>
    </Sheet>
  )
}

function Notes({ id, saved }: { id: string; saved: string }) {
  const m = useDemoLeadMutations()
  const [notes, setNotes] = useState(saved)
  const save = () =>
    m.update.mutate({ id, notes }, { onSuccess: () => toast.success('Notes saved') })
  return (
    <Field label="Notes">
      <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Called back, interested in the Growth plan…" />
      <div className="mt-2 flex justify-end">
        <Button size="sm" variant="outline" disabled={m.update.isPending || notes === saved} onClick={save}>Save notes</Button>
      </div>
    </Field>
  )
}
