import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { CalendarClock, Check, ChevronLeft, ChevronRight, Loader2, Plus, UserX, X } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, PageHeader, TableSkeleton } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { addDaysISO, dateOnly, isValidPhone, normalizePhone, titleCase } from '@/lib/format'
import { useCtx } from '@/lib/tenant'
import { APPOINTMENT_STYLE, zoneDateKey, zoneTime, zoneToday } from '@/lib/tenant-format'
import { useAppointmentMutations, useAppointments, useResources, useServices, useSlots } from '@/lib/tenant-hooks'
import type { Appointment } from '@/lib/tenant-types'
import { cn } from '@/lib/utils'

export default function Appointments() {
  const ctx = useCtx()
  const tz = ctx.tenant.timezone
  const L = ctx.vertical.labels
  const [start, setStart] = useState(() => zoneToday(tz))
  const [resource, setResource] = useState('all')
  const [creating, setCreating] = useState(false)
  const [cancelling, setCancelling] = useState<Appointment | null>(null)
  const end = addDaysISO(start, 6)
  const q = useAppointments({ date_from: start, date_to: end, resource_id: resource === 'all' ? undefined : resource })
  const resources = useResources()
  const m = useAppointmentMutations()

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDaysISO(start, i)), [start])
  const byDay = useMemo(() => {
    const out: Record<string, Appointment[]> = {}
    for (const a of q.data?.appointments ?? []) (out[zoneDateKey(a.start, tz)] ??= []).push(a)
    return out
  }, [q.data, tz])

  const setStatus = (a: Appointment, status: string) =>
    m.update.mutate({ id: a.appointment_id, status }, { onSuccess: () => toast.success(`Marked ${titleCase(status).toLowerCase()}`) })

  return (
    <>
      <PageHeader
        title={L.appointments}
        description={`Everything booked by your assistant, your team or online. Times are in ${tz}.`}
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus /> New {L.appointment.toLowerCase()}
          </Button>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button size="icon-sm" variant="outline" aria-label="Previous week" onClick={() => setStart(addDaysISO(start, -7))}><ChevronLeft /></Button>
        <Button size="sm" variant="outline" onClick={() => setStart(zoneToday(tz))}>Today</Button>
        <Button size="icon-sm" variant="outline" aria-label="Next week" onClick={() => setStart(addDaysISO(start, 7))}><ChevronRight /></Button>
        <span className="text-sm font-medium">{dateOnly(start, 'd MMM')} – {dateOnly(end, 'd MMM yyyy')}</span>
        <div className="flex-1" />
        <SimpleSelect
          size="sm"
          value={resource}
          onChange={setResource}
          options={[{ value: 'all', label: `All ${L.resources.toLowerCase()}` },
            ...(resources.data?.resources ?? []).filter((r) => r.active).map((r) => ({ value: r.id, label: r.name }))]}
        />
      </div>
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <Card className="gap-0 py-0">
        {q.isLoading ? (
          <TableSkeleton rows={7} cols={4} />
        ) : (
          <div className="divide-y">
            {days.map((day) => {
              const rows = byDay[day] ?? []
              const isToday = day === zoneToday(tz)
              return (
                <div key={day} className="flex flex-col gap-2 px-4 py-3 sm:flex-row">
                  <div className={cn('w-32 shrink-0 text-sm font-medium', isToday && 'text-primary')}>
                    {dateOnly(day, 'EEE d MMM')}
                    {isToday && <span className="ml-1 text-xs">· today</span>}
                  </div>
                  <div className="flex-1 space-y-2">
                    {rows.length === 0 && <div className="text-sm text-muted-foreground">Nothing booked</div>}
                    {rows.map((a) => (
                      <div key={a.appointment_id}
                        className={cn('flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-sm', a.status === 'cancelled' && 'opacity-50')}>
                        <span className="w-28 font-medium tabular-nums">{zoneTime(a.start, tz)} – {zoneTime(a.end, tz)}</span>
                        <span className="min-w-32 flex-1">
                          <span className="font-medium">{a.name}</span>
                          <span className="ml-2 text-muted-foreground">{a.phone}</span>
                          {a.notes && <span className="block text-xs text-muted-foreground">{a.notes}</span>}
                        </span>
                        <span>{a.service_name}{a.party_size ? ` · ${a.party_size} people` : ''}</span>
                        <span className="text-muted-foreground">{a.resource_name}</span>
                        <Badge variant="secondary" className="border-0">{a.source === 'voice' ? 'AI call' : a.source === 'whatsapp' ? 'WhatsApp' : titleCase(a.source)}</Badge>
                        <Badge variant="secondary" className={cn('border-0', APPOINTMENT_STYLE[a.status])}>{titleCase(a.status)}</Badge>
                        {a.status === 'confirmed' && (
                          <span className="flex gap-1">
                            <Button size="icon-xs" variant="ghost" title="Completed" onClick={() => setStatus(a, 'completed')}><Check /></Button>
                            <Button size="icon-xs" variant="ghost" title="No-show" onClick={() => setStatus(a, 'no_show')}><UserX /></Button>
                            <Button size="icon-xs" variant="ghost" title="Cancel" onClick={() => setCancelling(a)}><X /></Button>
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        )}
        {!q.isLoading && (q.data?.appointments.length ?? 0) === 0 && (
          <EmptyState className="border-t" title="A quiet week" icon={<CalendarClock className="size-8" />}
            description="Bookings your assistant makes appear here instantly." />
        )}
      </Card>

      <NewAppointmentDialog open={creating} onOpenChange={setCreating} />
      <ConfirmDialog
        open={!!cancelling}
        onOpenChange={(o) => !o && setCancelling(null)}
        title={`Cancel this ${L.appointment.toLowerCase()}?`}
        description={cancelling ? `${cancelling.name}, ${zoneTime(cancelling.start, tz)} — the time becomes bookable again.` : ''}
        confirmLabel="Cancel it"
        destructive
        loading={m.update.isPending}
        onConfirm={() =>
          cancelling &&
          m.update.mutate({ id: cancelling.appointment_id, status: 'cancelled' }, { onSuccess: () => setCancelling(null) })
        }
      />
    </>
  )
}

function NewAppointmentDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const ctx = useCtx()
  const tz = ctx.tenant.timezone
  const L = ctx.vertical.labels
  const services = useServices()
  const resources = useResources()
  const m = useAppointmentMutations()
  const [serviceId, setServiceId] = useState('')
  const [resourceId, setResourceId] = useState('any')
  const [day, setDay] = useState(() => zoneToday(tz))
  const [slot, setSlot] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [notes, setNotes] = useState('')
  const active = (services.data?.services ?? []).filter((s) => s.active)
  const svc = serviceId || active[0]?.id || ''
  const slots = useSlots({ service_id: svc || undefined, day, resource_id: resourceId === 'any' ? undefined : resourceId }, open && !!svc)
  const picked = slots.data?.slots.find((s) => s.slot_id === slot)
  const normalized = normalizePhone(phone)

  const reset = () => {
    setSlot(null)
    setName('')
    setPhone('')
    setNotes('')
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New {L.appointment.toLowerCase()}</DialogTitle>
          <DialogDescription>Only open times are shown — the same rules your assistant follows.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={L.service}>
            <SimpleSelect value={svc} onChange={(v) => { setServiceId(v); setSlot(null) }}
              options={active.map((s) => ({ value: s.id, label: `${s.name} (${s.duration_min} min)` }))} />
          </Field>
          <Field label={L.resource}>
            <SimpleSelect value={resourceId} onChange={(v) => { setResourceId(v); setSlot(null) }}
              options={[{ value: 'any', label: 'Anyone' }, ...(resources.data?.resources ?? []).filter((r) => r.active).map((r) => ({ value: r.id, label: r.name }))]} />
          </Field>
          <Field label="Day" className="sm:col-span-2">
            <Input type="date" value={day} min={zoneToday(tz)} onChange={(e) => { setDay(e.target.value); setSlot(null) }} />
          </Field>
        </div>
        <div className="max-h-40 overflow-y-auto">
          {slots.isLoading && <Loader2 className="mx-auto size-5 animate-spin text-muted-foreground" />}
          {slots.data && slots.data.slots.length === 0 && <p className="text-sm text-muted-foreground">No open times that day.</p>}
          <div className="flex flex-wrap gap-1.5">
            {slots.data?.slots
              .filter((s, i, arr) => resourceId !== 'any' || arr.findIndex((x) => x.start === s.start) === i)
              .map((s) => (
                <Button key={s.slot_id} size="xs" variant={slot === s.slot_id ? 'default' : 'outline'} onClick={() => setSlot(s.slot_id)}>
                  {zoneTime(s.start, tz)}
                </Button>
              ))}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Phone" hint={phone && !isValidPhone(normalized) ? 'International format, e.g. +15125550100' : undefined}>
            <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1…" />
          </Field>
          <Field label="Notes" className="sm:col-span-2"><Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          <Button
            disabled={!picked || !name.trim() || !isValidPhone(normalized) || m.create.isPending}
            onClick={() =>
              picked &&
              m.create.mutate(
                { service_id: svc, resource_id: resourceId === 'any' ? undefined : picked.resource_id, start: picked.start,
                  name: name.trim(), phone: normalized, notes },
                { onSuccess: () => { toast.success('Booked'); onOpenChange(false); reset() } },
              )
            }
          >
            {m.create.isPending && <Loader2 className="animate-spin" />} Book {picked ? zoneTime(picked.start, tz) : ''}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
