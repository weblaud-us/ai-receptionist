import { useState } from 'react'
import { Link } from 'react-router-dom'
import { differenceInCalendarDays, parseISO } from 'date-fns'
import { toast } from 'sonner'
import {
  Ban,
  CalendarPlus,
  Check,
  CheckCheck,
  Copy,
  DoorClosed,
  DoorOpen,
  Loader2,
  MessageCircle,
  Pencil,
  UserX,
  X,
} from 'lucide-react'
import { ConfirmDialog, ErrorBanner, Field, KeyValue } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { BookingStatusBadge, PaymentBadge } from '@/components/app/status'
import { Button } from '@receptionist/shared/components/ui/button'
import { Input } from '@receptionist/shared/components/ui/input'
import { Separator } from '@receptionist/shared/components/ui/separator'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { API_BASE } from '@receptionist/shared/lib/api'
import { dateOnly, dateTime, money, sourceLabel, titleCase } from '@receptionist/shared/lib/format'
import {
  useBooking,
  useCancelBooking,
  useSendConfirmation,
  useSetBookingStatus,
  useUpdateBooking,
} from '@/lib/hooks'
import { PAYMENT_STATUSES, type Booking } from '@/lib/types'

function cancellationPolicy(checkIn: string): { label: string; pct: number } {
  const days = differenceInCalendarDays(parseISO(checkIn), new Date())
  if (days < 1) return { label: 'Same-day / past check-in: 100% cancellation fee applies.', pct: 100 }
  if (days < 3) return { label: 'Less than 3 days before arrival: 50% cancellation fee applies.', pct: 50 }
  return { label: 'More than 3 days before arrival: free cancellation.', pct: 0 }
}

export function BookingSheet({ bookingId, onClose }: { bookingId: string | null; onClose: () => void }) {
  return (
    <Sheet open={!!bookingId} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        {bookingId && <Body key={bookingId} bookingId={bookingId} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  )
}

function Body({ bookingId, onClose }: { bookingId: string; onClose: () => void }) {
  const q = useBooking(bookingId)

  if (q.isLoading) {
    return (
      <div className="space-y-3 p-6">
        <SheetTitle className="sr-only">Booking</SheetTitle>
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (q.error || !q.data) {
    return (
      <div className="p-6">
        <SheetTitle className="sr-only">Booking</SheetTitle>
        <ErrorBanner error={q.error ?? new Error('Booking not found')} onRetry={() => void q.refetch()} />
      </div>
    )
  }
  return <Details booking={q.data} onClose={onClose} />
}

function Details({ booking: b, onClose }: { booking: Booking; onClose: () => void }) {
  const setStatus = useSetBookingStatus()
  const cancel = useCancelBooking()
  const update = useUpdateBooking()
  const sendConfirmation = useSendConfirmation()

  const [confirmCancel, setConfirmCancel] = useState(false)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(b.guest_name)
  const [email, setEmail] = useState(b.guest_email ?? '')
  const [requests, setRequests] = useState(b.special_requests ?? '')
  const [note, setNote] = useState('')

  const policy = cancellationPolicy(b.check_in_date)
  const closed = b.status === 'cancelled' || b.status === 'checked_out' || b.status === 'no_show'
  const busy = setStatus.isPending || cancel.isPending

  const move = (status: string, label: string) =>
    setStatus.mutate({ bookingId: b.booking_id, status }, { onSuccess: () => toast.success(label) })

  const icsUrl = b.calendar_token ? `${API_BASE}/bookings/${b.booking_id}/calendar.ics?token=${b.calendar_token}` : ''

  return (
    <>
      <SheetHeader className="border-b">
        <div className="flex items-center gap-2 pr-8">
          <SheetTitle className="font-mono text-lg">{b.booking_id}</SheetTitle>
          <BookingStatusBadge status={b.status} />
        </div>
        <SheetDescription>
          {b.guest_name} · {titleCase(b.room_type)} room {b.room_number}
        </SheetDescription>
      </SheetHeader>

      <div className="space-y-5 p-4">
        {/* ── status actions ── */}
        {!closed && (
          <div className="flex flex-wrap gap-2">
            {b.status === 'pending' && (
              <Button size="sm" disabled={busy} onClick={() => move('confirmed', 'Booking confirmed')}>
                <Check /> Confirm
              </Button>
            )}
            {(b.status === 'confirmed' || b.status === 'pending') && (
              <Button size="sm" disabled={busy} onClick={() => move('checked_in', 'Guest checked in')}>
                <DoorOpen /> Check in
              </Button>
            )}
            {b.status === 'checked_in' && (
              <Button size="sm" disabled={busy} onClick={() => move('checked_out', 'Guest checked out')}>
                <DoorClosed /> Check out
              </Button>
            )}
            {b.status === 'confirmed' && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => move('no_show', 'Marked as no-show')}>
                <UserX /> No-show
              </Button>
            )}
            <Button size="sm" variant="destructive" disabled={busy} onClick={() => setConfirmCancel(true)}>
              <Ban /> Cancel booking
            </Button>
          </div>
        )}

        {/* ── guest ── */}
        <section>
          <div className="mb-1 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Guest</h3>
            {!editing ? (
              <Button size="xs" variant="ghost" onClick={() => setEditing(true)}>
                <Pencil /> Edit
              </Button>
            ) : (
              <div className="flex gap-1">
                <Button
                  size="xs"
                  disabled={update.isPending || !name.trim()}
                  onClick={() =>
                    update.mutate(
                      {
                        bookingId: b.booking_id,
                        guest_name: name.trim(),
                        guest_email: email.trim() || null,
                        special_requests: requests.trim() || null,
                      },
                      {
                        onSuccess: () => {
                          toast.success('Booking updated')
                          setEditing(false)
                        },
                      },
                    )
                  }
                >
                  {update.isPending ? <Loader2 className="animate-spin" /> : <Check />} Save
                </Button>
                <Button size="xs" variant="ghost" onClick={() => setEditing(false)}>
                  <X /> Cancel
                </Button>
              </div>
            )}
          </div>
          {editing ? (
            <div className="space-y-3">
              <Field label="Name">
                <Input value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Email">
                <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
              </Field>
              <Field label="Special requests">
                <Textarea rows={3} value={requests} onChange={(e) => setRequests(e.target.value)} />
              </Field>
            </div>
          ) : (
            <>
              <KeyValue label="Name">{b.guest_name}</KeyValue>
              <KeyValue label="Phone">
                <Link className="text-primary hover:underline" to={`/guests?phone=${encodeURIComponent(b.guest_phone)}`}>
                  {b.guest_phone}
                </Link>
              </KeyValue>
              <KeyValue label="Email">{b.guest_email || '—'}</KeyValue>
              <KeyValue label="Special requests">{b.special_requests || '—'}</KeyValue>
            </>
          )}
        </section>

        <Separator />

        {/* ── stay ── */}
        <section>
          <h3 className="mb-1 text-sm font-semibold">Stay</h3>
          <KeyValue label="Check-in">{dateOnly(b.check_in_date, 'EEE, d MMM yyyy')}</KeyValue>
          <KeyValue label="Check-out">{dateOnly(b.check_out_date, 'EEE, d MMM yyyy')}</KeyValue>
          <KeyValue label="Nights">{b.pricing.nights}</KeyValue>
          <KeyValue label="Room">
            {titleCase(b.room_type)} · {b.room_number}
          </KeyValue>
          <KeyValue label="Guests">
            {b.adults} adult{b.adults === 1 ? '' : 's'}
            {b.children ? `, ${b.children} child${b.children === 1 ? '' : 'ren'}` : ''}
          </KeyValue>
          <KeyValue label="Source">{sourceLabel(b.source)}</KeyValue>
        </section>

        <Separator />

        {/* ── pricing & payment ── */}
        <section>
          <h3 className="mb-1 text-sm font-semibold">Pricing</h3>
          <KeyValue label={`Room (${money(b.pricing.per_night_avg, b.pricing.currency)} × ${b.pricing.nights})`}>
            {money(b.pricing.base_total, b.pricing.currency)}
          </KeyValue>
          {b.pricing.discounts > 0 && (
            <KeyValue label="Discount">− {money(b.pricing.discounts, b.pricing.currency)}</KeyValue>
          )}
          <KeyValue label="Taxes">{money(b.pricing.taxes, b.pricing.currency)}</KeyValue>
          <KeyValue label="Total">
            <span className="text-base">{money(b.pricing.final_total, b.pricing.currency)}</span>
          </KeyValue>
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className="flex items-center gap-2 text-sm text-muted-foreground">
              Payment <PaymentBadge status={b.payment_status} />
            </span>
            <SimpleSelect
              size="sm"
              value={b.payment_status}
              options={PAYMENT_STATUSES.map((p) => ({ value: p, label: titleCase(p) }))}
              disabled={update.isPending}
              onChange={(v) =>
                v !== b.payment_status &&
                update.mutate(
                  { bookingId: b.booking_id, payment_status: v },
                  { onSuccess: () => toast.success(`Payment marked ${v}`) },
                )
              }
            />
          </div>
        </section>

        <Separator />

        {/* ── notes ── */}
        <section>
          <h3 className="mb-2 text-sm font-semibold">Notes</h3>
          {b.booking_notes.length === 0 ? (
            <p className="text-sm text-muted-foreground">No notes yet.</p>
          ) : (
            <ul className="space-y-1.5">
              {b.booking_notes.map((n, i) => (
                <li key={i} className="rounded-md bg-muted px-3 py-2 text-sm">
                  {n}
                </li>
              ))}
            </ul>
          )}
          <form
            className="mt-2 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (!note.trim()) return
              update.mutate(
                { bookingId: b.booking_id, add_note: note.trim() },
                { onSuccess: () => setNote('') },
              )
            }}
          >
            <Input placeholder="Add an internal note…" value={note} onChange={(e) => setNote(e.target.value)} />
            <Button type="submit" variant="outline" disabled={!note.trim() || update.isPending}>
              Add
            </Button>
          </form>
        </section>

        <Separator />

        {/* ── timeline & tools ── */}
        <section>
          <h3 className="mb-1 text-sm font-semibold">Timeline</h3>
          <KeyValue label="Created">{dateTime(b.created_at)}</KeyValue>
          <KeyValue label="Checked in">{dateTime(b.checked_in_at)}</KeyValue>
          <KeyValue label="Checked out">{dateTime(b.checked_out_at)}</KeyValue>
          <KeyValue label="1-day reminder">
            {b.one_day_reminder_sent
              ? `Sent ${dateTime(b.one_day_reminder_sent_at)}`
              : b.one_day_reminder_error
                ? 'Failed'
                : 'Not sent'}
          </KeyValue>
        </section>

        <div className="flex flex-wrap gap-2 pb-2">
          <Button
            size="sm"
            variant="outline"
            disabled={sendConfirmation.isPending || b.status === 'cancelled'}
            onClick={() =>
              sendConfirmation.mutate(b.booking_id, {
                onSuccess: () => toast.success('Confirmation sent on WhatsApp'),
              })
            }
          >
            {sendConfirmation.isPending ? <Loader2 className="animate-spin" /> : <MessageCircle />}
            {b.whatsapp_confirmation_sent ? 'Resend' : 'Send'} WhatsApp confirmation
          </Button>
          {icsUrl && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                void navigator.clipboard.writeText(icsUrl).then(() => toast.success('Calendar link copied'))
              }}
            >
              <Copy /> Copy calendar link
            </Button>
          )}
          {icsUrl && (
            <Button size="sm" variant="outline" nativeButton={false} render={<a href={icsUrl} target="_blank" rel="noreferrer" />}>
              <CalendarPlus /> .ics
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onClose}>
            <CheckCheck /> Close
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title={`Cancel booking ${b.booking_id}?`}
        description={policy.label}
        confirmLabel="Cancel booking"
        destructive
        loading={cancel.isPending}
        onConfirm={() =>
          cancel.mutate(
            { bookingId: b.booking_id, reason: 'Cancelled from dashboard' },
            {
              onSuccess: (r) => {
                setConfirmCancel(false)
                toast.success(
                  r.cancellation_fee > 0
                    ? `Cancelled. Fee due: ${money(r.cancellation_fee, r.currency)} (not charged automatically)`
                    : 'Cancelled — no fee',
                )
              },
            },
          )
        }
      >
        {policy.pct > 0 && (
          <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
            Fee: {money((b.pricing.final_total * policy.pct) / 100, b.pricing.currency)}. The fee is recorded in
            the response only — the system does not charge or refund automatically.
          </p>
        )}
      </ConfirmDialog>
    </>
  )
}
