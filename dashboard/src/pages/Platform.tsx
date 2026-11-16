import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { ArrowRight, Copy, Loader2, Plus, ShieldCheck } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader, TableSkeleton } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuth } from '@/auth/auth'
import { SecurityCard } from '@/components/app/security-card'
import { titleCase } from '@/lib/format'
import { TIMEZONES } from '@/lib/tenant-format'
import { usePlatformMutations, usePlatformTenants, useVerticals } from '@/lib/tenant-hooks'
import type { PlatformTenant } from '@/lib/tenant-types'

export default function Platform() {
  const { user, switchTenant } = useAuth()
  const navigate = useNavigate()
  const q = usePlatformTenants(!!user?.is_platform_admin)
  const m = usePlatformMutations()
  const [creating, setCreating] = useState(false)
  const [numberFor, setNumberFor] = useState<PlatformTenant | null>(null)

  if (!user?.is_platform_admin) return <EmptyState title="Platform administrators only" icon={<ShieldCheck className="size-8" />} />

  const open = async (t: PlatformTenant) => {
    await switchTenant(t.id)
    navigate('/')
  }

  return (
    <>
      <PageHeader title="Platform" description="Every business on the platform. Opening one acts as its owner — every change is audited."
        actions={<Button onClick={() => setCreating(true)}><Plus /> New business</Button>} />
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
                  <TableCell><div className="font-medium">{t.name}</div><div className="text-xs text-muted-foreground">{t.timezone} · {t.region.toUpperCase()}</div></TableCell>
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
                    <Button size="xs" variant="link" className="h-auto px-0" onClick={() => setNumberFor(t)}>+ assign</Button>
                  </TableCell>
                  <TableCell className="tabular-nums">{t.calls_30d}</TableCell>
                  <TableCell className="tabular-nums">{t.minutes_30d}{t.included_minutes ? ` / ${t.included_minutes}` : ''}</TableCell>
                  <TableCell><ConcurrencyInput tenant={t} /></TableCell>
                  <TableCell className="text-right"><Button size="sm" variant="outline" onClick={() => void open(t)}>Open <ArrowRight /></Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      {creating && <ProvisionDialog onClose={() => setCreating(false)} />}
      {numberFor && <AssignNumberDialog tenant={numberFor} onClose={() => setNumberFor(null)} />}
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

function ProvisionDialog({ onClose }: { onClose: () => void }) {
  const verticals = useVerticals()
  const m = usePlatformMutations()
  const [form, setForm] = useState({ name: '', vertical: 'home_services', timezone: 'America/Chicago', country: 'US', currency: 'USD',
    owner_email: '', owner_name: '', inbound_number: '' })
  const [link, setLink] = useState<string | null>(null)
  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch })
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New business</DialogTitle>
          <DialogDescription>Creates the business with its vertical's example services and FAQs, and the owner's account.</DialogDescription>
        </DialogHeader>
        {link ? (
          <div className="space-y-2 text-sm">
            <p>Created. Send the owner this one-time link to set their password (valid 1 hour):</p>
            <div className="flex items-center gap-1 rounded border p-2">
              <code className="flex-1 truncate text-xs">{link}</code>
              <Button size="icon-xs" variant="ghost" aria-label="Copy" onClick={() => void navigator.clipboard.writeText(link).then(() => toast.success('Copied'))}><Copy /></Button>
            </div>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Business name" className="sm:col-span-2"><Input value={form.name} onChange={(e) => set({ name: e.target.value })} /></Field>
            <Field label="Type" className="sm:col-span-2">
              <SimpleSelect value={form.vertical} onChange={(v) => set({ vertical: v })}
                options={(verticals.data?.verticals ?? []).map((v) => ({ value: v.key, label: v.label }))} />
            </Field>
            <Field label="Timezone"><SimpleSelect value={form.timezone} onChange={(v) => set({ timezone: v })} options={TIMEZONES.map((z) => ({ value: z, label: z }))} /></Field>
            <Field label="Country (ISO)"><Input maxLength={2} value={form.country} onChange={(e) => set({ country: e.target.value.toUpperCase() })} /></Field>
            <Field label="Owner e-mail"><Input type="email" value={form.owner_email} onChange={(e) => set({ owner_email: e.target.value })} /></Field>
            <Field label="Owner name"><Input value={form.owner_name} onChange={(e) => set({ owner_name: e.target.value })} /></Field>
            <Field label="AI number (optional)" hint="E.164, already on the SIP trunk" className="sm:col-span-2">
              <Input value={form.inbound_number} placeholder="+15125551234" onChange={(e) => set({ inbound_number: e.target.value })} />
            </Field>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{link ? 'Done' : 'Cancel'}</Button>
          {!link && (
            <Button disabled={form.name.trim().length < 2 || !form.owner_email.includes('@') || m.provision.isPending}
              onClick={() => m.provision.mutate({ ...form, inbound_number: form.inbound_number || undefined },
                { onSuccess: (r) => { toast.success(`${r.tenant.name} created`); if (r.set_password_link) setLink(r.set_password_link); else onClose() } })}>
              {m.provision.isPending && <Loader2 className="animate-spin" />} Create
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AssignNumberDialog({ tenant, onClose }: { tenant: PlatformTenant; onClose: () => void }) {
  const m = usePlatformMutations()
  const [e164, setE164] = useState('')
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Assign a number to {tenant.name}</DialogTitle>
          <DialogDescription>Calls to this number will be answered as {tenant.name}. It must already route to the LiveKit SIP trunk.</DialogDescription>
        </DialogHeader>
        <Input value={e164} placeholder="+15125551234" onChange={(e) => setE164(e.target.value)} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={e164.replace(/\D/g, '').length < 8 || m.assignNumber.isPending}
            onClick={() => m.assignNumber.mutate({ e164, tenant_id: tenant.id }, { onSuccess: () => { toast.success('Assigned'); onClose() } })}>
            Assign
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
