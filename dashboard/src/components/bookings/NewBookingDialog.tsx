import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { addDays, parseISO } from 'date-fns'
import { Loader2, Search } from 'lucide-react'
import { EmptyState, ErrorBanner, Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { addDaysISO, isValidPhone, money, nights as countNights, normalizePhone, titleCase, toISODate } from '@receptionist/shared/lib/format'
import { useAvailability, useCreateBooking } from '@/lib/hooks'
import { ROOM_TYPES } from '@/lib/types'
import { cn } from '@receptionist/shared/lib/utils'

export interface BookingPrefill {
  checkIn?: string
  checkOut?: string
  roomType?: string
  roomId?: string
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  prefill?: BookingPrefill
  onCreated?: (bookingId: string) => void
}

export function NewBookingDialog({ open, onOpenChange, prefill, onCreated }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        {/* mounted only while open, so every opening starts from a clean form */}
        {open && <Form prefill={prefill} onClose={() => onOpenChange(false)} onCreated={onCreated} />}
      </DialogContent>
    </Dialog>
  )
}

function Form({
  prefill,
  onClose,
  onCreated,
}: {
  prefill?: BookingPrefill
  onClose: () => void
  onCreated?: (id: string) => void
}) {
  const today = toISODate(new Date())
  const initialIn = prefill?.checkIn ?? today
  const initialOut = prefill?.checkOut ?? toISODate(addDays(parseISO(initialIn), 1))
  const [checkIn, setCheckIn] = useState(initialIn)
  const [checkOut, setCheckOut] = useState(initialOut)
  const [adults, setAdults] = useState(2)
  const [children, setChildren] = useState(0)
  const [roomType, setRoomType] = useState(prefill?.roomType ?? '')
  // Opened from a specific room/night (e.g. the calendar): search straight away.
  const [searched, setSearched] = useState<Record<string, string | number> | null>(() =>
    prefill?.roomId
      ? {
          check_in_date: initialIn,
          check_out_date: initialOut,
          adults: 2,
          children: 0,
          ...(prefill.roomType ? { room_type: prefill.roomType } : {}),
        }
      : null,
  )
  const [roomId, setRoomId] = useState<string | null>(prefill?.roomId ?? null)

  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [requests, setRequests] = useState('')
  const [sendWhatsapp, setSendWhatsapp] = useState(true)

  const create = useCreateBooking()
  const availability = useAvailability(searched ?? {}, !!searched)

  const stayNights = countNights(checkIn, checkOut)
  const dateError =
    checkIn < today ? 'Check-in cannot be in the past' : stayNights < 1 ? 'Check-out must be after check-in' : ''

  const rooms = useMemo(() => availability.data?.rooms ?? [], [availability.data])
  const selected = useMemo(() => rooms.find((r) => r.room_id === roomId) ?? null, [rooms, roomId])
  const normalizedPhone = normalizePhone(phone)
  const phoneOk = isValidPhone(normalizedPhone)
  const canCreate = !!selected && name.trim().length > 1 && phoneOk && !create.isPending

  function search() {
    // `selected` is derived from the fresh results, so a stale room id simply stops matching.
    setSearched({
      check_in_date: checkIn,
      check_out_date: checkOut,
      adults,
      children,
      ...(roomType ? { room_type: roomType } : {}),
    })
  }

  function submit() {
    if (!selected) return
    create.mutate(
      {
        booking: {
          guest_name: name.trim(),
          guest_phone: normalizedPhone,
          guest_email: email.trim() || null,
          room_id: selected.room_id,
          check_in_date: checkIn,
          check_out_date: checkOut,
          adults,
          children,
          special_requests: requests.trim() || null,
        },
        sendWhatsapp,
      },
      {
        onSuccess: (r) => {
          toast.success(`Booking ${r.booking_id} created — room ${r.room_number}`)
          if (sendWhatsapp && r.whatsapp_sent === false) {
            toast.warning(`WhatsApp confirmation not sent: ${r.whatsapp_error ?? 'unknown error'}`)
          }
          onCreated?.(r.booking_id)
          onClose()
        },
      },
    )
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>New booking</DialogTitle>
        <DialogDescription>Find an available room, then add the guest's details.</DialogDescription>
      </DialogHeader>

      {/* 1 — stay */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold">1 · Stay</h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Check-in">
            <Input
              type="date"
              min={today}
              value={checkIn}
              onChange={(e) => {
                const v = e.target.value
                setCheckIn(v)
                if (v && countNights(v, checkOut) < 1) setCheckOut(addDaysISO(v, 1))
              }}
            />
          </Field>
          <Field label="Check-out">
            <Input type="date" min={checkIn} value={checkOut} onChange={(e) => setCheckOut(e.target.value)} />
          </Field>
          <Field label="Adults">
            <Input type="number" min={1} max={10} value={adults} onChange={(e) => setAdults(Math.max(1, +e.target.value || 1))} />
          </Field>
          <Field label="Children">
            <Input type="number" min={0} max={10} value={children} onChange={(e) => setChildren(Math.max(0, +e.target.value || 0))} />
          </Field>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Room type" className="w-48">
            <SimpleSelect
              className="w-full"
              value={roomType || 'any'}
              onChange={(v) => setRoomType(v === 'any' ? '' : v)}
              options={[{ value: 'any', label: 'Any type' }, ...ROOM_TYPES.map((t) => ({ value: t, label: titleCase(t) }))]}
            />
          </Field>
          <Button onClick={search} disabled={!!dateError || availability.isFetching}>
            {availability.isFetching ? <Loader2 className="animate-spin" /> : <Search />}
            Check availability
          </Button>
          {!dateError && <span className="pb-2 text-sm text-muted-foreground">{stayNights} night{stayNights === 1 ? '' : 's'}</span>}
          {dateError && <span className="pb-2 text-sm text-destructive">{dateError}</span>}
        </div>
      </section>

      {/* 2 — rooms */}
      {searched && (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">2 · Choose a room</h3>
          <ErrorBanner error={availability.error} />
          {availability.data?.error && <ErrorBanner error={new Error(availability.data.error)} />}
          {availability.data && !availability.data.error && rooms.length === 0 && (
            <div>
              <EmptyState title="No rooms available" description="Nothing matches those dates and guest numbers." className="py-6" />
              {availability.data.alternative_dates.length > 0 && (
                <div className="flex flex-wrap items-center justify-center gap-2 text-sm">
                  <span className="text-muted-foreground">Try:</span>
                  {availability.data.alternative_dates.map((a) => (
                    <Button
                      key={a.check_in_date}
                      size="xs"
                      variant="outline"
                      onClick={() => {
                        setCheckIn(a.check_in_date)
                        setCheckOut(a.check_out_date)
                        setSearched(null)
                      }}
                    >
                      {a.check_in_date} → {a.check_out_date} ({a.available_rooms} rooms)
                    </Button>
                  ))}
                </div>
              )}
            </div>
          )}
          {rooms.length > 0 && (
            <div className="grid max-h-64 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
              {rooms.map((r) => (
                <button
                  type="button"
                  key={r.room_id}
                  onClick={() => setRoomId(r.room_id)}
                  className={cn(
                    'rounded-lg border p-3 text-left text-sm transition-colors hover:bg-muted',
                    roomId === r.room_id && 'border-primary bg-primary/5 ring-2 ring-primary/30',
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">Room {r.room_number}</span>
                    <span className="text-xs text-muted-foreground">Floor {r.floor}</span>
                  </div>
                  <div className="text-muted-foreground">
                    {titleCase(r.room_type)} · {titleCase(r.bed_type)} bed · sleeps {r.capacity}
                  </div>
                  <div className="mt-1 flex items-baseline justify-between">
                    <span>{money(r.price_per_night, r.currency)}/night</span>
                    <span className="font-semibold">{money(r.total_price, r.currency)} total</span>
                  </div>
                </button>
              ))}
            </div>
          )}
          {rooms.length > 0 && <p className="text-xs text-muted-foreground">Totals include tax and any long-stay discount.</p>}
        </section>
      )}

      {/* 3 — guest */}
      {selected && (
        <section className="space-y-3">
          <h3 className="text-sm font-semibold">3 · Guest</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Full name">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Rahim Uddin" />
            </Field>
            <Field
              label="Phone (with country code)"
              hint={phone && !phoneOk ? 'Use international format, e.g. +8801712345678' : undefined}
            >
              <Input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+8801712345678"
                aria-invalid={!!phone && !phoneOk}
              />
            </Field>
            <Field label="Email (optional)">
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Special requests (optional)">
              <Textarea rows={1} value={requests} onChange={(e) => setRequests(e.target.value)} />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={sendWhatsapp} onCheckedChange={(v) => setSendWhatsapp(v === true)} />
            Send WhatsApp confirmation to the guest
          </label>
        </section>
      )}

      <div className="sticky -bottom-4 z-10 -mx-4 -mb-4 flex items-center justify-between gap-3 border-t bg-popover p-4">
        <div className="text-sm">
          {selected && (
            <>
              <span className="text-muted-foreground">Total · </span>
              <span className="text-base font-semibold">{money(selected.total_price, selected.currency)}</span>
            </>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button disabled={!canCreate} onClick={submit}>
            {create.isPending && <Loader2 className="animate-spin" />}
            Create booking
          </Button>
        </div>
      </div>
    </>
  )
}
