import { useState } from 'react'
import { ErrorBanner, PageHeader } from '@receptionist/shared/components/blocks'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Input } from '@receptionist/shared/components/ui/input'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { SOURCE_TEXT, costSentence, usdRate } from '@/lib/call-cost'
import { useCarrierRate, useCostModel, useMargins, type CostModel } from '@/lib/platform'

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

/** What the phone network charges per minute: the launch markets, and a lookup for any number in the world. */
function CarrierRates({ rows }: { rows: CostModel['carrier_table'] }) {
  const [raw, setRaw] = useState('')
  const number = useDebounced(raw.replace(/[^\d+]/g, ''), 400)
  const q = useCarrierRate(number.length >= 8 ? number : '')
  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>Phone network rates</CardTitle>
        <CardDescription>
          Twilio Elastic SIP, USD per minute, for this account (refreshed weekly). An outbound call pays the rate of
          the number it rings, not the inbound rate. "All-in" adds the AI and LiveKit costs above.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Country</TableHead><TableHead className="text-right">Inbound</TableHead>
            <TableHead className="text-right">To a landline</TableHead><TableHead className="text-right">To a mobile</TableHead>
            <TableHead className="text-right">All-in inbound</TableHead><TableHead className="text-right">All-in to a mobile</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.country}>
                <TableCell className="font-medium">
                  {r.country_name} {r.source !== 'twilio' && <Badge variant="secondary" className="ml-1">{SOURCE_TEXT[r.source]}</Badge>}
                </TableCell>
                <TableCell className="text-right tabular-nums">{usdRate(r.inbound)}</TableCell>
                <TableCell className="text-right tabular-nums">{usdRate(r.outbound_landline)}</TableCell>
                <TableCell className="text-right tabular-nums">{usdRate(r.outbound_mobile)}</TableCell>
                <TableCell className="text-right tabular-nums">{usdRate(r.per_minute_inbound)}</TableCell>
                <TableCell className="text-right tabular-nums">{usdRate(r.per_minute_outbound_mobile)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="space-y-2">
          <label htmlFor="rate-number" className="text-sm font-medium">Check a number, any country</label>
          <Input id="rate-number" className="max-w-xs" inputMode="tel" placeholder="+61 412 345 678" value={raw}
            onChange={(e) => setRaw(e.target.value)} />
          {q.error && <p className="text-xs text-destructive">{q.error instanceof Error ? q.error.message : 'Enter a full international number.'}</p>}
          {q.data && (
            <ul className="space-y-1 text-sm" aria-label="Rate for this number">
              <li><span className="font-medium">Calling it:</span> {costSentence(q.data.outbound)} · carrier {usdRate(q.data.outbound.carrier_usd_per_min)}/min
                <span className="text-xs text-muted-foreground"> ({q.data.outbound.basis})</span>
                {q.data.outbound.warn && <Badge variant="destructive" className="ml-1">costs more</Badge>}</li>
              <li><span className="font-medium">If it were our number, receiving:</span> {costSentence(q.data.inbound)} · carrier {usdRate(q.data.inbound.carrier_usd_per_min)}/min</li>
            </ul>
          )}
        </div>
      </CardContent>
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
      {m.carrier_table && <CarrierRates rows={m.carrier_table} />}
      <MarginTable />
      <p className="mt-4 text-xs text-muted-foreground">
        Businesses by status: {Object.entries(m.tenants_by_status).map(([k, n]) => `${k} ${n}`).join(' · ') || 'none yet'}
      </p>
    </>
  )
}
