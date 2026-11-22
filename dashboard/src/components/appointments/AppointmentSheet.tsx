import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { CircleCheck, CircleX, ExternalLink, Loader2, Pencil, PhoneCall, UserRound, UserX } from 'lucide-react'
import { KeyValue } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Separator } from '@receptionist/shared/components/ui/separator'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { titleCase } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useCtx } from '@/lib/tenant'
import { APPOINTMENT_STYLE, zoneDateTime, zoneDay, zoneTime } from '@/lib/tenant-format'
import { useAppointmentMutations, useContact, useTenantCall } from '@/lib/tenant-hooks'
import type { Appointment } from '@/lib/tenant-types'

const sourceName = (s: string) => (s === 'voice' ? 'AI call' : s === 'whatsapp' ? 'WhatsApp' : titleCase(s))

/** Side panel with everything known about one appointment: the reason/notes, the call it came from, and the patient's history. */
export function AppointmentSheet({ appointment, onClose, onCancel }: {
  appointment: Appointment | null
  onClose: () => void
  onCancel: (a: Appointment) => void
}) {
  return (
    <Sheet open={!!appointment} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        {appointment && <Body key={appointment.appointment_id} a={appointment} onClose={onClose} onCancel={onCancel} />}
      </SheetContent>
    </Sheet>
  )
}

function Section({ title, icon, action, children }: { title: string; icon?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="space-y-2 px-4 py-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold [&_svg]:size-4 [&_svg]:text-muted-foreground">{icon}{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

function Body({ a, onClose, onCancel }: { a: Appointment; onClose: () => void; onCancel: (a: Appointment) => void }) {
  const ctx = useCtx()
  const tz = ctx.tenant.timezone
  const L = ctx.vertical.labels
  const m = useAppointmentMutations()
  const call = useTenantCall(a.call_id ?? null)
  const contact = useContact(a.contact_id ?? null)
  const [editing, setEditing] = useState(false)
  const [notes, setNotes] = useState(a.notes ?? '')

  const saveNotes = () =>
    m.update.mutate({ id: a.appointment_id, notes: notes.trim() }, {
      onSuccess: () => { toast.success('Notes saved'); setEditing(false) },
    })

  const setStatus = (status: 'completed' | 'no_show') =>
    m.update.mutate({ id: a.appointment_id, status }, {
      onSuccess: () => { toast.success(status === 'completed' ? 'Marked attended' : 'Marked no-show'); onClose() },
    })

  const c = contact.data
  const history = (c?.appointments ?? []).filter((x) => x.appointment_id !== a.appointment_id).slice(0, 5)
  const savedNotes = a.notes?.trim()

  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="pr-8">{a.name}</SheetTitle>
        <SheetDescription>
          {zoneDay(a.start, tz)} · {zoneTime(a.start, tz)} – {zoneTime(a.end, tz)}
        </SheetDescription>
        <div className="flex flex-wrap gap-1.5 pt-1">
          <Badge variant="secondary" className={cn('border-0', APPOINTMENT_STYLE[a.status])}>{titleCase(a.status)}</Badge>
          <Badge variant="secondary" className="border-0">Booked via {sourceName(a.source)}</Badge>
          {c?.do_not_contact && <Badge variant="destructive">Do not contact</Badge>}
        </div>
      </SheetHeader>

      <Section
        title="Reason & notes"
        icon={<Pencil />}
        action={!editing && (
          <Button size="xs" variant="ghost" onClick={() => { setNotes(a.notes ?? ''); setEditing(true) }}>
            {savedNotes ? 'Edit' : 'Add notes'}
          </Button>
        )}
      >
        {editing ? (
          <div className="space-y-2">
            <Textarea autoFocus rows={4} maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder="Main problem, symptoms, anxiety, medical alerts, anything the team should know" />
            <div className="flex justify-end gap-2">
              <Button size="xs" variant="ghost" onClick={() => setEditing(false)}>Discard</Button>
              <Button size="xs" disabled={m.update.isPending} onClick={saveNotes}>
                {m.update.isPending && <Loader2 className="animate-spin" />} Save
              </Button>
            </div>
          </div>
        ) : savedNotes ? (
          <p className="rounded-md bg-muted/60 px-3 py-2 text-sm whitespace-pre-wrap">{savedNotes}</p>
        ) : (
          <p className="text-sm text-muted-foreground">No reason or notes recorded for this {L.appointment.toLowerCase()}.</p>
        )}
      </Section>

      <Separator />
      <Section title="Details">
        <div className="divide-y">
          <KeyValue label={L.service}>{a.service_name || '—'}</KeyValue>
          <KeyValue label={L.resource}>{a.resource_name || '—'}</KeyValue>
          {a.party_size ? <KeyValue label="Party size">{a.party_size}</KeyValue> : null}
          <KeyValue label="Phone"><a className="font-mono hover:underline" href={`tel:${a.phone}`}>{a.phone}</a></KeyValue>
          {a.email && <KeyValue label="E-mail"><a className="hover:underline" href={`mailto:${a.email}`}>{a.email}</a></KeyValue>}
        </div>
      </Section>

      {a.call_id && (
        <>
          <Separator />
          <Section
            title="From the call"
            icon={<PhoneCall />}
            action={
              <Button size="xs" variant="ghost" nativeButton={false} render={<Link to={`/calls?call=${a.call_id}`} />}>
                Transcript <ExternalLink />
              </Button>
            }
          >
            {call.isLoading ? <Skeleton className="h-12 w-full" /> : call.data?.summary ? (
              <>
                <p className="text-sm whitespace-pre-wrap">{call.data.summary}</p>
                <p className="text-xs text-muted-foreground">{zoneDateTime(call.data.started_at, tz)}</p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">No summary for this call. Open the transcript to read it in full.</p>
            )}
          </Section>
        </>
      )}

      {a.contact_id && (
        <>
          <Separator />
          <Section
            title={`${L.contact} history`}
            icon={<UserRound />}
            action={
              <Button size="xs" variant="ghost" nativeButton={false} render={<Link to={`/contacts?contact=${a.contact_id}`} />}>
                Full record <ExternalLink />
              </Button>
            }
          >
            {contact.isLoading ? <Skeleton className="h-16 w-full" /> : c ? (
              <div className="space-y-3">
                <div className="grid grid-cols-3 gap-2 text-center">
                  <Stat label={L.appointments} value={c.stats?.appointments ?? 0} />
                  <Stat label="Calls" value={c.stats?.calls ?? 0} />
                  <Stat label="No-shows" value={c.stats?.no_shows ?? 0} warn={(c.stats?.no_shows ?? 0) > 0} />
                </div>
                {c.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1">{c.tags.map((t) => <Badge key={t} variant="secondary">{t}</Badge>)}</div>
                )}
                {c.notes?.trim() && (
                  <div>
                    <div className="mb-1 text-xs font-medium text-muted-foreground">{L.contact} notes</div>
                    <p className="rounded-md bg-muted/60 px-3 py-2 text-sm whitespace-pre-wrap">{c.notes}</p>
                  </div>
                )}
                {history.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-medium text-muted-foreground">Other {L.appointments.toLowerCase()}</div>
                    <ul className="divide-y rounded-md border text-sm">
                      {history.map((h) => (
                        <li key={h.appointment_id} className="space-y-0.5 px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="tabular-nums">{zoneDay(h.start, tz)} · {zoneTime(h.start, tz)}</span>
                            <Badge variant="secondary" className={cn('border-0', APPOINTMENT_STYLE[h.status])}>{titleCase(h.status)}</Badge>
                          </div>
                          <div className="text-muted-foreground">{h.service_name || '—'}</div>
                          {h.notes?.trim() && <div className="text-xs text-muted-foreground">{h.notes}</div>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No {L.contact.toLowerCase()} record found.</p>
            )}
          </Section>
        </>
      )}

      {a.status === 'confirmed' && (
        <div className="sticky bottom-0 mt-auto flex flex-wrap gap-2 border-t bg-background px-4 py-3">
          <Button size="sm" variant="outline" disabled={m.update.isPending}
            className="text-emerald-700 hover:bg-emerald-50 hover:text-emerald-800 dark:text-emerald-400 dark:hover:bg-emerald-950"
            onClick={() => setStatus('completed')}><CircleCheck /> Attended</Button>
          <Button size="sm" variant="outline" disabled={m.update.isPending}
            className="text-amber-700 hover:bg-amber-50 hover:text-amber-800 dark:text-amber-400 dark:hover:bg-amber-950"
            onClick={() => setStatus('no_show')}><UserX /> No-show</Button>
          <Button size="sm" variant="outline" className="ml-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => onCancel(a)}><CircleX /> Cancel</Button>
        </div>
      )}
    </>
  )
}

function Stat({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <div className="rounded-md border px-2 py-1.5">
      <div className={cn('text-lg font-semibold tabular-nums', warn && 'text-orange-600 dark:text-orange-400')}>{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  )
}
