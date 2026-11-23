import { ErrorBanner, PageHeader } from '@receptionist/shared/components/blocks'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { useCostModel, useMargins } from '@/lib/platform'

const usd = (n: number, digits = 2) => `$${n.toFixed(digits)}`

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
      </CardHeader>
      {hint && <CardContent className="pt-0 text-xs text-muted-foreground">{hint}</CardContent>}
    </Card>
  )
}

/** Phase 7, Task 7.2: revenue vs cost per business, worst margin first. */
function MarginTable() {
  const q = useMargins()
  if (q.error) return <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return <Skeleton className="mt-4 h-48 w-full" />
  const m = q.data
  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>Margin per business</CardTitle>
        <CardDescription>
          Last {m.days} days: list price (prorated) plus overage, minus call cost and {usd(m.fixed_per_business_usd)} fixed.
          Manual (invoiced) businesses show cost only. Totals: revenue {usd(m.totals.revenue_usd)}, cost {usd(m.totals.cost_usd)}, margin {usd(m.totals.margin_usd)}.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader><TableRow><TableHead>Business</TableHead><TableHead>Plan</TableHead><TableHead className="text-right">Minutes</TableHead><TableHead className="text-right">Revenue</TableHead><TableHead className="text-right">Cost</TableHead><TableHead className="text-right">Margin</TableHead></TableRow></TableHeader>
          <TableBody>
            {m.businesses.map((b) => (
              <TableRow key={b.id}>
                <TableCell className="font-medium">
                  {b.name} {b.pilot && <Badge variant="secondary" className="ml-1">pilot</Badge>} {b.flag === 'loss' && <Badge variant="destructive" className="ml-1">loss</Badge>}
                </TableCell>
                <TableCell>{b.plan} <span className="text-xs text-muted-foreground">· {b.status}</span></TableCell>
                <TableCell className="text-right tabular-nums">{b.minutes.toLocaleString()}</TableCell>
                <TableCell className="text-right tabular-nums">{b.revenue_usd === null ? '—' : usd(b.revenue_usd)}</TableCell>
                <TableCell className="text-right tabular-nums">{usd(b.cost_usd)}</TableCell>
                <TableCell className="text-right tabular-nums">{b.margin_usd === null ? '—' : `${usd(b.margin_usd)}${b.margin_pct !== null ? ` (${b.margin_pct}%)` : ''}`}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

/** Platform admins: cost per minute, margin per plan, and what the last 30 days actually cost (Task 5.1). */
export default function Economics() {
  const q = useCostModel()
  if (q.error) return <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return <Skeleton className="h-96 w-full" />
  const m = q.data
  const minutes = m.last_30d.seconds / 60
  const live = Object.entries(m.tenants_by_plan).reduce((a, [, n]) => a + n, 0)
  return (
    <>
      <PageHeader title="Unit economics" description={m.note} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Variable cost / minute" value={usd(m.per_minute.mid, 3)} hint={`${usd(m.per_minute.low, 3)}–${usd(m.per_minute.high, 3)}`} />
        <Stat label="List MRR" value={usd(m.list_mrr_usd, 0)} hint={`${live} paying or trialling businesses, at list price`} />
        <Stat label="Minutes, last 30 days" value={Math.round(minutes).toLocaleString()} hint={`${m.last_30d.calls.toLocaleString()} calls`} />
        <Stat label="Estimated cost, last 30 days" value={usd(m.last_30d.cost_usd)} hint={`Fixed ${usd(m.fixed_monthly_total.low, 0)}–${usd(m.fixed_monthly_total.high, 0)} / month on top`} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Margin per plan</CardTitle><CardDescription>At full use of included minutes, plus {usd(m.fixed_per_tenant_month, 0)}/business/month fixed.</CardDescription></CardHeader>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>Plan</TableHead><TableHead className="text-right">Price</TableHead><TableHead className="text-right">Minutes</TableHead><TableHead className="text-right">Margin</TableHead><TableHead className="text-right">Businesses</TableHead></TableRow></TableHeader>
              <TableBody>
                {m.plans.map((p) => (
                  <TableRow key={p.key}>
                    <TableCell className="font-medium">{p.name}</TableCell>
                    <TableCell className="text-right tabular-nums">{usd(p.monthly_usd, 0)}</TableCell>
                    <TableCell className="text-right tabular-nums">{p.included_minutes}</TableCell>
                    <TableCell className="text-right tabular-nums">{p.margin_pct}%</TableCell>
                    <TableCell className="text-right tabular-nums">{m.tenants_by_plan[p.key] ?? 0}</TableCell>
                  </TableRow>
                ))}
                <TableRow>
                  <TableCell className="font-medium">Overage</TableCell>
                  <TableCell className="text-right tabular-nums">{usd(m.overage.usd_per_min)}/min</TableCell>
                  <TableCell />
                  <TableCell className="text-right tabular-nums">{m.overage.margin_pct}%</TableCell>
                  <TableCell />
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Cost per call-minute</CardTitle><CardDescription>List-price estimates — verify each before publishing prices.</CardDescription></CardHeader>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>Item</TableHead><TableHead className="text-right">$/min</TableHead></TableRow></TableHeader>
              <TableBody>
                {m.items.map((i) => (
                  <TableRow key={i.key}>
                    <TableCell>
                      <div>{i.label}</div>
                      <div className="text-xs text-muted-foreground">{i.basis}{i.applies !== 'always' && ` · only ${i.applies}`}</div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{i.low === i.high ? usd(i.low, 4) : `${usd(i.low, 4)}–${usd(i.high, 4)}`}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
      <MarginTable />
      <p className="mt-4 text-xs text-muted-foreground">
        Businesses by status: {Object.entries(m.tenants_by_status).map(([k, n]) => `${k} ${n}`).join(' · ') || 'none yet'}
      </p>
    </>
  )
}
