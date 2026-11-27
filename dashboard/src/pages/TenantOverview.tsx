import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import { ArrowRight, BookOpen, CalendarCheck, ClipboardList, Inbox, PhoneCall, Rocket, Timer } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Progress } from '@receptionist/shared/components/ui/progress'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { duration } from '@receptionist/shared/lib/format'
import { useCtx, useTenant } from '@/lib/tenant'
import { OUTCOME_LABEL, OUTCOME_STYLE, zoneDateTime } from '@/lib/tenant-format'
import { useOverview } from '@/lib/tenant-hooks'
import { DentalRoiCard } from '@/components/app/dental-roi'
import { GettingStartedCard, useGettingStartedVisible } from '@/components/app/getting-started'
import { cn } from '@receptionist/shared/lib/utils'

function Stat({ label, value, sub, icon, to }: { label: string; value: ReactNode; sub?: ReactNode; icon: ReactNode; to?: string }) {
  const body = (
    <Card className="h-full transition-colors hover:bg-muted/40">
      <CardContent className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm text-muted-foreground">{label}</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
          {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
        </div>
        <div className="rounded-lg bg-muted p-2 text-muted-foreground">{icon}</div>
      </CardContent>
    </Card>
  )
  return to ? <Link to={to}>{body}</Link> : body
}

export function OutcomeBadge({ outcome }: { outcome: string | null | undefined }) {
  const key = outcome ?? 'completed'
  return (
    <Badge variant="secondary" className={cn('border-0', OUTCOME_STYLE[key])}>
      {OUTCOME_LABEL[key] ?? key}
    </Badge>
  )
}

export default function TenantOverview() {
  const ctx = useCtx()
  const { can } = useTenant()
  const checklist = useGettingStartedVisible(ctx, can)
  const q = useOverview()
  const d = q.data
  const tz = ctx.tenant.timezone
  const L = ctx.vertical.labels
  const setupDone = !!ctx.tenant.onboarding?.completed_at || ctx.legacy
  const usagePct = d ? Math.min(100, Math.round((d.minutes_used / Math.max(d.minutes_included, 1)) * 100)) : 0

  return (
    <>
      <PageHeader title={`Good to see you`} description={`${ctx.tenant.name} · what your assistant did for you`} />
      {checklist && <GettingStartedCard ctx={ctx} />}
      {/* Hidden the list before finishing setup? The setup prompt stays until it's done. */}
      {!checklist && !setupDone && ctx.role !== 'staff' && ctx.role !== 'dentist' && (
        <Card className="mb-4 border-primary/30 bg-primary/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Rocket className="size-5 text-primary" />
              <div>
                <div className="font-medium">Finish setting up your assistant</div>
                <div className="text-sm text-muted-foreground">Hours, {L.services.toLowerCase()}, greeting and a test call — about ten minutes.</div>
              </div>
            </div>
            <Button nativeButton={false} render={<Link to="/onboarding" />}>
              Continue setup <ArrowRight />
            </Button>
          </CardContent>
        </Card>
      )}
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.isLoading && <Skeleton className="h-64" />}
      {d && (
        <div className="space-y-4">
          {ctx.vertical.key === 'dental' && !ctx.legacy && <DentalRoiCard />}
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Calls today" value={d.calls_today} icon={<PhoneCall className="size-4" />} to="/calls"
              sub={d.handled_rate_pct != null ? `${d.handled_rate_pct}% handled in the last 30 days` : undefined} />
            <Stat label={`${L.appointments} made today`} value={d.bookings_today} icon={<CalendarCheck className="size-4" />}
              to={ctx.vertical.uses_scheduling ? '/appointments' : '/bookings'} sub="by the assistant" />
            <Stat label="Messages waiting" value={d.messages_new + d.leads_new} icon={<Inbox className="size-4" />}
              to={d.leads_new ? '/leads' : '/calls'}
              sub={d.leads_new ? `${d.leads_new} job request${d.leads_new === 1 ? '' : 's'}` : 'call them back'} />
            <Stat label="Minutes this month" value={d.minutes_used.toLocaleString()} icon={<Timer className="size-4" />}
              sub={<span className="flex items-center gap-2"><Progress value={usagePct} className="h-1.5 w-20" /> of {d.minutes_included} included</span>} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Calls, last 30 days</CardTitle>
                <CardDescription>{d.calls_30d} calls answered by your assistant.</CardDescription>
              </CardHeader>
              <CardContent className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={d.series} margin={{ left: -20, right: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                    <XAxis dataKey="date" tickFormatter={(v: string) => v.slice(5)} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                    <ChartTooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8 }} />
                    <Area type="monotone" dataKey="calls" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.15} strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>How calls ended</CardTitle>
                <CardDescription>Last 30 days</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {Object.keys(d.outcomes_30d).length === 0 && <p className="text-sm text-muted-foreground">No calls yet.</p>}
                {Object.entries(d.outcomes_30d)
                  .sort((a, b) => b[1] - a[1])
                  .map(([k, n]) => (
                    <div key={k} className="flex items-center justify-between text-sm">
                      <OutcomeBadge outcome={k} />
                      <span className="tabular-nums">{n}</span>
                    </div>
                  ))}
                {d.unanswered_open > 0 && (
                  <Link to="/knowledge?tab=unanswered" className="mt-3 flex items-center gap-2 rounded-lg border p-2 text-sm hover:bg-muted">
                    <BookOpen className="size-4 text-amber-600" />
                    {d.unanswered_open} question{d.unanswered_open === 1 ? '' : 's'} your assistant couldn't answer
                    <ArrowRight className="ml-auto size-4" />
                  </Link>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="gap-0 py-0">
              <CardHeader className="border-b py-4">
                <CardTitle>Recent calls</CardTitle>
              </CardHeader>
              {d.recent_calls.length === 0 ? (
                <EmptyState title="No calls yet" icon={<PhoneCall className="size-8" />} description="Place a test call from the Assistant page." />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>When</TableHead>
                      <TableHead>Caller</TableHead>
                      <TableHead>Outcome</TableHead>
                      <TableHead>Length</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {d.recent_calls.map((c) => (
                      <TableRow key={c.call_id}>
                        <TableCell className="whitespace-nowrap">
                          <Link className="hover:underline" to={`/calls?call=${c.call_id}`}>{zoneDateTime(c.started_at, tz)}</Link>
                        </TableCell>
                        <TableCell>{c.caller_phone ?? (c.channel === 'browser' ? 'Browser' : 'Unknown')}</TableCell>
                        <TableCell><OutcomeBadge outcome={c.status === 'in_progress' ? 'in progress' : c.outcome} /></TableCell>
                        <TableCell>{duration(c.duration_s)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Card>
            {ctx.vertical.uses_scheduling && (
              <Card className="gap-0 py-0">
                <CardHeader className="border-b py-4">
                  <CardTitle>Coming up</CardTitle>
                </CardHeader>
                {d.upcoming.length === 0 ? (
                  <EmptyState title={`No upcoming ${L.appointments.toLowerCase()}`} icon={<ClipboardList className="size-8" />} />
                ) : (
                  <Table>
                    <TableBody>
                      {d.upcoming.map((a) => (
                        <TableRow key={a.appointment_id}>
                          <TableCell className="whitespace-nowrap">{zoneDateTime(a.start, tz)}</TableCell>
                          <TableCell className="font-medium">{a.name}</TableCell>
                          <TableCell>{a.service_name}</TableCell>
                          <TableCell className="text-muted-foreground">{a.resource_name}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </Card>
            )}
          </div>
        </div>
      )}
    </>
  )
}
