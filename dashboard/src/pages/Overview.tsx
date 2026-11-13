import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  AlertTriangle,
  ArrowRight,
  BedDouble,
  CalendarCheck,
  DoorClosed,
  DoorOpen,
  MessageSquare,
  PhoneCall,
  RefreshCw,
  TrendingUp,
  Users,
} from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { BookingStatusBadge } from '@/components/app/status'
import { BookingSheet } from '@/components/bookings/BookingSheet'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { compactMoney, dateOnly, money, titleCase } from '@/lib/format'
import { useSetBookingStatus, useStats } from '@/lib/hooks'
import type { MiniBooking, Stats } from '@/lib/types'

const COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']

function StatCard({
  label,
  value,
  sub,
  icon,
  children,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  icon: ReactNode
  children?: ReactNode
}) {
  return (
    <Card size="sm">
      <CardContent className="space-y-1">
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          {label}
          <span className="text-muted-foreground/70 [&_svg]:size-4">{icon}</span>
        </div>
        <div className="text-2xl font-semibold tracking-tight">{value}</div>
        {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
        {children}
      </CardContent>
    </Card>
  )
}

const PERIODS = [
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
]

export default function Overview() {
  const [days, setDays] = useState('30')
  const [open, setOpen] = useState<string | null>(null)
  const q = useStats(Number(days))
  const s = q.data

  return (
    <>
      <PageHeader
        title="Overview"
        description={s ? `Today is ${dateOnly(s.hotel_today, 'EEEE, d MMMM yyyy')}` : 'Hotel performance at a glance'}
        actions={
          <>
            <SimpleSelect value={days} onChange={setDays} options={PERIODS} />
            <Button variant="outline" size="icon" onClick={() => void q.refetch()} aria-label="Refresh">
              <RefreshCw className={q.isFetching ? 'animate-spin' : ''} />
            </Button>
          </>
        }
      />

      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.isLoading && <LoadingGrid />}
      {s && (
        <div className="space-y-6">
          <Attention s={s} />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Occupancy today"
              value={`${s.rooms.occupancy_pct}%`}
              icon={<BedDouble />}
              sub={`${s.rooms.occupied} of ${s.rooms.sellable} sellable rooms${s.rooms.maintenance ? ` · ${s.rooms.maintenance} in maintenance` : ''}`}
            >
              <Progress value={s.rooms.occupancy_pct} className="mt-2" />
            </StatCard>
            <StatCard
              label="Today"
              value={
                <span className="text-lg">
                  {s.today.arrivals.length} <span className="text-sm font-normal text-muted-foreground">arriving</span> ·{' '}
                  {s.today.departures.length} <span className="text-sm font-normal text-muted-foreground">leaving</span>
                </span>
              }
              icon={<CalendarCheck />}
              sub={`${s.today.in_house} guest${s.today.in_house === 1 ? '' : 's'} in house`}
            />
            <StatCard
              label={`Revenue (${s.period_days}d)`}
              value={compactMoney(s.revenue.period_total, s.currency)}
              icon={<TrendingUp />}
              sub={`Avg booking ${money(s.revenue.avg_booking_value, s.currency)}`}
            />
            <StatCard
              label={`Bookings (${s.period_days}d)`}
              value={s.bookings.period_total}
              icon={<CalendarCheck />}
              sub={`${s.bookings.period_cancelled} cancelled · ${s.bookings.cancellation_rate_pct}% rate`}
            />
            <StatCard
              label="AI conversations"
              value={s.conversations.period_total}
              icon={<MessageSquare />}
              sub={`${s.conversations.converted} led to a booking · ${s.conversations.conversion_pct}% conversion`}
            />
            <StatCard
              label="Channels"
              value={
                <span className="text-lg">
                  {s.conversations.by_channel.voice ?? 0} <span className="text-sm font-normal text-muted-foreground">calls</span> ·{' '}
                  {s.conversations.by_channel.whatsapp ?? 0}{' '}
                  <span className="text-sm font-normal text-muted-foreground">chats</span>
                </span>
              }
              icon={<PhoneCall />}
              sub={
                s.conversations.live_calls > 0 ? (
                  <Link to="/calls" className="font-medium text-emerald-600 hover:underline">
                    {s.conversations.live_calls} live call{s.conversations.live_calls === 1 ? '' : 's'} now
                  </Link>
                ) : (
                  'No live calls'
                )
              }
            />
            <StatCard
              label="Guests"
              value={s.guests.total}
              icon={<Users />}
              sub={`${s.guests.new_in_period} new in the last ${s.period_days} days`}
            />
            <StatCard
              label="Rooms"
              value={s.rooms.total}
              icon={<BedDouble />}
              sub={`${s.rooms.available} available tonight`}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Revenue &amp; bookings</CardTitle>
                <CardDescription>By the day the booking was made</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={s.series} margin={{ left: 0, right: 8, top: 8 }}>
                      <defs>
                        <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.35} />
                          <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis
                        dataKey="date"
                        tickFormatter={(d: string) => dateOnly(d, 'd MMM')}
                        tickLine={false}
                        axisLine={false}
                        minTickGap={32}
                        fontSize={12}
                      />
                      <YAxis
                        yAxisId="rev"
                        tickFormatter={(v: number) => compactMoney(v, '').trim()}
                        tickLine={false}
                        axisLine={false}
                        width={48}
                        fontSize={12}
                      />
                      <YAxis yAxisId="cnt" orientation="right" allowDecimals={false} hide />
                      <ChartTooltip
                        content={({ active, payload, label }) =>
                          active && payload?.length ? (
                            <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
                              <div className="mb-1 font-medium">{dateOnly(String(label), 'EEE, d MMM')}</div>
                              <div>Revenue: {money(Number(payload.find((p) => p.dataKey === 'revenue')?.value ?? 0), s.currency)}</div>
                              <div>Bookings: {payload.find((p) => p.dataKey === 'bookings')?.value}</div>
                            </div>
                          ) : null
                        }
                      />
                      <Area yAxisId="rev" dataKey="revenue" stroke="var(--chart-1)" strokeWidth={2} fill="url(#rev)" />
                      <Area
                        yAxisId="cnt"
                        dataKey="bookings"
                        stroke="var(--chart-2)"
                        strokeWidth={2}
                        fill="none"
                        dot={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-2 flex gap-4 text-xs text-muted-foreground">
                  <Legend color="var(--chart-1)" label="Revenue" />
                  <Legend color="var(--chart-2)" label="Bookings" />
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Arrivals, next 7 days</CardTitle>
                <CardDescription>Confirmed &amp; pending check-ins</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={s.upcoming_check_ins}>
                      <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="date" tickFormatter={(d: string) => dateOnly(d, 'EEE')} tickLine={false} axisLine={false} fontSize={12} />
                      <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={24} fontSize={12} />
                      <ChartTooltip
                        cursor={{ fill: 'var(--muted)' }}
                        content={({ active, payload }) =>
                          active && payload?.length ? (
                            <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
                              {dateOnly(String(payload[0].payload.date), 'EEE, d MMM')}: {payload[0].value} check-in
                              {payload[0].value === 1 ? '' : 's'}
                            </div>
                          ) : null
                        }
                      />
                      <Bar dataKey="check_ins" fill="var(--chart-3)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Where bookings come from</CardTitle>
                <CardDescription>Excludes cancelled · last {s.period_days} days</CardDescription>
              </CardHeader>
              <CardContent>
                {s.bookings.by_source.length === 0 ? (
                  <EmptyState title="No bookings in this period" className="py-8" />
                ) : (
                  <div className="flex items-center gap-6">
                    <div className="size-40 shrink-0">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie data={s.bookings.by_source} dataKey="count" nameKey="name" innerRadius={42} outerRadius={70} paddingAngle={2}>
                            {s.bookings.by_source.map((_, i) => (
                              <Cell key={i} fill={COLORS[i % COLORS.length]} stroke="none" />
                            ))}
                          </Pie>
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                    <ul className="flex-1 space-y-2 text-sm">
                      {s.bookings.by_source.map((r, i) => (
                        <li key={r.name} className="flex items-center justify-between gap-2">
                          <Legend color={COLORS[i % COLORS.length]} label={r.name === 'ai_receptionist' ? 'AI receptionist' : titleCase(r.name)} />
                          <span className="text-muted-foreground">
                            {r.count} · {compactMoney(r.revenue, s.currency)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Revenue by room type</CardTitle>
                <CardDescription>Last {s.period_days} days</CardDescription>
              </CardHeader>
              <CardContent>
                {s.bookings.by_room_type.length === 0 ? (
                  <EmptyState title="No bookings in this period" className="py-8" />
                ) : (
                  <div className="h-44">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={s.bookings.by_room_type.map((r) => ({ ...r, name: titleCase(r.name) }))} layout="vertical" margin={{ left: 16 }}>
                        <XAxis type="number" hide />
                        <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} width={84} fontSize={12} />
                        <ChartTooltip
                          cursor={{ fill: 'var(--muted)' }}
                          content={({ active, payload }) =>
                            active && payload?.length ? (
                              <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
                                {payload[0].payload.name}: {money(Number(payload[0].value), s.currency)} · {payload[0].payload.count} bookings
                              </div>
                            ) : null
                          }
                        />
                        <Bar dataKey="revenue" fill="var(--chart-1)" radius={[0, 4, 4, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Movements title="Arrivals today" icon={<DoorOpen className="size-4" />} rows={s.today.arrivals} kind="arrival" onOpen={setOpen} />
            <Movements title="Departures today" icon={<DoorClosed className="size-4" />} rows={s.today.departures} kind="departure" onOpen={setOpen} />
          </div>
        </div>
      )}

      <BookingSheet bookingId={open} onClose={() => setOpen(null)} />
    </>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="size-2.5 rounded-full" style={{ background: color }} />
      {label}
    </span>
  )
}

function LoadingGrid() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
      <Skeleton className="h-72" />
    </div>
  )
}

function Attention({ s }: { s: Stats }) {
  const items: { text: string; to: string }[] = []
  if (s.attention.pending_bookings > 0)
    items.push({ text: `${s.attention.pending_bookings} booking(s) awaiting confirmation`, to: '/bookings?status=pending' })
  if (s.attention.unpaid_soon > 0)
    items.push({ text: `${s.attention.unpaid_soon} stay(s) starting within 2 days with payment pending`, to: '/bookings?status=confirmed,checked_in&payment=pending' })
  if (s.attention.reminders_failed > 0)
    items.push({ text: `${s.attention.reminders_failed} reminder(s) failed to send`, to: '/reminders' })
  if (s.rooms.total === 0)
    items.push({ text: 'No rooms in the database yet — run `python -m utils.seed_rooms` or add rooms', to: '/rooms' })
  if (items.length === 0) return null
  return (
    <div className="rounded-lg border border-amber-300/60 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
      <div className="mb-1 flex items-center gap-2 text-sm font-medium text-amber-900 dark:text-amber-200">
        <AlertTriangle className="size-4" /> Needs attention
      </div>
      <ul className="space-y-1 text-sm">
        {items.map((it) => (
          <li key={it.text}>
            <Link to={it.to} className="inline-flex items-center gap-1 text-amber-900 hover:underline dark:text-amber-100">
              {it.text} <ArrowRight className="size-3" />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Movements({
  title,
  icon,
  rows,
  kind,
  onOpen,
}: {
  title: string
  icon: ReactNode
  rows: MiniBooking[]
  kind: 'arrival' | 'departure'
  onOpen: (id: string) => void
}) {
  const setStatus = useSetBookingStatus()
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {icon} {title}
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-normal text-muted-foreground">{rows.length}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState title={kind === 'arrival' ? 'No arrivals today' : 'No departures today'} className="py-6" />
        ) : (
          <ul className="divide-y">
            {rows.map((b) => (
              <li key={b.booking_id} className="flex items-center gap-3 py-2.5">
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onOpen(b.booking_id)}>
                  <div className="truncate text-sm font-medium">{b.guest_name}</div>
                  <div className="text-xs text-muted-foreground">
                    Room {b.room_number} · {titleCase(b.room_type)} · {b.booking_id}
                  </div>
                </button>
                <BookingStatusBadge status={b.status} />
                <Button
                  size="xs"
                  variant="outline"
                  disabled={setStatus.isPending || (kind === 'arrival' ? false : b.status !== 'checked_in')}
                  onClick={() => {
                    const status = kind === 'arrival' ? 'checked_in' : 'checked_out'
                    setStatus.mutate(
                      { bookingId: b.booking_id, status },
                      { onSuccess: () => toast.success(kind === 'arrival' ? 'Checked in' : 'Checked out') },
                    )
                  }}
                >
                  {kind === 'arrival' ? 'Check in' : 'Check out'}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
