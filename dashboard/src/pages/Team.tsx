import { useState } from 'react'
import { toast } from 'sonner'
import { Copy, Loader2, Trash2, UserPlus } from 'lucide-react'
import { ConfirmDialog, ErrorBanner, Field, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { useAuth } from '@receptionist/shared/auth/auth'
import { SecurityCard } from '@receptionist/shared/components/security-card'
import { timeAgo } from '@receptionist/shared/lib/format'
import { useTenant } from '@/lib/tenant'
import { ROLE_LABEL } from '@/lib/tenant-format'
import { useResources, useTeam, useTeamMutations } from '@/lib/tenant-hooks'
import type { TeamMember } from '@/lib/tenant-types'

const ROLES = [
  { value: 'staff', label: 'Staff — calls, messages, bookings' },
  { value: 'admin', label: 'Admin — plus settings and knowledge' },
  { value: 'owner', label: 'Owner — everything, incl. team and billing' },
]
const DENTIST_ROLE = { value: 'dentist', label: 'Dentist — only their own patients (My day)' }

/** A member's role; a dentist also names the practitioner whose day they see. Saved only when complete. */
function MemberRole({ mem, roles, dentists }: { mem: TeamMember; roles: typeof ROLES; dentists: { value: string; label: string }[] }) {
  const m = useTeamMutations()
  const [role, setRole] = useState(mem.role)
  const save = (next: string, resource_id?: string) => {
    if (next === 'dentist' && !resource_id) {
      setRole('dentist')                       // wait for the practitioner
      return
    }
    setRole(next as TeamMember['role'])
    m.role.mutate({ id: mem.id, role: next, resource_id }, { onSuccess: () => toast.success('Role updated') })
  }
  return (
    <div className="flex flex-wrap gap-2">
      <SimpleSelect size="sm" value={role} onChange={(v) => save(v)}
        options={roles.map((r) => ({ value: r.value, label: ROLE_LABEL[r.value] }))} />
      {role === 'dentist' && (
        <SimpleSelect size="sm" value={mem.role === 'dentist' ? mem.resource_id ?? '' : ''} placeholder="Which dentist?"
          onChange={(v) => save('dentist', v)} options={dentists} />
      )}
    </div>
  )
}

export default function Team() {
  const { ctx, can } = useTenant()
  const { user } = useAuth()
  const q = useTeam()
  const m = useTeamMutations()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('staff')
  const [resourceId, setResourceId] = useState('')
  const [link, setLink] = useState<string | null>(null)
  const [removing, setRemoving] = useState<TeamMember | null>(null)
  const owner = can('owner')
  // Dental practices can give a dentist their own login that shows only their day.
  const dental = ctx?.vertical.key === 'dental'
  const roles = dental ? [...ROLES, DENTIST_ROLE] : ROLES
  const resources = useResources(owner && dental)
  const dentists = (resources.data?.resources ?? []).filter((r) => r.active).map((r) => ({ value: r.id, label: r.name }))
  const dentistName = (id?: string | null) => dentists.find((d) => d.value === id)?.label

  return (
    <>
      <PageHeader title="Team" description="Who can sign in to this dashboard, and your own sign-in security." />
      {q.error && <ErrorBanner error={q.error} />}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-3 lg:max-w-md"><SecurityCard /></div>
        <Card className="gap-0 py-0 lg:col-span-2">
          {q.isLoading ? <TableSkeleton rows={3} cols={4} /> : (
            <Table>
              <TableHeader><TableRow><TableHead>Person</TableHead><TableHead>Role</TableHead><TableHead>Last sign-in</TableHead><TableHead /></TableRow></TableHeader>
              <TableBody>
                {q.data?.members.map((mem) => (
                  <TableRow key={mem.id}>
                    <TableCell>
                      <div className="font-medium">{mem.name || mem.email}{mem.id === user?.id && <span className="text-muted-foreground"> (you)</span>}</div>
                      <div className="text-xs text-muted-foreground">{mem.email}</div>
                    </TableCell>
                    <TableCell>
                      {owner && mem.id !== user?.id ? (
                        <MemberRole mem={mem} roles={roles} dentists={dentists} />
                      ) : (
                        <Badge variant="secondary">
                          {ROLE_LABEL[mem.role]}{mem.role === 'dentist' && dentistName(mem.resource_id) ? ` · ${dentistName(mem.resource_id)}` : ''}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{mem.pending_password ? 'Hasn\'t set a password yet' : timeAgo(mem.last_login_at)}</TableCell>
                    <TableCell className="text-right">
                      {owner && mem.id !== user?.id && (
                        <Button size="icon-xs" variant="ghost" aria-label="Remove" onClick={() => setRemoving(mem)}><Trash2 /></Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {q.data?.invites.map((i) => (
                  <TableRow key={i.email} className="opacity-60">
                    <TableCell>{i.email}</TableCell>
                    <TableCell><Badge variant="outline">{ROLE_LABEL[i.role]} · invited</Badge></TableCell>
                    <TableCell className="text-muted-foreground">Expires {new Date(i.expires_at).toLocaleDateString()}</TableCell>
                    <TableCell />
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
        {owner && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><UserPlus className="size-4" /> Invite someone</CardTitle>
              <CardDescription>They get an e-mail with a link to set their password.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Field label="E-mail"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
              <Field label="Role"><SimpleSelect value={role} onChange={setRole} options={roles} /></Field>
              {role === 'dentist' && (
                <Field label="Which dentist?" hint="They'll see this practitioner's patients today and tomorrow, and nothing else.">
                  <SimpleSelect value={resourceId} onChange={setResourceId} placeholder="Choose a practitioner" options={dentists} />
                </Field>
              )}
              <Button disabled={!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || m.invite.isPending || (role === 'dentist' && !resourceId)}
                onClick={() => m.invite.mutate({ email: email.trim(), role, resource_id: role === 'dentist' ? resourceId : undefined }, {
                  onSuccess: (r) => {
                    setEmail('')
                    if (r.emailed) toast.success('Invitation sent')
                    else setLink(r.link)
                  },
                })}>
                {m.invite.isPending && <Loader2 className="animate-spin" />} Send invite
              </Button>
              {link && (
                <div className="space-y-1 rounded-lg border p-2 text-xs">
                  <p>E-mail isn't set up on the server. Send this link yourself (valid 7 days):</p>
                  <div className="flex items-center gap-1">
                    <code className="flex-1 truncate">{link}</code>
                    <Button size="icon-xs" variant="ghost" aria-label="Copy" onClick={() => void navigator.clipboard.writeText(link).then(() => toast.success('Copied'))}><Copy /></Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Remove ${removing?.email}?`}
        description="They lose access to this business immediately." destructive confirmLabel="Remove" loading={m.remove.isPending}
        onConfirm={() => removing && m.remove.mutate(removing.id, { onSuccess: () => setRemoving(null) })} />
    </>
  )
}
