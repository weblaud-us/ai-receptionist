import { useState } from 'react'
import { toast } from 'sonner'
import { Copy, Loader2, UserPlus } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateTime } from '@receptionist/shared/lib/format'
import { useSalesTeam, useTeamMutations, type SalesRole } from '@/lib/sales'

const ROLE_HELP: Record<SalesRole | 'admin', string> = {
  viewer: 'Sees everything, changes nothing',
  caller: 'Works leads: power dialer, notes, tasks, wrap-up',
  manager: 'Offers, AI, campaigns, imports and exports too',
  admin: 'Platform admin: everything, plus the team',
}
const ROLES: SalesRole[] = ['viewer', 'caller', 'manager']

/** Who may use the sales dialer (SALES_SUITE_PLAN.md Phase 2). Platform admins only. */
export function Team() {
  const q = useSalesTeam()
  const m = useTeamMutations()
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ email: '', name: '', role: 'caller' as SalesRole, takes_leads: true })
  const [link, setLink] = useState<string | null>(null)

  const add = () => m.add.mutate(form, {
    onSuccess: (r) => {
      toast.success(`${r.member.email} can now use the dialer as ${r.member.role}`)
      setAdding(false)
      setLink(r.set_password_link)
      setForm({ email: '', name: '', role: 'caller', takes_leads: true })
    },
  })

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">
          People here see only the Sales dialer. Taking a role away works at once. Everyone needs two-factor sign-in, and every change is in the audit log.
        </p>
        <div className="flex-1" />
        <Button onClick={() => setAdding(true)}><UserPlus /> Add someone</Button>
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      {link && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary/5 p-2 text-sm">
          Send them this one-time link to set a password (it isn't emailed): <code className="max-w-md truncate">{link}</code>
          <Button size="xs" variant="outline" onClick={() => { void navigator.clipboard.writeText(link); toast.success('Copied') }}><Copy /> Copy</Button>
        </div>
      )}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={3} cols={5} /> : (q.data?.members.length ?? 0) === 0 ? (
          <EmptyState title="Only platform admins so far" description="Add salespeople as callers, and a sales manager to run offers and campaigns." />
        ) : (
          <Table>
            <TableHeader><TableRow><TableHead>Person</TableHead><TableHead>Role</TableHead><TableHead>Gets new leads</TableHead><TableHead>Two-factor</TableHead><TableHead>Last sign-in</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {q.data?.members.map((p) => (
                <TableRow key={p.id}>
                  <TableCell><div className="font-medium">{p.name || p.email}</div><div className="text-xs text-muted-foreground">{p.email}</div></TableCell>
                  <TableCell>
                    {p.role === 'admin' ? <Badge variant="secondary">Platform admin</Badge> : (
                      <SimpleSelect size="sm" value={p.role} onChange={(v) => m.update.mutate({ id: p.id, role: v as SalesRole }, { onSuccess: () => toast.success('Role changed') })}
                        options={ROLES.map((r) => ({ value: r, label: r[0].toUpperCase() + r.slice(1) }))} />
                    )}
                    <div className="mt-0.5 text-xs text-muted-foreground">{ROLE_HELP[p.role]}</div>
                  </TableCell>
                  <TableCell>
                    {p.role !== 'admin' && <Switch checked={p.takes_leads} onCheckedChange={(v) => m.update.mutate({ id: p.id, takes_leads: v })} />}
                  </TableCell>
                  <TableCell className="text-sm">{p.mfa_enabled ? 'On' : <span className="text-amber-700 dark:text-amber-400">Not set up</span>}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{p.last_login_at ? dateTime(p.last_login_at) : 'Never'}</TableCell>
                  <TableCell className="text-right">
                    {p.role !== 'admin' && <Button size="sm" variant="ghost" onClick={() => m.update.mutate({ id: p.id, role: 'none' }, { onSuccess: () => toast.success('Access removed') })}>Remove</Button>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      <Dialog open={adding} onOpenChange={setAdding}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add someone to the sales team</DialogTitle>
            <DialogDescription>They sign in to this dashboard and see only the Sales dialer.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Field label="Email"><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Role" hint={ROLE_HELP[form.role]}>
              <SimpleSelect className="w-full" value={form.role} onChange={(v) => setForm({ ...form, role: v as SalesRole })}
                options={ROLES.map((r) => ({ value: r, label: r[0].toUpperCase() + r.slice(1) }))} />
            </Field>
            <label className="flex items-center gap-2 text-sm"><Switch checked={form.takes_leads} onCheckedChange={(v) => setForm({ ...form, takes_leads: v })} /> Share new interested leads and deals with them</label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAdding(false)}>Cancel</Button>
            <Button disabled={!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email) || m.add.isPending} onClick={add}>{m.add.isPending && <Loader2 className="animate-spin" />} Add</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
