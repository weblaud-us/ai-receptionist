import { useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, CheckCircle2, ShieldCheck } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader, Pagination } from '@/components/app/blocks'
import { SecurityCard } from '@/components/app/security-card'
import { Campaigns } from '@/components/sales/campaigns'
import { Leads } from '@/components/sales/leads'
import { Live } from '@/components/sales/live'
import { Results } from '@/components/sales/results'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuth } from '@/auth/auth'
import { dateOnly, dateTime, titleCase } from '@/lib/format'
import { MODE_LABEL, useDnc, useLiveSalesCalls, useSalesMutations, useSalesStatus } from '@/lib/sales'
import { cn } from '@/lib/utils'

const TABS = ['campaigns', 'leads', 'live', 'results', 'compliance'] as const
type Tab = (typeof TABS)[number]
const TAB_LABEL: Record<Tab, string> = { campaigns: 'Campaigns', leads: 'Leads', live: 'Live calls', results: 'Results', compliance: 'Compliance' }

/** Outbound AI sales dialer (Roadmap Phase 4). Platform administrators only. */
export default function Dialer() {
  const { user } = useAuth()
  const admin = !!user?.is_platform_admin
  const [tab, setTab] = useState<Tab>('campaigns')
  const status = useSalesStatus()
  const live = useLiveSalesCalls(admin)

  if (!admin) return <EmptyState title="Platform administrators only" icon={<ShieldCheck className="size-8" />} />
  const mfa = String((status.error as Error | null)?.message ?? '').startsWith('mfa_required')

  return (
    <>
      <PageHeader title="Sales dialer" description="Outbound calls that sell your receptionist. Every number passes the compliance checks before every call." />
      {mfa ? (
        <div className="mb-4 max-w-md space-y-3">
          <ErrorBanner error={new Error('The dialer needs two-factor sign-in. Turn it on below, then reload.')} />
          <SecurityCard />
        </div>
      ) : status.error ? <div className="mb-4"><ErrorBanner error={status.error} /></div> : null}
      {status.data && !status.data.ready && (
        <div className="mb-4 flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" />
          <div><div className="font-medium">The dialer can't place calls yet</div><ul className="list-disc pl-4 text-muted-foreground">{status.data.problems.map((p) => <li key={p}>{p}</li>)}</ul></div>
        </div>
      )}
      <div className="mb-4 flex flex-wrap gap-1 border-b">
        {TABS.map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={cn('-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors', tab === t ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}>
            {TAB_LABEL[t]}{t === 'live' && (live.data?.total ?? 0) > 0 && <span className="ml-1.5 rounded-full bg-emerald-500/20 px-1.5 text-xs text-emerald-600">{live.data?.total}</span>}
          </button>
        ))}
      </div>
      {tab === 'campaigns' && <Campaigns />}
      {tab === 'leads' && <Leads />}
      {tab === 'live' && <Live />}
      {tab === 'results' && <Results />}
      {tab === 'compliance' && <Compliance />}
    </>
  )
}

function Compliance() {
  const status = useSalesStatus().data
  const m = useSalesMutations()
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [phone, setPhone] = useState('')
  const dnc = useDnc({ page, q: search })
  if (!status) return null
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>Do-not-call registries</CardTitle><CardDescription>A number can't be dialled unless its country's list is loaded for its area code and is under {status.registry_max_age_days} days old. Load them with <code>python -m scripts.import_dnc_registry</code>.</CardDescription></CardHeader>
        <CardContent className="space-y-2 text-sm">
          {status.registries.length === 0 && <p className="text-destructive">No registry is loaded. US numbers cannot be dialled.</p>}
          {status.registries.map((r) => (
            <div key={r.list} className="flex items-start gap-2">
              {r.stale_area_codes.length ? <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" /> : <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />}
              <div><span className="font-medium">{r.list}</span> · {r.rows.toLocaleString()} numbers · refreshed {dateOnly(r.refreshed_at)}
                <div className="text-xs text-muted-foreground">Area codes: {r.area_codes.join(', ')}{r.stale_area_codes.length ? ` — out of date: ${r.stale_area_codes.join(', ')}` : ''}</div></div>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>What may be called</CardTitle><CardDescription>Set on the server. A lawyer must confirm these for each market before the first campaign.</CardDescription></CardHeader>
        <CardContent className="space-y-2 text-sm">
          {status.modes.map((mode) => <div key={mode.key}><span className="font-medium">{MODE_LABEL[mode.key]}</span><div className="text-xs text-muted-foreground">Countries: {mode.countries.join(', ') || 'none'}</div></div>)}
          <div><span className="font-medium">US states with AI cold calls switched off</span><div className="text-xs text-muted-foreground">{status.blocked_us_states.join(', ') || 'none'}</div></div>
          <div><span className="font-medium">Line-type lookups</span><div className="text-xs text-muted-foreground">{status.line_type_lookup ? 'On: mobiles are detected before each cold call.' : 'Not configured: no cold call can pass the checks.'}</div></div>
        </CardContent>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader><CardTitle>Your do-not-call list ({status.counts.dnc})</CardTitle><CardDescription>Permanent. Numbers are added when someone asks on a call, or by you. They can't be removed here.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Add a number"><Input value={phone} placeholder="+15125551234" onChange={(e) => setPhone(e.target.value)} /></Field>
            <Button disabled={phone.replace(/\D/g, '').length < 8 || m.addDnc.isPending}
              onClick={() => m.addDnc.mutate(phone, { onSuccess: (r) => { toast.success(r.added ? `${r.phone} will never be called` : 'Already on the list'); setPhone('') }, onError: (e) => toast.error(e.message) })}>Add</Button>
            <div className="flex-1" />
            <Input className="max-w-xs" placeholder="Find a number" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
          </div>
          {(dnc.data?.entries.length ?? 0) > 0 && (
            <div className="rounded-md border">
              <Table>
                <TableHeader><TableRow><TableHead>Number</TableHead><TableHead>Why</TableHead><TableHead>Added</TableHead></TableRow></TableHeader>
                <TableBody>{dnc.data?.entries.map((e) => <TableRow key={e.phone}><TableCell className="font-mono text-xs">{e.phone}</TableCell><TableCell>{titleCase(e.reason)}</TableCell><TableCell className="text-muted-foreground">{dateTime(e.added_at)}</TableCell></TableRow>)}</TableBody>
              </Table>
              <Pagination page={dnc.data?.page ?? 1} pages={dnc.data?.pages ?? 1} total={dnc.data?.total ?? 0} onPage={setPage} label="numbers" />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
