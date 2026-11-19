import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Search, Users } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader, Pagination, TableSkeleton } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { duration, timeAgo, titleCase } from '@receptionist/shared/lib/format'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { useCtx } from '@/lib/tenant'
import { zoneDateTime } from '@/lib/tenant-format'
import { useContact, useContacts, useUpdateContact } from '@/lib/tenant-hooks'
import type { Contact } from '@/lib/tenant-types'
import { OutcomeBadge } from './TenantOverview'

export default function Contacts() {
  const L = useCtx().vertical.labels
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<string | null>(null)
  const q = useDebounced(search, 300)
  const list = useContacts({ q, page, limit: 25 })

  return (
    <>
      <PageHeader title={L.contacts} description="Everyone who has called, messaged or booked. Created automatically by your assistant." />
      <div className="relative mb-4 max-w-sm">
        <Search className="absolute top-2 left-2.5 size-4 text-muted-foreground" />
        <Input className="pl-8" placeholder="Search name, phone or e-mail" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
      </div>
      {list.error && <ErrorBanner error={list.error} />}
      <Card className="gap-0 py-0">
        {list.isLoading ? <TableSkeleton rows={8} cols={5} /> : (list.data?.contacts.length ?? 0) === 0 ? (
          <EmptyState title={`No ${L.contacts.toLowerCase()} yet`} icon={<Users className="size-8" />} />
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Phone</TableHead>
                  <TableHead>Calls</TableHead>
                  <TableHead>{L.appointments}</TableHead>
                  <TableHead>Last seen</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data?.contacts.map((c) => (
                  <TableRow key={c.id} className="cursor-pointer" onClick={() => setOpen(c.id)}>
                    <TableCell className="font-medium">
                      {c.name || 'Unknown'}
                      {c.tags.map((t) => <Badge key={t} variant="secondary" className="ml-2">{t}</Badge>)}
                      {c.do_not_contact && <Badge variant="destructive" className="ml-2">Do not contact</Badge>}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{c.phone}</TableCell>
                    <TableCell>{c.stats?.calls ?? 0}</TableCell>
                    <TableCell>{c.stats?.appointments ?? 0}{c.stats?.no_shows ? ` (${c.stats.no_shows} no-show)` : ''}</TableCell>
                    <TableCell className="text-muted-foreground">{timeAgo(c.last_seen_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pagination page={page} pages={list.data?.pages ?? 1} total={list.data?.total ?? 0} onPage={setPage} label={L.contacts.toLowerCase()} />
          </>
        )}
      </Card>
      <ContactSheet id={open} onClose={() => setOpen(null)} />
    </>
  )
}

function ContactSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const ctx = useCtx()
  const tz = ctx.tenant.timezone
  const q = useContact(id)
  const c = q.data

  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{c?.name || c?.phone || 'Contact'}</SheetTitle>
          <SheetDescription>{c?.phone}</SheetDescription>
        </SheetHeader>
        {q.isLoading && <Loader2 className="mx-auto size-5 animate-spin" />}
        {c && (
          <div className="space-y-5 px-4 pb-6">
            <ContactForm key={c.id} contact={c} />
            <section>
              <h3 className="mb-2 text-sm font-semibold">{ctx.vertical.labels.appointments}</h3>
              {c.appointments.length === 0 ? <p className="text-sm text-muted-foreground">None</p> : c.appointments.map((a) => (
                <div key={a.appointment_id} className="flex justify-between border-b py-1.5 text-sm">
                  <span>{zoneDateTime(a.start, tz)} · {a.service_name}</span><span className="text-muted-foreground">{titleCase(a.status)}</span>
                </div>
              ))}
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold">Calls</h3>
              {c.calls.length === 0 ? <p className="text-sm text-muted-foreground">None</p> : c.calls.map((call) => (
                <div key={call.call_id} className="flex items-center justify-between border-b py-1.5 text-sm">
                  <span>{zoneDateTime(call.started_at, tz)} · {duration(call.duration_s)}</span><OutcomeBadge outcome={call.outcome} />
                </div>
              ))}
            </section>
            {c.messages.length > 0 && (
              <section>
                <h3 className="mb-2 text-sm font-semibold">Messages left</h3>
                {c.messages.map((m) => <p key={m.message_id} className="border-b py-1.5 text-sm">{m.message}</p>)}
              </section>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}

function ContactForm({ contact }: { contact: Contact }) {
  const save = useUpdateContact()
  const [name, setName] = useState(contact.name ?? '')
  const [email, setEmail] = useState(contact.email ?? '')
  const [notes, setNotes] = useState(contact.notes ?? '')
  const [tags, setTags] = useState(contact.tags.join(', '))
  const [dnc, setDnc] = useState(contact.do_not_contact)
  return (
    <div className="grid gap-3">
      <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="E-mail"><Input value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Tags" hint="Comma-separated, e.g. vip, regular"><Input value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
      <Field label="Notes"><Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      <label className="flex items-center gap-2 text-sm"><Switch checked={dnc} onCheckedChange={setDnc} /> Do not contact (no reminders or marketing)</label>
      <Button disabled={save.isPending} onClick={() => save.mutate(
        { id: contact.id, name, email: email || null, notes, tags: tags.split(',').map((t) => t.trim()).filter(Boolean), do_not_contact: dnc },
        { onSuccess: () => toast.success('Saved') })}>
        {save.isPending && <Loader2 className="animate-spin" />} Save
      </Button>
    </div>
  )
}
