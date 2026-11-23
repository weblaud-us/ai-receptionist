import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { AppointmentSheet } from '@/components/appointments/AppointmentSheet'
import { ArrowLeftRight, CalendarClock, ChevronLeft, ChevronRight, CircleCheck, CircleX, FileText, Loader2, Plus, UserCheck, UserX, WifiOff } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { addDaysISO, dateOnly, isValidPhone, normalizePhone, titleCase } from '@receptionist/shared/lib/format'
import { regionFor } from '@/lib/region'
import { useCtx } from '@/lib/tenant'
import { APPOINTMENT_STYLE, inZone, zoneDateKey, zoneDay, zoneTime, zoneToday } from '@/lib/tenant-format'
import { useAppointmentMutations, useAppointments, useContacts, useFreePractitioners, useResources, useServices, useSlots } from '@/lib/tenant-hooks'
import type { Appointment, Contact, Slot } from '@/lib/tenant-types'
import { cn } from '@receptionist/shared/lib/utils'

export default function Appointments() {
  const ctx = useCtx()
  const tz = ctx.tenant.timezone
  const L = ctx.vertical.labels
  const [start, setStart] = useState(() => zoneToday(tz))
  const [resource, setResource] = useState('all')
  const [creating, setCreating] = useState(false)
  const [cancelling, setCancelling] = useState<Appointment | null>(null)
  const [moving, setMoving] = useState<Appointment | null>(null)
  const [viewingId, setViewingId] = useState<string | null>(null)
  const end = addDaysISO(start, 6)
  const q = useAppointments({ date_from: start, date_to: end, resource_id: resource === 'all' ? undefined : resource })
  // Read from the live list so saved notes / status changes show in the open sheet straight away.
  const viewing = q.data?.appointments.find((a) => a.appointment_id === viewingId) ?? null
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
                          <button type="button" className="font-medium hover:underline" onClick={() => setViewingId(a.appointment_id)}>{a.name}</button>
                          <span className="ml-2 text-muted-foreground">{a.phone}</span>
                          {a.notes && <span className="block truncate text-xs text-muted-foreground">{a.notes}</span>}
                        </span>
                        <span>{a.service_name}{a.party_size ? ` · ${a.party_size} people` : ''}</span>
                        <span className="text-muted-foreground">{a.resource_name}</span>
                        <Badge variant="secondary" className="border-0">{a.source === 'voice' ? 'AI call' : a.source === 'whatsapp' ? 'WhatsApp' : titleCase(a.source)}</Badge>
                        <Badge variant="secondary" className={cn('border-0', APPOINTMENT_STYLE[a.status])}>{titleCase(a.status)}</Badge>
                        {a.status !== 'cancelled' && a.confirmation?.status && CONFIRMATION_LABEL[a.confirmation.status] && (
                          <span className="text-xs text-muted-foreground">{CONFIRMATION_LABEL[a.confirmation.status]}</span>
                        )}
                        <Button size="xs" variant="outline" title="See reason, notes, call summary and patient history"
                          onClick={() => setViewingId(a.appointment_id)}><FileText /> Details</Button>
                        {a.status === 'confirmed' && (
                          <span className="flex gap-1">
                            <Button size="xs" variant="outline" title="Mark as completed — the patient attended"
                              className="text-emerald-700 hover:bg-emerald-50 hover:text-emerald-800 dark:text-emerald-400 dark:hover:bg-emerald-950"
                              onClick={() => setStatus(a, 'completed')}><CircleCheck /> Attended</Button>
                            <Button size="xs" variant="outline" title="Mark as no-show — the patient didn't turn up"
                              className="text-amber-700 hover:bg-amber-50 hover:text-amber-800 dark:text-amber-400 dark:hover:bg-amber-950"
                              onClick={() => setStatus(a, 'no_show')}><UserX /> No-show</Button>
                            {(resources.data?.resources ?? []).filter((r) => r.active).length > 1 && (
                              <Button size="xs" variant="outline" title={`Same time, another ${L.resource.toLowerCase()}`}
                                onClick={() => setMoving(a)}><ArrowLeftRight /> Change {L.resource.toLowerCase()}</Button>
                            )}
                            <Button size="xs" variant="outline" title={`Cancel this ${L.appointment.toLowerCase()}`}
                              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                              onClick={() => setCancelling(a)}><CircleX /> Cancel</Button>
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

      <AppointmentSheet appointment={viewing} onClose={() => setViewingId(null)} onCancel={setCancelling} />
      <NewAppointmentDialog open={creating} onOpenChange={setCreating} />
      <ChangeResourceDialog appointment={moving} onClose={() => setMoving(null)} />
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
          m.update.mutate({ id: cancelling.appointment_id, status: 'cancelled' }, { onSuccess: () => { setCancelling(null); setViewingId(null) } })
        }
      />
    </>
  )
}

/** Same time, another dentist (plan E3): the new one is booked first, then the old booking is cancelled
 * (in Cliniko / Dentally too). The server refuses if they aren't free then. */
function ChangeResourceDialog({ appointment, onClose }: { appointment: Appointment | null; onClose: () => void }) {
  const ctx = useCtx()
  const tz = ctx.tenant.timezone
  const L = ctx.vertical.labels
  const free = useFreePractitioners(appointment?.appointment_id)
  const m = useAppointmentMutations()
  const [to, setTo] = useState('')
  // Only who is actually free at that time (the server re-checks when moving).
  const options = (free.data?.practitioners ?? []).map((r) => ({ value: r._id, label: r.name }))
  const close = () => { setTo(''); onClose() }
  return (
    <Dialog open={!!appointment} onOpenChange={(o) => !o && close()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Change {L.resource.toLowerCase()}</DialogTitle>
          <DialogDescription>
            {appointment ? `${appointment.name}, ${zoneDay(appointment.start, tz)} at ${zoneTime(appointment.start, tz)}, now with ${appointment.resource_name ?? 'nobody'}.` : ''}
            {' '}The time stays the same.
          </DialogDescription>
        </DialogHeader>
        <Field label={`New ${L.resource.toLowerCase()}`}>
          {free.isLoading ? (
            <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline size-4 animate-spin" />Checking who's free…</p>
          ) : options.length ? (
            <SimpleSelect value={to} onChange={setTo} placeholder="Choose" options={options} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {free.isError ? "Couldn't check the diary. Try again in a moment." : `No other ${L.resource.toLowerCase()} is free at that time.`}
            </p>
          )}
        </Field>
        <DialogFooter>
          <Button variant="outline" onClick={close}>Keep as is</Button>
          <Button disabled={!to || m.reassign.isPending} onClick={() => appointment && m.reassign.mutate(
            { id: appointment.appointment_id, resource_id: to },
            { onSuccess: (r) => { toast.success(`Moved to ${r.resource_name ?? 'the new ' + L.resource.toLowerCase()}`); close() } })}>
            {m.reassign.isPending && <Loader2 className="animate-spin" />} Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** What the day-before confirmation call found (Phase 2.5). */
const CONFIRMATION_LABEL: Record<string, string> = {
  calling: 'Calling to confirm…', confirmed: 'Confirmed by phone', rescheduled: 'Moved on the confirmation call',
  cancelled: 'Cancelled on the confirmation call', voicemail: 'Voicemail left', no_answer: 'No answer to confirmation call',
  no_decision: 'Answered, not confirmed', failed: "Confirmation call didn't go through",
}

/** How many days the picker loads at once. Matches the API's `days` cap. */
const WINDOW_DAYS = 14
const PARTS = ['Morning', 'Afternoon', 'Evening'] as const

function partOfDay(iso: string, tz: string): (typeof PARTS)[number] {
  const hour = Number(inZone(iso, tz, { hour: 'numeric', hourCycle: 'h23' }))
  return hour < 12 ? 'Morning' : hour < 17 ? 'Afternoon' : 'Evening'
}

function NewAppointmentDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const ctx = useCtx()
  const tz = ctx.tenant.timezone
  const L = ctx.vertical.labels
  const region = regionFor(ctx.tenant.country)
  const today = zoneToday(tz)
  const services = useServices()
  const resources = useResources()
  const m = useAppointmentMutations()

  const [serviceId, setServiceId] = useState('')
  const [resourceId, setResourceId] = useState('any')
  const [windowStart, setWindowStart] = useState(today)
  const [day, setDay] = useState<string | null>(null)
  const [slot, setSlot] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [notes, setNotes] = useState('')
  const [contact, setContact] = useState<Contact | null>(null)
  const [searching, setSearching] = useState(false)

  const active = (services.data?.services ?? []).filter((s) => s.active)
  const svc = serviceId || active[0]?.id || ''
  const service = active.find((s) => s.id === svc)
  const activeResources = (resources.data?.resources ?? []).filter((r) => r.active)

  // ── Availability: one request for the whole two-week window ──────────────
  const slots = useSlots(
    { service_id: svc || undefined, day: windowStart, days: WINDOW_DAYS, resource_id: resourceId === 'any' ? undefined : resourceId },
    open && !!svc,
  )
  const windowDays = useMemo(() => Array.from({ length: WINDOW_DAYS }, (_, i) => addDaysISO(windowStart, i)), [windowStart])
  const byDay = useMemo(() => {
    const out: Record<string, Slot[]> = {}
    for (const s of slots.data?.slots ?? []) (out[zoneDateKey(s.start, tz)] ??= []).push(s)
    return out
  }, [slots.data, tz])
  // The chosen day, or the first day with openings when nothing (or a now-full day) is chosen.
  const selectedDay = day && byDay[day]?.length ? day : windowDays.find((d) => byDay[d]?.length) ?? null
  const daySlots = selectedDay ? byDay[selectedDay] ?? [] : []
  const picked = daySlots.find((s) => s.slot_id === slot)
  const windowEmpty = !!slots.data && slots.data.slots.length === 0

  // ── Patient lookup: search by name, or by phone when no name is typed ────
  const phoneDigits = phone.replace(/\D/g, '').replace(/^0/, '')
  const query = name.trim().length >= 2 ? name.trim() : phoneDigits.length >= 3 ? phoneDigits : ''
  const debouncedQuery = useDebounced(query)
  const matches = useContacts({ q: debouncedQuery, limit: 5 }, open && !contact && debouncedQuery.length > 0)
  const suggestions = searching && !contact && debouncedQuery ? matches.data?.contacts ?? [] : []

  const normalized = normalizePhone(phone, region.dial)
  const phoneOk = isValidPhone(normalized)
  const emailOk = !email.trim() || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())

  const changeSearch = () => {
    setSlot(null)
    setDay(null)
  }
  const pickContact = (c: Contact) => {
    setContact(c)
    setName(c.name ?? '')
    setPhone(c.phone)
    setEmail(c.email ?? '')
    setSearching(false)
    // Book with the patient's regular dentist by default (still changeable), as the assistant does.
    const regular = c.preferred_resource_id
    if (resourceId === 'any' && regular && activeResources.some((r) => r.id === regular)) {
      setResourceId(regular)
      changeSearch()
    }
  }
  const reset = () => {
    setWindowStart(zoneToday(tz))
    setDay(null)
    setSlot(null)
    setName('')
    setPhone('')
    setEmail('')
    setNotes('')
    setContact(null)
    setSearching(false)
  }
  const close = () => {
    onOpenChange(false)
    reset()
  }

  const book = () =>
    picked &&
    m.create.mutate(
      { service_id: svc, resource_id: resourceId === 'any' ? undefined : picked.resource_id, start: picked.start,
        name: name.trim(), phone: normalized, email: email.trim() || undefined, notes },
      {
        onSuccess: () => {
          toast.success(`Booked ${name.trim()} for ${zoneDay(picked.start, tz)}, ${zoneTime(picked.start, tz)}`)
          close()
        },
        // The error toast comes from the global handler; drop a time someone else just took.
        onError: () => {
          setSlot(null)
          void slots.refetch()
        },
      },
    )

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? onOpenChange(true) : close())}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New {L.appointment.toLowerCase()}</DialogTitle>
          <DialogDescription>Only open times are shown — the same rules your assistant follows. Times are in {tz}.</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={L.service}>
            <SimpleSelect value={svc} onChange={(v) => { setServiceId(v); changeSearch() }}
              options={active.map((s) => ({
                value: s.id,
                label: `${s.name} · ${s.duration_min} min${s.price?.display ? ` · ${s.price.display}` : ''}`,
              }))} />
          </Field>
          <Field label={L.resource}>
            <SimpleSelect value={resourceId} onChange={(v) => { setResourceId(v); changeSearch() }}
              options={[{ value: 'any', label: `Any ${L.resource.toLowerCase()}` },
                ...activeResources.map((r) => ({ value: r.id, label: r.name }))]} />
          </Field>
        </div>

        {/* Day strip */}
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">Day</span>
            <Button size="icon-xs" variant="outline" aria-label="Earlier days" disabled={windowStart <= today}
              onClick={() => { const s = addDaysISO(windowStart, -WINDOW_DAYS); setWindowStart(s < today ? today : s); changeSearch() }}>
              <ChevronLeft />
            </Button>
            <span className="text-xs text-muted-foreground tabular-nums">
              {dateOnly(windowStart, 'd MMM')} – {dateOnly(addDaysISO(windowStart, WINDOW_DAYS - 1), 'd MMM')}
            </span>
            <Button size="icon-xs" variant="outline" aria-label="Later days"
              onClick={() => { setWindowStart(addDaysISO(windowStart, WINDOW_DAYS)); changeSearch() }}>
              <ChevronRight />
            </Button>
            <div className="flex-1" />
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Jump to
              <Input type="date" className="h-7 w-auto text-xs" min={today} value={selectedDay ?? windowStart}
                onChange={(e) => {
                  const v = e.target.value
                  if (!v || v < today) return
                  setWindowStart(v)
                  setDay(v)
                  setSlot(null)
                }} />
            </label>
          </div>
          <div className="flex gap-1.5 overflow-x-auto pb-1">
            {windowDays.map((d) => {
              const count = byDay[d]?.length ?? 0
              const loading = slots.isLoading
              return (
                <button key={d} type="button" disabled={loading || count === 0}
                  onClick={() => { setDay(d); setSlot(null) }}
                  className={cn(
                    'flex w-14 shrink-0 flex-col items-center rounded-lg border px-1 py-1.5 text-xs transition-colors',
                    d === selectedDay ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-muted',
                    !loading && count === 0 && 'cursor-not-allowed opacity-40',
                  )}>
                  <span className="font-medium">{dateOnly(d, 'EEE')}</span>
                  <span className="text-sm font-semibold tabular-nums">{dateOnly(d, 'd')}</span>
                  <span className={cn('tabular-nums', d === selectedDay ? 'text-primary-foreground/80' : 'text-muted-foreground')}>
                    {loading ? '·' : count ? `${count} open` : '—'}
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        {/* Times */}
        <div className="min-h-16 space-y-3">
          {slots.error ? (
            <ErrorBanner error={slots.error} onRetry={() => void slots.refetch()} />
          ) : slots.fetchStatus === 'paused' ? (
            <div className="flex items-center gap-2 rounded-lg border p-3 text-sm text-muted-foreground">
              <WifiOff className="size-4" /> You're offline. Open times will load when the connection is back.
            </div>
          ) : slots.isLoading ? (
            <div className="flex flex-wrap gap-1.5">
              {Array.from({ length: 12 }, (_, i) => <Skeleton key={i} className="h-7 w-16" />)}
            </div>
          ) : windowEmpty ? (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed p-3 text-sm text-muted-foreground">
              No open times between {dateOnly(windowStart, 'd MMM')} and {dateOnly(addDaysISO(windowStart, WINDOW_DAYS - 1), 'd MMM')}.
              <Button size="xs" variant="outline" onClick={() => { setWindowStart(addDaysISO(windowStart, WINDOW_DAYS)); changeSearch() }}>
                Look further
              </Button>
            </div>
          ) : (
            PARTS.map((part) => {
              const rows = daySlots.filter((s) => partOfDay(s.start, tz) === part)
              if (!rows.length) return null
              return (
                <div key={part} className="space-y-1.5">
                  <div className="text-xs font-medium text-muted-foreground">{part}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {rows.map((s) => (
                      <Button key={s.slot_id} size="xs" variant={slot === s.slot_id ? 'default' : 'outline'}
                        className="h-auto min-h-7 flex-col gap-0 py-1 tabular-nums" onClick={() => setSlot(s.slot_id)}>
                        {zoneTime(s.start, tz)}
                        {resourceId === 'any' && s.resource && (
                          <span className={cn('text-[10px] font-normal', slot === s.slot_id ? 'opacity-80' : 'text-muted-foreground')}>
                            {s.resource}
                          </span>
                        )}
                      </Button>
                    ))}
                  </div>
                </div>
              )
            })
          )}
        </div>

        {/* Patient */}
        <div className="relative grid gap-3 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} autoComplete="off" placeholder="Type to find an existing patient"
              onChange={(e) => { setName(e.target.value); setContact(null); setSearching(true) }}
              onFocus={() => setSearching(true)} onBlur={() => setSearching(false)}
              onKeyDown={(e) => e.key === 'Escape' && suggestions.length > 0 && (e.preventDefault(), setSearching(false))} />
          </Field>
          <Field label="Phone" hint={phone && !phoneOk ? `Include the area code, e.g. ${region.phoneExample}` : undefined}>
            <Input value={phone} inputMode="tel" autoComplete="off" placeholder={region.phoneExample} aria-invalid={!!phone && !phoneOk}
              onChange={(e) => { setPhone(e.target.value); setContact(null); setSearching(true) }}
              onFocus={() => setSearching(true)} onBlur={() => setSearching(false)}
              onKeyDown={(e) => e.key === 'Escape' && suggestions.length > 0 && (e.preventDefault(), setSearching(false))} />
          </Field>
          {suggestions.length > 0 && (
            <div className="absolute inset-x-0 top-[4.25rem] z-50 overflow-hidden rounded-lg border bg-popover shadow-md" role="listbox">
              {suggestions.map((c) => (
                <button key={c.id} type="button" role="option" aria-selected={false}
                  className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-muted"
                  // mousedown, not click: keeps focus so the input's blur doesn't close the list first.
                  onMouseDown={(e) => { e.preventDefault(); pickContact(c) }}>
                  <span className="flex-1 font-medium">{c.name || 'No name'}</span>
                  <span className="text-muted-foreground tabular-nums">{c.phone}</span>
                  {!!c.stats?.appointments && <span className="text-xs text-muted-foreground">{c.stats.appointments} visits</span>}
                </button>
              ))}
            </div>
          )}
          {contact && (
            <div className="flex items-center gap-2 text-xs sm:col-span-2">
              <Badge variant="secondary" className="gap-1 border-0">
                <UserCheck className="size-3" /> Existing patient
                {contact.stats?.appointments ? ` · ${contact.stats.appointments} visits` : ''}
                {contact.stats?.no_shows ? ` · ${contact.stats.no_shows} no-shows` : ''}
              </Badge>
              {contact.do_not_contact && <Badge variant="secondary" className="border-0 bg-amber-100 text-amber-900">Do not contact</Badge>}
              <Button size="xs" variant="ghost" onClick={() => { setContact(null); setName(''); setPhone(''); setEmail('') }}>
                Clear
              </Button>
            </div>
          )}
          <Field label="Email (optional)" hint={!emailOk ? 'Check the email address' : undefined}>
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={!emailOk} />
          </Field>
          <Field label="Notes">
            <Textarea rows={1} className="min-h-9" value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder="Reason for visit, anything the team should know" />
          </Field>
        </div>

        {picked && service && (
          <div className="rounded-lg bg-muted px-3 py-2 text-sm">
            <span className="font-medium">{service.name}</span>
            {picked.resource && <> with <span className="font-medium">{picked.resource}</span></>}
            {' · '}{zoneDay(picked.start, tz)}, {zoneTime(picked.start, tz)} – {zoneTime(picked.end, tz)}
            {name.trim() && <> · for <span className="font-medium">{name.trim()}</span></>}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={close}>Close</Button>
          <Button disabled={!picked || !name.trim() || !phoneOk || !emailOk || m.create.isPending} onClick={book}>
            {m.create.isPending && <Loader2 className="animate-spin" />}
            {picked ? `Book ${zoneTime(picked.start, tz)}` : 'Pick a time'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
