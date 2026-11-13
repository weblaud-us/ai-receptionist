import { toast } from 'sonner'
import { Loader2, Play, RefreshCw } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader, TableSkeleton } from '@/components/app/blocks'
import { TagBadge } from '@/components/app/status'
import { BookingSheet } from '@/components/bookings/BookingSheet'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { dateOnly, dateTime } from '@/lib/format'
import { useReminders, useRunReminders } from '@/lib/hooks'
import type { ReminderRow } from '@/lib/types'
import { useState } from 'react'

export default function Reminders() {
  const q = useReminders()
  const run = useRunReminders()
  const [open, setOpen] = useState<string | null>(null)
  const d = q.data

  return (
    <>
      <PageHeader
        title="Reminders"
        description={
          d
            ? `One WhatsApp reminder is sent the day before check-in. The server checks every ${d.poll_minutes} minutes.`
            : 'Automatic check-in reminders'
        }
        actions={
          <>
            <Button variant="outline" size="icon" onClick={() => void q.refetch()} aria-label="Refresh">
              <RefreshCw className={q.isFetching ? 'animate-spin' : ''} />
            </Button>
            <Button
              disabled={run.isPending || d?.enabled === false}
              onClick={() => run.mutate(undefined, { onSuccess: () => toast.success('Reminder run finished') })}
            >
              {run.isPending ? <Loader2 className="animate-spin" /> : <Play />} Run now
            </Button>
          </>
        }
      />

      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {d && !d.enabled && (
        <div className="mb-4 rounded-lg border border-amber-300/60 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          Reminders are switched off. Enable <code>ENABLE_ONE_DAY_BOOKING_REMINDER</code> and <code>ENABLE_WHATSAPP</code> in the server's
          .env to send them.
        </div>
      )}
      {q.isLoading && <TableSkeleton />}

      {d && (
        <div className="space-y-4">
          <Section title={`Tomorrow's arrivals (${dateOnly(d.reminder_for_date, 'EEE, d MMM')})`} description="Confirmed bookings that will be reminded today." rows={d.tomorrow} onOpen={setOpen} empty="No confirmed arrivals tomorrow." />
          <Section title="Failed" description="Retried automatically on every check until they send or the guest arrives." rows={d.failed} onOpen={setOpen} empty="Nothing has failed." />
          <Section title="Recently sent" rows={d.recent_sent} onOpen={setOpen} empty="No reminders sent yet." />
        </div>
      )}

      <BookingSheet bookingId={open} onClose={() => setOpen(null)} />
    </>
  )
}

function Section({
  title,
  description,
  rows,
  empty,
  onOpen,
}: {
  title: string
  description?: string
  rows: ReminderRow[]
  empty: string
  onOpen: (id: string) => void
}) {
  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b py-4">
        <CardTitle>
          {title} <span className="ml-1 rounded-full bg-muted px-2 py-0.5 text-xs font-normal text-muted-foreground">{rows.length}</span>
        </CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      {rows.length === 0 ? (
        <EmptyState title={empty} className="py-8" />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Booking</TableHead>
              <TableHead>Guest</TableHead>
              <TableHead>Room</TableHead>
              <TableHead>Check-in</TableHead>
              <TableHead>State</TableHead>
              <TableHead>Last attempt</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.booking_id} className="cursor-pointer" onClick={() => onOpen(r.booking_id)}>
                <TableCell className="font-mono text-xs">{r.booking_id}</TableCell>
                <TableCell>
                  <div className="font-medium">{r.guest_name}</div>
                  <div className="text-xs text-muted-foreground">{r.guest_phone}</div>
                </TableCell>
                <TableCell>{r.room_number}</TableCell>
                <TableCell>{dateOnly(r.check_in_date)}</TableCell>
                <TableCell>
                  <TagBadge value={r.state} />
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {r.sent_at ? `Sent ${dateTime(r.sent_at)}` : r.attempted_at ? `Tried ${dateTime(r.attempted_at)}` : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  )
}
