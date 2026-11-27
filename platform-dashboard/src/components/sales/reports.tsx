import { useState } from 'react'
import { ErrorBanner } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { DEAL_STAGE_LABEL, DEAL_STAGES, pct, usePeople, useReport } from '@/lib/sales'
import { money } from './crm-logic'
import { PickupHeatmap } from './charts'
import { useTiming } from '@/lib/learning'

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  )
}

function Bars({ data }: { data: Record<string, number> }) {
  const rows = Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, 8)
  const max = Math.max(1, ...rows.map(([, n]) => n))
  if (!rows.length) return <p className="text-sm text-muted-foreground">None yet.</p>
  return (
    <ul className="space-y-1.5 text-sm">
      {rows.map(([k, n]) => (
        <li key={k} className="grid grid-cols-[minmax(0,10rem)_1fr_2.5rem] items-center gap-2">
          <span className="truncate" title={k}>{k}</span>
          <span className="h-2 rounded-full bg-muted"><span className="block h-2 rounded-full bg-primary" style={{ width: `${(n / max) * 100}%` }} /></span>
          <span className="text-right tabular-nums">{n}</span>
        </li>
      ))}
    </ul>
  )
}

/** Sales reports from the built-in CRM (SALES_SUITE_PLAN.md Phase 18). */
function PickupCard() {
  const [country, setCountry] = useState('AU')
  const q = useTiming(country)
  const options = [...new Set([...(q.data?.countries ?? []), 'AU', 'GB', 'US'])].map((c) => ({ value: c, label: c }))
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><CardTitle>When businesses pick up</CardTitle><CardDescription>First calls in the last 90 days, by their local day and hour. Smart retries aim for the darker hours.</CardDescription></div>
          <SimpleSelect size="sm" value={country} onChange={setCountry} options={options} />
        </div>
      </CardHeader>
      <CardContent>{q.error ? <ErrorBanner error={q.error} /> : q.data ? <PickupHeatmap data={q.data} /> : null}</CardContent>
    </Card>
  )
}

export function Reports() {
  const [days, setDays] = useState(90)
  const q = useReport(days)
  const people = usePeople().data?.people ?? []
  const r = q.data
  const name = (id: string) => people.find((p) => p.id === id || `platform:${p.id}` === id)?.name ?? (id === 'sales_agent' ? 'AI agent' : id === 'system' ? 'System' : id)
  const f = r?.funnel
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted-foreground">Closed deals, meetings and activity in the last</span>
        <SimpleSelect size="sm" value={String(days)} onChange={(v) => setDays(Number(v))}
          options={[{ value: '30', label: '30 days' }, { value: '90', label: '90 days' }, { value: '365', label: '12 months' }]} />
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      {r && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Stat label="Open pipeline" value={money(r.pipeline.open_value)} hint={`${money(r.pipeline.forecast)} weighted forecast`} />
            <Stat label="Win rate" value={pct(r.results.win_rate)} hint={`${r.results.won} won · ${r.results.lost} lost`} />
            <Stat label="Average won deal" value={r.results.average_won != null ? money(r.results.average_won) : '—'} />
            <Stat label="Days to close" value={r.results.cycle_days != null ? String(r.results.cycle_days) : '—'} hint="from deal opened to won" />
            <Stat label="Meetings booked" value={String(r.meetings.total)} />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>Funnel</CardTitle><CardDescription>Leads that reached each step.</CardDescription></CardHeader>
              <CardContent>{f && <Bars data={{ 'Leads': f.leads, 'Called': f.called, 'Reached a person': f.reached, 'Meeting booked': f.meetings, 'Won': f.won }} />}</CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Pipeline by stage</CardTitle><CardDescription>Value, and value × probability.</CardDescription></CardHeader>
              <CardContent>
                <ul className="space-y-1 text-sm">
                  {DEAL_STAGES.map((s) => (
                    <li key={s} className="flex justify-between"><span>{DEAL_STAGE_LABEL[s]} <span className="text-muted-foreground">({r.pipeline.stages[s]?.count ?? 0})</span></span>
                      <span className="tabular-nums">{money(r.pipeline.stages[s]?.value ?? 0)} <span className="text-muted-foreground">· {money(r.pipeline.stages[s]?.weighted ?? 0)}</span></span></li>
                  ))}
                </ul>
              </CardContent>
            </Card>
            <Card><CardHeader><CardTitle>Meetings by offer</CardTitle></CardHeader><CardContent><Bars data={r.meetings.by_offer} /></CardContent></Card>
            <Card><CardHeader><CardTitle>Meetings by campaign</CardTitle></CardHeader><CardContent><Bars data={r.meetings.by_campaign} /></CardContent></Card>
            <Card><CardHeader><CardTitle>Meetings by lead source</CardTitle></CardHeader><CardContent><Bars data={r.meetings.by_source} /></CardContent></Card>
            <Card>
              <CardHeader><CardTitle>Activity, last 4 weeks</CardTitle><CardDescription>Calls, notes, emails and stage moves by who did them.</CardDescription></CardHeader>
              <CardContent><Bars data={Object.fromEntries(Object.entries(r.activity).map(([who, c]) => [name(who), Object.values(c).reduce((a, b) => a + b, 0)]))} /></CardContent>
            </Card>
          </div>
        </>
      )}
      <PickupCard />
    </div>
  )
}
