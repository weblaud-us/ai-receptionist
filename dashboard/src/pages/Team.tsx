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
import { useTeam, useTeamMutations } from '@/lib/tenant-hooks'
import type { TeamMember } from '@/lib/tenant-types'

const ROLES = [
  { value: 'staff', label: 'Staff — calls, messages, bookings' },
  { value: 'admin', label: 'Admin — plus settings and knowledge' },
  { value: 'owner', label: 'Owner — everything, incl. team and billing' },
]

export default function Team() {
  const { can } = useTenant()
  const { user } = useAuth()
  const q = useTeam()
  const m = useTeamMutations()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('staff')
  const [link, setLink] = useState<string | null>(null)
  const [removing, setRemoving] = useState<TeamMember | null>(null)
  const owner = can('owner')

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
                        <SimpleSelect size="sm" value={mem.role} onChange={(v) => m.role.mutate({ id: mem.id, role: v }, { onSuccess: () => toast.success('Role updated') })}
                          options={ROLES.map((r) => ({ value: r.value, label: ROLE_LABEL[r.value] }))} />
                      ) : <Badge variant="secondary">{ROLE_LABEL[mem.role]}</Badge>}
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
              <Field label="Role"><SimpleSelect value={role} onChange={setRole} options={ROLES} /></Field>
              <Button disabled={!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || m.invite.isPending}
                onClick={() => m.invite.mutate({ email: email.trim(), role }, {
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
