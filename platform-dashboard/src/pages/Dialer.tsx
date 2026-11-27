import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, CheckCircle2, ShieldCheck } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader, Pagination } from '@receptionist/shared/components/blocks'
import { SecurityCard } from '@receptionist/shared/components/security-card'
import { Campaigns } from '@/components/sales/campaigns'
import { Leads } from '@/components/sales/leads'
import { Offers } from '@/components/sales/offers'
import { Pipeline } from '@/components/sales/pipeline'
import { Live } from '@/components/sales/live'
import { Results } from '@/components/sales/results'
import { Team } from '@/components/sales/team'
import { NumbersCard } from '@/components/sales/numbers'
import { Reports } from '@/components/sales/reports'
import { ActivityLog } from '@/components/sales/activity'
import { SalesSearch } from '@/components/sales/search'
import { can as canDo } from '@/lib/roles'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { useAuth } from '@receptionist/shared/auth/auth'
import { dateOnly, dateTime, titleCase } from '@receptionist/shared/lib/format'
import { downloadSalesCsv, MODE_LABEL, useDnc, useLiveSalesCalls, useSalesMutations, useSalesStatus } from '@/lib/sales'

const TABS = ['offers', 'campaigns', 'leads', 'pipeline', 'live', 'results', 'reports', 'activity', 'compliance', 'team'] as const
type Tab = (typeof TABS)[number]
const TAB_LABEL: Record<Tab, string> = {
  offers: 'Offers', campaigns: 'Campaigns', leads: 'Leads', pipeline: 'Pipeline', live: 'Live calls', results: 'Results', reports: 'Reports', activity: 'Activity', compliance: 'Compliance', team: 'Team',
}

/** Outbound sales dialer (Roadmap Phase 4; SALES_DIALER_PLAN.md). Platform admins and the sales team
 * (viewer < caller < manager, SALES_SUITE_PLAN.md Phase 2). */
export default function Dialer() {
  const { user } = useAuth()
  const admin = canDo(user, 'viewer')
  const platformAdmin = canDo(user, 'admin')
  const [params, setParams] = useSearchParams()
  const tab: Tab = (TABS as readonly string[]).includes(params.get('tab') ?? '') ? (params.get('tab') as Tab) : 'campaigns'
  const status = useSalesStatus()
  const live = useLiveSalesCalls(admin)
  const [quickOffer, setQuickOffer] = useState<string | null>(null)

  if (!admin) return <EmptyState title="For the sales team only" icon={<ShieldCheck className="size-8" />} />
  const mfa = String((status.error as Error | null)?.message ?? '').startsWith('mfa_required')

  return (
    <>
      <PageHeader title="Sales dialer" description="Outbound calls that sell whatever you offer, and the CRM to close them. Every number passes the compliance checks before every call." />
      <div className="-mt-2 mb-3 flex justify-end"><SalesSearch /></div>
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
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: String(v) }, { replace: true })}>
        <TabsList variant="line" className="mb-4 flex-wrap">
          {TABS.filter((t) => (t !== 'team' || platformAdmin) && (t !== 'activity' || canDo(user, 'manager'))).map((t) => (
            <TabsTrigger key={t} value={t}>
              {TAB_LABEL[t]}{t === 'live' && (live.data?.total ?? 0) > 0 && <span className="ml-1.5 rounded-full bg-emerald-500/20 px-1.5 text-xs text-emerald-600">{live.data?.total}</span>}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="offers"><Offers onCreateCampaign={(id) => { setQuickOffer(id); setParams({ tab: 'campaigns' }, { replace: true }) }} /></TabsContent>
        <TabsContent value="campaigns"><Campaigns quickOffer={quickOffer} onQuickDone={() => setQuickOffer(null)} /></TabsContent>
        <TabsContent value="leads"><Leads /></TabsContent>
        <TabsContent value="pipeline"><Pipeline /></TabsContent>
        <TabsContent value="live"><Live /></TabsContent>
        <TabsContent value="results"><Results /></TabsContent>
        <TabsContent value="compliance"><Compliance /></TabsContent>
        <TabsContent value="reports"><Reports /></TabsContent>
        {canDo(user, 'manager') && <TabsContent value="activity"><ActivityLog /></TabsContent>}
        {platformAdmin && <TabsContent value="team"><Team /></TabsContent>}
      </Tabs>
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
      <NumbersCard />
      <Card>
        <CardHeader><CardTitle>What may be called</CardTitle><CardDescription>Set on the server. A lawyer must confirm these for each market before the first campaign.</CardDescription></CardHeader>
        <CardContent className="space-y-2 text-sm">
          {status.modes.map((mode) => <div key={mode.key}><span className="font-medium">{MODE_LABEL[mode.key]}</span><div className="text-xs text-muted-foreground">Countries: {mode.countries.join(', ') || 'none'}</div></div>)}
          <div><span className="font-medium">US states with AI cold calls switched off</span><div className="text-xs text-muted-foreground">{status.blocked_us_states.join(', ') || 'none'}</div></div>
          <div><span className="font-medium">Line-type lookups</span><div className="text-xs text-muted-foreground">{status.line_type_lookup ? 'On: mobiles are detected before each cold call.' : 'Not configured: no cold call can pass the checks.'}</div></div>
        </CardContent>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Australia: Do Not Call Register</CardTitle>
          <CardDescription>The register can't be downloaded: you wash numbers at donotcall.gov.au and get a result per number, good for {30} days. An Australian number without a fresh result is never called.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3 text-sm">
          {status.au_dncr_needs_wash > 0
            ? <span className="flex items-center gap-1.5"><AlertTriangle className="size-4 text-amber-600" /> {status.au_dncr_needs_wash} Australian number{status.au_dncr_needs_wash === 1 ? '' : 's'} need a wash</span>
            : <span className="flex items-center gap-1.5"><CheckCircle2 className="size-4 text-emerald-600" /> Every Australian lead has a fresh result</span>}
          <Button size="sm" variant="outline" disabled={!status.au_dncr_needs_wash}
            onClick={() => void downloadSalesCsv('dncr-wash').catch((e: Error) => toast.error(e.message))}>Download numbers to wash</Button>
          <span className="text-xs text-muted-foreground">Then load the result: <code>python -m scripts.import_dnc_registry au_dncr --wash-result wash.csv --country AU</code></span>
        </CardContent>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader><CardTitle>Your do-not-call list ({status.counts.dnc})</CardTitle><CardDescription>Permanent. Numbers are added when someone asks on a call, or by you. They can't be removed here.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Add a number"><Input value={phone} placeholder="+61290000000" onChange={(e) => setPhone(e.target.value)} /></Field>
            <Button disabled={phone.replace(/\D/g, '').length < 8 || m.addDnc.isPending}
              onClick={() => m.addDnc.mutate(phone, { onSuccess: (r) => { toast.success(r.added ? `${r.phone} will never be called` : 'Already on the list'); setPhone('') } })}>Add</Button>
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
