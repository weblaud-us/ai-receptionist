import { useState } from 'react'
import { toast } from 'sonner'
import { Link, useNavigate } from 'react-router-dom'
import { ExternalLink, Plus } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { SecurityCard } from '@receptionist/shared/components/security-card'
import { titleCase } from '@receptionist/shared/lib/format'
import { impersonateUrl } from '@receptionist/shared/lib/urls'
import { AddNumberDialog } from '@/components/add-number-dialog'
import { usePlatformMutations, usePlatformTenants, type PlatformTenant } from '@/lib/platform'

export default function Businesses() {
  const q = usePlatformTenants()
  const m = usePlatformMutations()
  const navigate = useNavigate()
  const [numberFor, setNumberFor] = useState<PlatformTenant | null>(null)

  // Opens the business in the client dashboard (new tab), acting as its owner. Audited server-side.
  const open = (t: PlatformTenant) => window.open(impersonateUrl(t.id), '_blank', 'noopener')

  return (
    <>
      <PageHeader title="Businesses" description="Every business on the platform. Opening one acts as its owner in the client dashboard — every change is audited."
        actions={<Button onClick={() => navigate('/businesses/new')}><Plus /> New business</Button>} />
      {q.error && (
        String((q.error as Error).message).startsWith('mfa_required') ? (
          <div className="mb-4 space-y-3">
            <ErrorBanner error={new Error('Platform access needs two-factor sign-in. Turn it on below, then reload.')} />
            <div className="max-w-md"><SecurityCard /></div>
          </div>
        ) : <ErrorBanner error={q.error} />
      )}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={5} cols={8} /> : (q.data?.tenants.length ?? 0) === 0 ? (
          <EmptyState title="No businesses yet" description="Provision the first one, or run migration m0003 to turn the existing hotel into tenant #1." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Business</TableHead><TableHead>Type</TableHead><TableHead>Status</TableHead>
                <TableHead>Numbers</TableHead><TableHead>Calls (30 d)</TableHead><TableHead>Minutes (30 d)</TableHead>
                <TableHead title="Calls at once before callers hear 'all lines are busy'">Max calls at once</TableHead><TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {q.data?.tenants.map((t) => (
                <TableRow key={t.id}>
                  <TableCell><Link to={`/businesses/${t.id}`} className="font-medium hover:underline">{t.name}</Link><div className="text-xs text-muted-foreground">{t.timezone} · {t.region.toUpperCase()}</div></TableCell>
                  <TableCell>{titleCase(t.vertical)}</TableCell>
                  <TableCell>
                    <SimpleSelect size="sm" value={t.status} onChange={(v) => m.update.mutate({ id: t.id, status: v }, { onSuccess: () => toast.success('Status updated') })}
                      options={['trial', 'active', 'past_due', 'suspended', 'cancelled'].map((s) => ({ value: s, label: titleCase(s) }))} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {t.numbers.map((n) => (
                      <div key={n} className="flex items-center gap-1">{n}
                        <Button size="xs" variant="link" className="h-auto px-1 text-destructive" onClick={() => m.releaseNumber.mutate(n, { onSuccess: () => toast.success('Released') })}>release</Button>
                      </div>
                    ))}
                    <Button size="xs" variant="link" className="h-auto px-0" onClick={() => setNumberFor(t)}>+ add number</Button>
                  </TableCell>
                  <TableCell className="tabular-nums">{t.calls_30d}</TableCell>
                  <TableCell className="tabular-nums">{t.minutes_30d}{t.included_minutes ? ` / ${t.included_minutes}` : ''}</TableCell>
                  <TableCell><ConcurrencyInput tenant={t} /></TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="outline" nativeButton={false} render={<Link to={`/businesses/${t.id}`} />}>Details</Button>
                      <Button size="sm" variant="outline" onClick={() => open(t)} title="Open the client dashboard as this business's owner">Open <ExternalLink /></Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      {numberFor && <AddNumberDialog tenant={numberFor} onClose={() => setNumberFor(null)} />}
    </>
  )
}

/** Per-business concurrent-call limit (Roadmap Task 3.6). Saved on blur / Enter. */
function ConcurrencyInput({ tenant }: { tenant: PlatformTenant }) {
  const m = usePlatformMutations()
  const [value, setValue] = useState(String(tenant.max_concurrent_calls))
  const save = () => {
    const n = Number(value)
    if (!Number.isInteger(n) || n < 1 || n > 500) {
      toast.error('Enter a whole number from 1 to 500')
      setValue(String(tenant.max_concurrent_calls))
      return
    }
    if (n === tenant.max_concurrent_calls) return
    m.update.mutate({ id: tenant.id, limits: { max_concurrent_calls: n } }, {
      onSuccess: () => toast.success(`${tenant.name}: up to ${n} calls at once`),
      onError: () => setValue(String(tenant.max_concurrent_calls)),
    })
  }
  return (
    <Input type="number" min={1} max={500} inputMode="numeric" className="h-8 w-20 tabular-nums"
      aria-label={`Max concurrent calls for ${tenant.name}`} value={value}
      onChange={(e) => setValue(e.target.value)} onBlur={save}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
  )
}
