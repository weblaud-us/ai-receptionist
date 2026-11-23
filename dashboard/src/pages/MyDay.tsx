import { useState } from 'react'
import { AlertTriangle, CalendarClock, CircleCheck, CircleX, PhoneCall, Sparkles, UserPlus } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { cn } from '@receptionist/shared/lib/utils'
import { titleCase } from '@receptionist/shared/lib/format'
import { useTenant } from '@/lib/tenant'
import { APPOINTMENT_STYLE, zoneTime } from '@/lib/tenant-format'
import { useMarkMyDay, useMyDay, useResources } from '@/lib/tenant-hooks'
import type { DayAppointment } from '@/lib/tenant-types'

const SOURCE_LABEL: Record<DayAppointment['source'], string> = {
  ai: 'Booked by the assistant',
  dashboard: 'Booked here',
  pms: 'From the practice diary',
}

function Row({ a, tz, canMark }: { a: DayAppointment; tz: string; canMark: boolean }) {
  const mark = useMarkMyDay()
  const [open, setOpen] = useState(false)
  const more = !!(a.notes || a.last_call_summary || a.triage || a.enquiry)
  return (
    <div className={cn('space-y-2 px-4 py-3', a.status === 'no_show' && 'opacity-60')}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="w-24 font-medium tabular-nums">{zoneTime(a.start, tz)}</span>
        <span className="min-w-40 flex-1">
          <span className="font-medium">{a.patient_name}</span>
          {a.service && <span className="ml-2 text-muted-foreground">{a.service}</span>}
        </span>
        {a.triage && (
          <Badge variant="secondary" className={cn('border-0', a.triage.red_flag || a.triage.severity === 'life_threatening'
            ? 'bg-red-100 text-red-800 dark:bg-red-500/20 dark:text-red-300'
            : 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300')}>
            <AlertTriangle className="size-3" /> {a.triage.severity === 'urgent' ? 'Urgent' : titleCase(a.triage.severity ?? 'Triage')}
          </Badge>
        )}
        {a.first_visit && <Badge variant="outline"><UserPlus className="size-3" /> New patient</Badge>}
        {a.no_shows > 0 && <Badge variant="outline">Missed {a.no_shows} before</Badge>}
        <Badge variant="secondary" className="border-0" title={SOURCE_LABEL[a.source]}>
          {a.source === 'pms' ? 'Diary' : a.source === 'ai' ? 'AI' : 'Dashboard'}
        </Badge>
        <Badge variant="secondary" className={cn('border-0', APPOINTMENT_STYLE[a.status])}>{titleCase(a.status)}</Badge>
        {more && <Button size="xs" variant="ghost" onClick={() => setOpen((o) => !o)}>{open ? 'Less' : 'More'}</Button>}
        {canMark && a.appointment_id && (a.status === 'confirmed' || a.status === 'pending') && (
          <span className="flex gap-1">
            <Button size="xs" variant="outline" disabled={mark.isPending}
              onClick={() => mark.mutate({ id: a.appointment_id!, status: 'completed' })}><CircleCheck /> Seen</Button>
            <Button size="xs" variant="outline" disabled={mark.isPending}
              onClick={() => mark.mutate({ id: a.appointment_id!, status: 'no_show' })}><CircleX /> Didn't arrive</Button>
          </span>
        )}
      </div>
      {open && (
        <div className="ml-0 space-y-1.5 rounded-lg bg-muted/50 p-3 text-sm sm:ml-27">
          {a.triage?.problem && <p><span className="font-medium">On the phone they said: </span>{a.triage.problem}</p>}
          {a.enquiry && (
            <p className="flex items-center gap-1"><Sparkles className="size-3.5" />
              <span className="font-medium">Enquiry:</span> {titleCase(a.enquiry.treatment ?? '')}
              {a.enquiry.timeframe && <span className="text-muted-foreground"> · wants to start {a.enquiry.timeframe.replace(/_/g, ' ')}</span>}
            </p>
          )}
          {a.last_call_summary && (
            <p className="flex gap-1"><PhoneCall className="mt-0.5 size-3.5 shrink-0" /><span><span className="font-medium">Last call: </span>{a.last_call_summary}</span></p>
          )}
          {a.notes && <p><span className="font-medium">Notes: </span>{a.notes}</p>}
        </div>
      )}
    </div>
  )
}

/** Plan E: a dentist's patients today and tomorrow, with what the practice already knows about each. */
export default function MyDay() {
  const { ctx, can } = useTenant()
  const staff = can('staff')
  const resources = useResources(staff)          // a dentist can't list practitioners: they see their own
  const dentists = (resources.data?.resources ?? []).filter((r) => r.active)
  const [day, setDay] = useState<'today' | 'tomorrow'>('today')
  const [picked, setPicked] = useState<string>('')
  const resourceId = staff ? (picked || dentists[0]?.id) : undefined
  const q = useMyDay(day, resourceId)
  const enabled = !staff || !!resourceId
  const tz = q.data?.timezone ?? ctx?.tenant.timezone ?? 'UTC'
  const L = ctx?.vertical.labels

  return (
    <>
      <PageHeader title={staff ? `${L?.resources ?? 'Dentists'}' day` : 'My day'}
        description={staff ? 'Who each dentist is seeing today and tomorrow.' : 'Your patients today and tomorrow, with what the practice knows about each one.'} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {(['today', 'tomorrow'] as const).map((d) => (
          <Button key={d} size="sm" variant={day === d ? 'default' : 'outline'} onClick={() => setDay(d)}>{titleCase(d)}</Button>
        ))}
        <div className="flex-1" />
        {staff && dentists.length > 0 && (
          <SimpleSelect size="sm" value={resourceId ?? ''} onChange={setPicked}
            options={dentists.map((r) => ({ value: r.id, label: r.name }))} />
        )}
      </div>
      {enabled && q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.data?.warning && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
          <AlertTriangle className="size-4" /> {q.data.warning}
        </div>
      )}
      <Card className="gap-0 py-0">
        {q.isLoading || (staff && resources.isLoading) ? <TableSkeleton rows={5} cols={4} /> : (
          q.data && q.data.appointments.length > 0 ? (
            <div className="divide-y">
              {q.data.appointments.map((a, i) => <Row key={a.appointment_id ?? `pms-${i}`} a={a} tz={tz} canMark />)}
            </div>
          ) : (
            <EmptyState icon={<CalendarClock className="size-5" />} title={`No patients ${day}`}
              description={q.data?.pms ? 'Nothing in the practice diary for this dentist.' : 'Nothing booked for this dentist.'} />
          )
        )}
      </Card>
    </>
  )
}
