import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
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
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { useCtx } from '@/lib/tenant'
import { zoneDateTime } from '@/lib/tenant-format'
import { useContact, useContacts, useResources, useUpdateContact } from '@/lib/tenant-hooks'
import type { Contact } from '@/lib/tenant-types'
import { OutcomeBadge } from './TenantOverview'
import { ContactWhatsApp } from '@/components/app/contact-whatsapp'

export default function Contacts() {
  const L = useCtx().vertical.labels
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [params, setParams] = useSearchParams()
  const [open, setOpenState] = useState<string | null>(() => params.get('contact'))
  const setOpen = (id: string | null) => {
    setOpenState(id)
    if (!id && params.has('contact')) setParams({}, { replace: true })   // drop the deep link once closed
  }
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
  const L = ctx.vertical.labels
  const q = useContact(id)
  const c = q.data
  const upcoming = c?.appointments.filter((a) => new Date(a.start) > new Date() && !['cancelled', 'no_show'].includes(a.status)) ?? []

  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{c ? c.name || 'Unknown caller' : L.contact}</SheetTitle>
          <SheetDescription className="font-mono">{c?.phone}{c?.email ? ` · ${c.email}` : ''}</SheetDescription>
        </SheetHeader>
        {q.isLoading && <Loader2 className="mx-auto size-5 animate-spin" />}
        {q.error && <div className="px-4"><ErrorBanner error={q.error} onRetry={() => q.refetch()} /></div>}
        {c && (
          <div className="space-y-6 px-4 pb-6">
            <div className="grid grid-cols-3 gap-2 text-center">
              <Stat label="Calls" value={c.stats?.calls ?? c.calls.length} />
              <Stat label={L.appointments} value={c.stats?.appointments ?? c.appointments.length} />
              <Stat label="No-shows" value={c.stats?.no_shows ?? 0} warn={!!c.stats?.no_shows} />
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {c.first_seen_at && <span>First contact {zoneDateTime(c.first_seen_at, tz)}</span>}
              {c.last_seen_at && <span>Last seen {timeAgo(c.last_seen_at)}</span>}
              {c.do_not_contact && <Badge variant="destructive">Do not contact</Badge>}
            </div>
            {upcoming.length > 0 && (
              <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm">
                <div className="mb-1 text-xs font-medium text-primary">Next {L.appointment.toLowerCase()}</div>
                {zoneDateTime(upcoming[upcoming.length - 1].start, tz)} · {upcoming[upcoming.length - 1].service_name}
                {upcoming[upcoming.length - 1].resource_name && ` with ${upcoming[upcoming.length - 1].resource_name}`}
              </div>
            )}
            <section>
              <h3 className="mb-2 text-sm font-semibold">Calls</h3>
              {c.calls.length === 0 ? <p className="text-sm text-muted-foreground">None</p> : c.calls.map((call) => (
                <Link key={call.call_id} to={`/calls?call=${encodeURIComponent(call.call_id)}`}
                  className="block border-b py-2 text-sm hover:bg-muted/40">
                  <div className="flex items-center justify-between gap-2">
                    <span>{zoneDateTime(call.started_at, tz)} · {duration(call.duration_s)}</span><OutcomeBadge outcome={call.outcome} />
                  </div>
                  {call.summary && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{call.summary}</p>}
                </Link>
              ))}
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold">{L.appointments}</h3>
              {c.appointments.length === 0 ? <p className="text-sm text-muted-foreground">None</p> : c.appointments.map((a) => (
                <div key={a.appointment_id} className="flex justify-between gap-2 border-b py-1.5 text-sm">
                  <span>{zoneDateTime(a.start, tz)} · {a.service_name}{a.resource_name ? ` · ${a.resource_name}` : ''}</span>
                  <span className="shrink-0 text-muted-foreground">{titleCase(a.status)}</span>
                </div>
              ))}
            </section>
            {c.messages.length > 0 && (
              <section>
                <h3 className="mb-2 text-sm font-semibold">Messages left</h3>
                {c.messages.map((m) => (
                  <div key={m.message_id} className="border-b py-1.5 text-sm">
                    <p>{m.message}</p>
                    <p className="text-xs text-muted-foreground">{timeAgo(m.created_at)} · {titleCase(m.status)}</p>
                  </div>
                ))}
              </section>
            )}
            <ContactWhatsApp key={`wa-${c.id}`} contact={c} />
            <section>
              <h3 className="mb-2 text-sm font-semibold">Details</h3>
              <ContactForm key={c.id} contact={c} />
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}

function Stat({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <div className="rounded-lg border p-2">
      <div className={`text-lg font-semibold ${warn ? 'text-destructive' : ''}`}>{value}</div>
      <div className="truncate text-xs text-muted-foreground">{label}</div>
    </div>
  )
}

function ContactForm({ contact }: { contact: Contact }) {
  const save = useUpdateContact()
  const ctx = useCtx()
  const L = ctx.vertical.labels
  // The patient's regular dentist: the assistant books them with this person first (plan E4).
  const resources = (useResources().data?.resources ?? []).filter((r) => r.active)
  const [regular, setRegular] = useState(contact.preferred_resource_id ?? '')
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
      {resources.length > 1 && (
        <Field label={`Regular ${L.resource.toLowerCase()}`} hint="Your assistant offers this person first when they call to book.">
          <SimpleSelect value={regular} onChange={setRegular}
            options={[{ value: '', label: 'No preference' }, ...resources.map((r) => ({ value: r.id, label: r.name }))]} />
        </Field>
      )}
      <label className="flex items-center gap-2 text-sm"><Switch checked={dnc} onCheckedChange={setDnc} /> Do not contact (no reminders or marketing)</label>
      <Button disabled={save.isPending} onClick={() => save.mutate(
        { id: contact.id, name, email: email || null, notes, tags: tags.split(',').map((t) => t.trim()).filter(Boolean), do_not_contact: dnc,
          ...(resources.length > 1 ? { preferred_resource_id: regular } : {}) },
        { onSuccess: () => toast.success('Saved') })}>
        {save.isPending && <Loader2 className="animate-spin" />} Save
      </Button>
    </div>
  )
}
