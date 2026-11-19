import { Fragment, useMemo, useState } from 'react'
import { addDays, format, parseISO } from 'date-fns'
import { ChevronLeft, ChevronRight, RefreshCw, Wrench } from 'lucide-react'
import { ErrorBanner, PageHeader } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { BookingSheet } from '@/components/bookings/BookingSheet'
import { NewBookingDialog, type BookingPrefill } from '@/components/bookings/NewBookingDialog'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { GRID_COLORS } from '@/lib/colors'
import { titleCase, toISODate } from '@receptionist/shared/lib/format'
import { useOccupancy } from '@/lib/hooks'
import { ROOM_TYPES } from '@/lib/types'
import { cn } from '@receptionist/shared/lib/utils'

const ALL = 'all'

export default function Calendar() {
  // null = let the server pick the hotel's own "today" (browser timezone may differ)
  const [startOverride, setStartOverride] = useState<string | null>(null)
  const [days, setDays] = useState('14')
  const [type, setType] = useState(ALL)
  const [open, setOpen] = useState<string | null>(null)
  const [prefill, setPrefill] = useState<BookingPrefill | null>(null)

  const n = Number(days)
  const q = useOccupancy(startOverride ?? '', n)
  const data = q.data
  const todayIso = data?.today ?? toISODate(new Date())
  const start = startOverride ?? data?.start ?? todayIso
  const setStart = (d: string) => setStartOverride(d === data?.today ? null : d)

  const dates = useMemo(() => Array.from({ length: n }, (_, i) => toISODate(addDays(parseISO(start), i))), [start, n])

  const rooms = useMemo(
    () => (data?.rooms ?? []).filter((r) => type === ALL || r.room_type === type),
    [data, type],
  )
  const groups = useMemo(
    () =>
      ROOM_TYPES.map((t) => ({ type: t, rooms: rooms.filter((r) => r.room_type === t) })).filter((g) => g.rooms.length),
    [rooms],
  )

  const bookingsByRoom = useMemo(() => {
    const map = new Map<string, NonNullable<typeof data>['bookings']>()
    for (const b of data?.bookings ?? []) {
      const list = map.get(b.room_id) ?? []
      list.push(b)
      map.set(b.room_id, list)
    }
    return map
  }, [data])

  // occupied rooms per night, over all rooms (not only the filtered ones)
  const occupiedPerDay = useMemo(
    () =>
      dates.map((d) => {
        const ids = new Set<string>()
        for (const b of data?.bookings ?? []) if (b.check_in_date <= d && b.check_out_date > d) ids.add(b.room_id)
        return ids.size
      }),
    [dates, data],
  )

  const shift = (by: number) => setStart(toISODate(addDays(parseISO(start), by)))
  const colTemplate = `repeat(${n}, minmax(${n > 14 ? 34 : 44}px, 1fr))`
  const LABEL_W = 112

  return (
    <>
      <PageHeader
        title="Availability"
        description="Room-by-day view. Click a booking to open it, or an empty night to start a new one."
        actions={
          <Button variant="outline" size="icon" onClick={() => void q.refetch()} aria-label="Refresh">
            <RefreshCw className={q.isFetching ? 'animate-spin' : ''} />
          </Button>
        }
      />

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <div className="flex items-center gap-1">
            <Button variant="outline" size="icon" onClick={() => shift(-n)} aria-label="Previous">
              <ChevronLeft />
            </Button>
            <Button variant="outline" onClick={() => setStartOverride(null)}>
              Today
            </Button>
            <Button variant="outline" size="icon" onClick={() => shift(n)} aria-label="Next">
              <ChevronRight />
            </Button>
          </div>
          <Input type="date" className="w-40" value={start} onChange={(e) => e.target.value && setStart(e.target.value)} />
          <SimpleSelect
            value={days}
            onChange={setDays}
            options={[
              { value: '7', label: '7 days' },
              { value: '14', label: '14 days' },
              { value: '30', label: '30 days' },
            ]}
          />
          <SimpleSelect
            value={type}
            onChange={setType}
            options={[{ value: ALL, label: 'All room types' }, ...ROOM_TYPES.map((t) => ({ value: t, label: titleCase(t) }))]}
          />
          <div className="ml-auto flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            {Object.entries(GRID_COLORS).map(([k, cls]) => (
              <span key={k} className="flex items-center gap-1.5">
                <span className={cn('size-3 rounded-sm', cls.split(' ')[0])} />
                {titleCase(k)}
              </span>
            ))}
          </div>
        </div>

        {q.error && (
          <div className="p-4">
            <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
          </div>
        )}
        {q.isLoading && <Skeleton className="m-4 h-96" />}

        {data && (
          <div className="max-h-[calc(100vh-17rem)] min-h-64 overflow-auto">
            <div style={{ minWidth: LABEL_W + n * (n > 14 ? 34 : 44) }}>
              {/* header */}
              <div className="sticky top-0 z-20 flex border-b bg-card">
                <div className="sticky left-0 z-30 shrink-0 border-r bg-card px-3 py-2 text-xs font-medium text-muted-foreground" style={{ width: LABEL_W }}>
                  Room
                  <div className="font-normal">Occupied</div>
                </div>
                <div className="grid flex-1" style={{ gridTemplateColumns: colTemplate }}>
                  {dates.map((d, i) => {
                    const dt = parseISO(d)
                    const weekend = [0, 6].includes(dt.getDay())
                    return (
                      <div
                        key={d}
                        className={cn(
                          'border-r px-1 py-1.5 text-center text-xs last:border-r-0',
                          weekend && 'bg-muted/50',
                          d === todayIso && 'bg-primary/10 font-semibold text-primary',
                        )}
                      >
                        <div className="text-muted-foreground">{format(dt, 'EEE')}</div>
                        <div>{format(dt, 'd')}</div>
                        {(i === 0 || dt.getDate() === 1) && <div className="text-[10px] text-muted-foreground">{format(dt, 'MMM')}</div>}
                        <div className="mt-0.5 text-[10px] text-muted-foreground">{occupiedPerDay[i]}</div>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* rows */}
              {groups.length === 0 && <div className="p-8 text-center text-sm text-muted-foreground">No rooms to show.</div>}
              {groups.map((g) => (
                <Fragment key={g.type}>
                  <div className="sticky left-0 border-b bg-muted/40 px-3 py-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    {titleCase(g.type)} · {g.rooms.length}
                  </div>
                  {g.rooms.map((r) => (
                    <div key={r.id} className="flex border-b last:border-b-0">
                      <div
                        className="sticky left-0 z-10 flex shrink-0 items-center gap-1.5 border-r bg-card px-3 py-1.5 text-sm"
                        style={{ width: LABEL_W }}
                      >
                        <span className="font-medium">{r.room_number}</span>
                        <span className="text-xs text-muted-foreground">F{r.floor}</span>
                        {r.is_maintenance && <Wrench className="size-3 text-amber-600" aria-label="Maintenance" />}
                      </div>
                      <div className="relative grid flex-1" style={{ gridTemplateColumns: colTemplate }}>
                        {dates.map((d, i) => (
                          <button
                            key={d}
                            type="button"
                            disabled={r.is_maintenance || d < todayIso}
                            aria-label={`Book room ${r.room_number} on ${d}`}
                            onClick={() => setPrefill({ checkIn: d, roomType: r.room_type, roomId: r.id })}
                            className={cn(
                              'h-9 border-r last:border-r-0 enabled:hover:bg-primary/10',
                              [0, 6].includes(parseISO(d).getDay()) && 'bg-muted/40',
                              d === todayIso && 'bg-primary/5',
                              (r.is_maintenance || d < todayIso) && 'cursor-default',
                              r.is_maintenance && 'bg-[repeating-linear-gradient(45deg,transparent,transparent_4px,var(--muted)_4px,var(--muted)_8px)]',
                            )}
                            style={{ gridColumn: i + 1, gridRow: 1 }}
                          />
                        ))}
                        {(bookingsByRoom.get(r.id) ?? []).map((b) => {
                          const startIdx = Math.max(dates.indexOf(b.check_in_date), b.check_in_date < dates[0] ? 0 : -1)
                          const rawEnd = dates.indexOf(b.check_out_date)
                          const endIdx = rawEnd === -1 ? (b.check_out_date > dates[n - 1] ? n : -1) : rawEnd
                          if (startIdx < 0 || endIdx <= startIdx) return null
                          return (
                            <button
                              key={b.booking_id}
                              type="button"
                              onClick={() => setOpen(b.booking_id)}
                              title={`${b.guest_name} · ${b.check_in_date} → ${b.check_out_date} · ${titleCase(b.status)}`}
                              className={cn(
                                'z-10 m-0.5 truncate rounded-md px-2 text-left text-xs font-medium shadow-sm transition-opacity hover:opacity-85',
                                GRID_COLORS[b.status] ?? 'bg-slate-400 text-white',
                              )}
                              style={{ gridColumn: `${startIdx + 1} / ${endIdx + 1}`, gridRow: 1 }}
                            >
                              {b.guest_name}
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </Fragment>
              ))}
            </div>
          </div>
        )}
      </Card>

      <BookingSheet bookingId={open} onClose={() => setOpen(null)} />
      <NewBookingDialog
        open={!!prefill}
        onOpenChange={(o) => !o && setPrefill(null)}
        prefill={prefill ?? undefined}
        onCreated={setOpen}
      />
    </>
  )
}
