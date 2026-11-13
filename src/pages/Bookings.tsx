import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ArrowDown, ArrowUp, CalendarPlus, RefreshCw, Search, X } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader, Pagination, TableSkeleton } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { BookingStatusBadge, PaymentBadge } from '@/components/app/status'
import { BookingSheet } from '@/components/bookings/BookingSheet'
import { NewBookingDialog } from '@/components/bookings/NewBookingDialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { dateOnly, dateTime, money, sourceLabel, titleCase } from '@/lib/format'
import { useBookings } from '@/lib/hooks'
import { BOOKING_STATUSES, PAYMENT_STATUSES, ROOM_TYPES } from '@/lib/types'
import { useDebounced } from '@/lib/use-debounced'

const ALL = 'all'
const opts = (values: string[], allLabel: string) => [
  { value: ALL, label: allLabel },
  ...values.map((v) => ({ value: v, label: titleCase(v) })),
]

const SORTS = [
  { value: 'created_at', label: 'Newest booked' },
  { value: 'check_in_date', label: 'Check-in date' },
  { value: 'check_out_date', label: 'Check-out date' },
]

export default function Bookings() {
  const [sp] = useSearchParams()
  const [q, setQ] = useState(sp.get('q') ?? '')
  const [status, setStatus] = useState(sp.get('status') ?? ALL)
  const [payment, setPayment] = useState(sp.get('payment') ?? ALL)
  const [roomType, setRoomType] = useState(ALL)
  const [source, setSource] = useState(ALL)
  const [from, setFrom] = useState(sp.get('from') ?? '')
  const [to, setTo] = useState(sp.get('to') ?? '')
  const [sort, setSort] = useState('created_at')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(sp.get('open'))
  const [creating, setCreating] = useState(false)

  const debouncedQ = useDebounced(q)
  const query = useBookings({
    q: debouncedQ.trim() || undefined,
    status: status === ALL ? undefined : status,
    payment_status: payment === ALL ? undefined : payment,
    room_type: roomType === ALL ? undefined : roomType,
    source: source === ALL ? undefined : source,
    date_from: from || undefined,
    date_to: to || undefined,
    sort,
    order,
    page,
    limit: 20,
  })

  const filtersActive = !!(q || from || to || status !== ALL || payment !== ALL || roomType !== ALL || source !== ALL)
  const reset = () => {
    setQ('')
    setFrom('')
    setTo('')
    setStatus(ALL)
    setPayment(ALL)
    setRoomType(ALL)
    setSource(ALL)
    setPage(1)
  }
  const change = <T,>(setter: (v: T) => void) => (v: T) => {
    setter(v)
    setPage(1)
  }

  const data = query.data

  return (
    <>
      <PageHeader
        title="Bookings"
        description="Every reservation made by the AI receptionist or at the desk."
        actions={
          <>
            <Button variant="outline" size="icon" onClick={() => void query.refetch()} aria-label="Refresh">
              <RefreshCw className={query.isFetching ? 'animate-spin' : ''} />
            </Button>
            <Button onClick={() => setCreating(true)}>
              <CalendarPlus /> New booking
            </Button>
          </>
        }
      />

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-end gap-3 border-b p-4">
          <div className="relative min-w-56 flex-1">
            <Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted-foreground" />
            <Input
              className="pl-8"
              placeholder="Search ID, name, phone, email, room…"
              value={q}
              onChange={(e) => change(setQ)(e.target.value)}
            />
          </div>
          <SimpleSelect value={status} onChange={change(setStatus)} options={opts(BOOKING_STATUSES, 'All statuses')} />
          <SimpleSelect value={payment} onChange={change(setPayment)} options={opts(PAYMENT_STATUSES, 'All payments')} />
          <SimpleSelect value={roomType} onChange={change(setRoomType)} options={opts(ROOM_TYPES, 'All room types')} />
          <SimpleSelect
            value={source}
            onChange={change(setSource)}
            options={[
              { value: ALL, label: 'All sources' },
              { value: 'ai_receptionist', label: 'AI receptionist' },
              { value: 'dashboard', label: 'Dashboard' },
            ]}
          />
          <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
            Check-in
            <Input type="date" className="w-36" value={from} onChange={(e) => change(setFrom)(e.target.value)} />
            –
            <Input type="date" className="w-36" value={to} onChange={(e) => change(setTo)(e.target.value)} />
          </div>
          <div className="flex items-center gap-1">
            <SimpleSelect value={sort} onChange={change(setSort)} options={SORTS} />
            <Button
              variant="outline"
              size="icon"
              aria-label="Toggle sort order"
              onClick={() => setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))}
            >
              {order === 'asc' ? <ArrowUp /> : <ArrowDown />}
            </Button>
          </div>
          {filtersActive && (
            <Button variant="ghost" size="sm" onClick={reset}>
              <X /> Clear
            </Button>
          )}
        </div>

        {query.error && (
          <div className="p-4">
            <ErrorBanner error={query.error} onRetry={() => void query.refetch()} />
          </div>
        )}

        {query.isLoading ? (
          <TableSkeleton rows={8} cols={7} />
        ) : data && data.bookings.length === 0 ? (
          <EmptyState
            title="No bookings found"
            description={filtersActive ? 'Try clearing some filters.' : 'New reservations will appear here.'}
          />
        ) : (
          data && (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Booking</TableHead>
                    <TableHead>Guest</TableHead>
                    <TableHead>Room</TableHead>
                    <TableHead>Stay</TableHead>
                    <TableHead className="text-right">Total</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Payment</TableHead>
                    <TableHead>Source</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.bookings.map((b) => (
                    <TableRow key={b.booking_id} className="cursor-pointer" onClick={() => setSelected(b.booking_id)}>
                      <TableCell>
                        <div className="font-mono text-xs font-medium">{b.booking_id}</div>
                        <div className="text-xs text-muted-foreground">{dateTime(b.created_at)}</div>
                      </TableCell>
                      <TableCell>
                        <div className="font-medium">{b.guest_name}</div>
                        <div className="text-xs text-muted-foreground">{b.guest_phone}</div>
                      </TableCell>
                      <TableCell>
                        <div>{b.room_number}</div>
                        <div className="text-xs text-muted-foreground">{titleCase(b.room_type)}</div>
                      </TableCell>
                      <TableCell>
                        <div>
                          {dateOnly(b.check_in_date, 'd MMM')} → {dateOnly(b.check_out_date, 'd MMM yyyy')}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {b.pricing.nights} night{b.pricing.nights === 1 ? '' : 's'} · {b.adults + b.children} guest
                          {b.adults + b.children === 1 ? '' : 's'}
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-medium">{money(b.pricing.final_total, b.pricing.currency)}</TableCell>
                      <TableCell>
                        <BookingStatusBadge status={b.status} />
                      </TableCell>
                      <TableCell>
                        <PaymentBadge status={b.payment_status} />
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{sourceLabel(b.source).split(' (')[0]}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pagination page={data.page} pages={data.pages} total={data.total} onPage={setPage} label="bookings" />
            </>
          )
        )}
      </Card>

      <BookingSheet bookingId={selected} onClose={() => setSelected(null)} />
      <NewBookingDialog open={creating} onOpenChange={setCreating} onCreated={setSelected} />
    </>
  )
}
