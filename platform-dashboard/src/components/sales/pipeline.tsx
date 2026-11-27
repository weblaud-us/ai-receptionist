import { useState } from 'react'
import { toast } from 'sonner'
import { CalendarClock, Check, Download, Mail, RotateCcw } from 'lucide-react'
import { EmptyState, ErrorBanner } from '@receptionist/shared/components/blocks'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { dateTime, timeAgo } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { LeadSheet } from '@/components/sales/leads'
import { useCan } from '@/lib/roles'
import { DealBoard, MyWorkCard } from '@/components/sales/crm-board'
import {
  downloadSalesCsv, useFollowUpMutations, useMeetings, useOutbox, useTasks
} from '@/lib/sales'

/** Everyone past the first call, the follow-ups that are due, the meetings booked, and email that didn't go out. */
export function Pipeline() {
  const can = useCan()
  const [open, setOpen] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const exportCsv = async () => {
    setExporting(true)
    try { await downloadSalesCsv('leads') } catch (e) { toast.error(e instanceof Error ? e.message : 'Export failed') } finally { setExporting(false) }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted-foreground">Deals move from qualified to won or lost. Leads still being called are on the Leads tab.</p>
        <div className="flex-1" />
        {can('manager') && <Button size="sm" variant="outline" disabled={exporting} onClick={() => void exportCsv()}><Download /> Export leads</Button>}
      </div>
      <DealBoard onOpenLead={setOpen} />
      <div className="grid gap-4 lg:grid-cols-2">
        <MyWorkCard onOpenLead={setOpen} />
        <TasksCard onOpen={setOpen} />
        <MeetingsCard onOpen={setOpen} />
      </div>
      <OutboxCard />
      <LeadSheet id={open} onClose={() => setOpen(null)} />
    </div>
  )
}

function TasksCard({ onOpen }: { onOpen: (id: string) => void }) {
  const q = useTasks()
  const m = useFollowUpMutations()
  const [now] = useState(() => Date.now())
  return (
    <Card>
      <CardHeader><CardTitle>Follow-ups to do</CardTitle><CardDescription>The agent adds one when someone is interested; add your own from a lead.</CardDescription></CardHeader>
      <CardContent className="space-y-1">
        {q.error ? <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
          : q.isLoading ? <p className="text-sm text-muted-foreground">Loading…</p>
          : (q.data?.tasks.length ?? 0) === 0 ? <EmptyState title="Nothing to do" /> : q.data?.tasks.map((t) => {
          const overdue = !!t.due_at && new Date(t.due_at).getTime() < now
          return (
            <div key={t.id} className="flex items-center gap-2 border-b py-1.5 text-sm last:border-0">
              <Button size="icon-xs" variant="outline" aria-label="Done" disabled={m.updateTask.isPending}
                onClick={() => m.updateTask.mutate({ id: t.id, done: true }, { onSuccess: () => toast.success('Done') })}><Check /></Button>
              <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onOpen(t.lead_id)}>
                <span className="block truncate">{t.title}</span>
                <span className="block truncate text-xs text-muted-foreground">{t.lead?.business_name || t.lead?.phone}</span>
              </button>
              {t.due_at && <span className={cn('shrink-0 text-xs', overdue ? 'font-medium text-destructive' : 'text-muted-foreground')}>{overdue ? 'overdue · ' : ''}{dateTime(t.due_at)}</span>}
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

function MeetingsCard({ onOpen }: { onOpen: (id: string) => void }) {
  const q = useMeetings()
  const [now] = useState(() => Date.now())
  return (
    <Card>
      <CardHeader><CardTitle>Meetings</CardTitle><CardDescription>Booked by the agent on your sales calendar, from a week ago to a month ahead.</CardDescription></CardHeader>
      <CardContent className="space-y-1">
        {q.error ? <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
          : q.isLoading ? <p className="text-sm text-muted-foreground">Loading…</p>
          : (q.data?.meetings.length ?? 0) === 0 ? <EmptyState title="No meetings booked" /> : q.data?.meetings.map((mt) => (
          <button key={mt.appointment_id} type="button" disabled={!mt.lead} onClick={() => mt.lead && onOpen(mt.lead.id)}
            className={cn('flex w-full items-center gap-2 border-b py-1.5 text-left text-sm last:border-0', new Date(mt.start).getTime() < now && 'opacity-60')}>
            <CalendarClock className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{mt.lead?.business_name || mt.name}</span>
              <span className="block truncate text-xs text-muted-foreground">{mt.name} · {mt.email}</span>
            </span>
            <span className="shrink-0 text-xs">{dateTime(mt.start)}</span>
          </button>
        ))}
      </CardContent>
    </Card>
  )
}

function OutboxCard() {
  const q = useOutbox()
  const m = useFollowUpMutations()
  const can = useCan()
  if ((q.data?.emails.length ?? 0) === 0 || !can('manager')) return null
  return (
    <Card className="border-amber-500/40">
      <CardHeader><CardTitle className="flex items-center gap-2"><Mail className="size-4" /> Emails that didn't go out</CardTitle><CardDescription>Tried three times. Retry, or send it yourself and mark it sent.</CardDescription></CardHeader>
      <CardContent className="space-y-1">
        {q.data?.emails.map((e) => (
          <div key={e.id} className="flex flex-wrap items-center gap-2 border-b py-1.5 text-sm last:border-0">
            <span className="min-w-0 flex-1"><span className="block truncate font-medium">{e.subject}</span><span className="text-xs text-muted-foreground">{e.to} · {timeAgo(e.created_at)}</span></span>
            <Button size="xs" variant="outline" onClick={() => m.retryEmail.mutate(e.id, { onSuccess: (r) => toast[r.email.status === 'sent' ? 'success' : 'message'](r.email.status === 'sent' ? 'Sent' : 'Still not sent') })}><RotateCcw /> Retry</Button>
            <Button size="xs" variant="ghost" onClick={() => m.markEmailSent.mutate(e.id)}>I sent it</Button>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
