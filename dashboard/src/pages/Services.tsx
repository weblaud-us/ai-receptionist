import { useState } from 'react'
import { toast } from 'sonner'
import { CalendarOff, Loader2, Pencil, Plus, Trash2 } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, PageHeader, TableSkeleton } from '@/components/app/blocks'
import { HoursEditor } from '@/components/app/hours-editor'
import { SimpleSelect } from '@/components/app/simple-select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { dateOnly, titleCase } from '@/lib/format'
import { useCtx } from '@/lib/tenant'
import { zoneToday } from '@/lib/tenant-format'
import {
  useExceptionMutations,
  useExceptions,
  useResourceMutations,
  useResources,
  useServiceMutations,
  useServices,
} from '@/lib/tenant-hooks'
import type { HoursEntry, Resource, Service } from '@/lib/tenant-types'

export default function ServicesPage() {
  const ctx = useCtx()
  const L = ctx.vertical.labels
  return (
    <>
      <PageHeader title={`${L.services} & ${L.resources.toLowerCase()}`}
        description="What callers can book, who does it, and when you're closed. Your assistant reads this on every call." />
      <Tabs defaultValue="services">
        <TabsList>
          <TabsTrigger value="services">{L.services}</TabsTrigger>
          <TabsTrigger value="resources">{L.resources}</TabsTrigger>
          <TabsTrigger value="closures">Closures & holidays</TabsTrigger>
        </TabsList>
        <TabsContent value="services"><ServicesTab /></TabsContent>
        <TabsContent value="resources"><ResourcesTab /></TabsContent>
        <TabsContent value="closures"><ClosuresTab /></TabsContent>
      </Tabs>
    </>
  )
}

/* ───────────── services ───────────── */

function ServicesTab() {
  const ctx = useCtx()
  const L = ctx.vertical.labels
  const q = useServices()
  const resources = useResources()
  const m = useServiceMutations()
  const [editing, setEditing] = useState<Partial<Service> | null>(null)
  const names = Object.fromEntries((resources.data?.resources ?? []).map((r) => [r.id, r.name]))

  return (
    <Card className="mt-3 gap-0 py-0">
      <div className="flex justify-end border-b p-3">
        <Button size="sm" onClick={() => setEditing({ duration_min: 30, buffer_min: 0, ai_bookable: true, active: true, resource_ids: [] })}>
          <Plus /> Add {L.service.toLowerCase()}
        </Button>
      </div>
      {q.error && <div className="p-4"><ErrorBanner error={q.error} /></div>}
      {q.isLoading ? <TableSkeleton rows={4} cols={5} /> : (q.data?.services.length ?? 0) === 0 ? (
        <EmptyState title={`No ${L.services.toLowerCase()} yet`} description="Add what callers can book, with how long it takes." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Length</TableHead>
              <TableHead>Price</TableHead>
              <TableHead>{L.resources}</TableHead>
              <TableHead>AI can book</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {q.data?.services.map((s) => (
              <TableRow key={s.id} className={s.active ? undefined : 'opacity-50'}>
                <TableCell className="font-medium">{s.name}{!s.active && <Badge variant="outline" className="ml-2">Inactive</Badge>}</TableCell>
                <TableCell>{s.duration_min} min{s.buffer_min ? ` + ${s.buffer_min} buffer` : ''}</TableCell>
                <TableCell>{s.price?.display || '—'}</TableCell>
                <TableCell className="text-muted-foreground">{s.resource_ids.length ? s.resource_ids.map((id) => names[id] ?? '?').join(', ') : 'Anyone'}</TableCell>
                <TableCell>{s.ai_bookable ? 'Yes' : 'No'}</TableCell>
                <TableCell className="text-right">
                  <Button size="icon-xs" variant="ghost" aria-label="Edit" onClick={() => setEditing(s)}><Pencil /></Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {editing && (
        <ServiceDialog value={editing} resources={resources.data?.resources ?? []} saving={m.create.isPending || m.update.isPending}
          onClose={() => setEditing(null)}
          onSave={(body) => {
            const done = { onSuccess: () => { toast.success('Saved'); setEditing(null) } }
            if (editing.id) m.update.mutate({ id: editing.id, ...body }, done)
            else m.create.mutate(body, done)
          }} />
      )}
    </Card>
  )
}

function ServiceDialog({ value, resources, saving, onClose, onSave }: {
  value: Partial<Service>
  resources: Resource[]
  saving: boolean
  onClose: () => void
  onSave: (body: Record<string, unknown>) => void
}) {
  const L = useCtx().vertical.labels
  const [s, setS] = useState(value)
  const set = (patch: Partial<Service>) => setS((prev) => ({ ...prev, ...patch }))
  const ids = s.resource_ids ?? []
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{value.id ? `Edit ${L.service.toLowerCase()}` : `New ${L.service.toLowerCase()}`}</DialogTitle></DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" className="sm:col-span-2"><Input value={s.name ?? ''} onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label="Length (minutes)"><Input type="number" min={5} value={s.duration_min ?? 30} onChange={(e) => set({ duration_min: Number(e.target.value) })} /></Field>
          <Field label="Buffer after (minutes)" hint="Clean-up or travel time"><Input type="number" min={0} value={s.buffer_min ?? 0} onChange={(e) => set({ buffer_min: Number(e.target.value) })} /></Field>
          <Field label="Price as callers hear it" hint='e.g. "from $45" — leave empty to not quote' className="sm:col-span-2">
            <Input value={s.price?.display ?? ''} onChange={(e) => set({ price: { ...(s.price ?? {}), display: e.target.value } })} />
          </Field>
          <Field label="Description" className="sm:col-span-2"><Textarea rows={2} value={s.description ?? ''} onChange={(e) => set({ description: e.target.value })} /></Field>
          {resources.length > 0 && (
            <Field label={`Who can do it`} hint="None ticked = anyone" className="sm:col-span-2">
              <div className="flex flex-wrap gap-3">
                {resources.filter((r) => r.active).map((r) => (
                  <label key={r.id} className="flex items-center gap-2 text-sm">
                    <Checkbox checked={ids.includes(r.id)}
                      onCheckedChange={(c) => set({ resource_ids: c ? [...ids, r.id] : ids.filter((x) => x !== r.id) })} />
                    {r.name}
                  </label>
                ))}
              </div>
            </Field>
          )}
          <label className="flex items-center gap-2 text-sm"><Switch checked={s.ai_bookable ?? true} onCheckedChange={(v) => set({ ai_bookable: v })} /> Assistant can book it</label>
          <label className="flex items-center gap-2 text-sm"><Switch checked={s.active ?? true} onCheckedChange={(v) => set({ active: v })} /> Active</label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!s.name?.trim() || saving}
            onClick={() => onSave({ name: s.name?.trim(), duration_min: s.duration_min, buffer_min: s.buffer_min, price: s.price?.display ? s.price : null,
              description: s.description ?? '', resource_ids: ids, ai_bookable: s.ai_bookable, active: s.active })}>
            {saving && <Loader2 className="animate-spin" />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ───────────── resources (staff, tables, rooms) ───────────── */

const KINDS = ['staff', 'chair', 'room', 'table', 'bay', 'technician', 'other']

function ResourcesTab() {
  const L = useCtx().vertical.labels
  const q = useResources()
  const m = useResourceMutations()
  const [editing, setEditing] = useState<Partial<Resource> | null>(null)
  const [removing, setRemoving] = useState<Resource | null>(null)
  return (
    <Card className="mt-3 gap-0 py-0">
      <div className="flex justify-end border-b p-3">
        <Button size="sm" onClick={() => setEditing({ kind: 'staff', capacity: 1, hours: null, active: true })}><Plus /> Add {L.resource.toLowerCase()}</Button>
      </div>
      {q.isLoading ? <TableSkeleton rows={3} cols={4} /> : (q.data?.resources.length ?? 0) === 0 ? (
        <EmptyState title={`No ${L.resources.toLowerCase()} yet`} description="Without any, every booking simply uses your business hours." />
      ) : (
        <Table>
          <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Kind</TableHead><TableHead>Capacity</TableHead><TableHead>Hours</TableHead><TableHead /></TableRow></TableHeader>
          <TableBody>
            {q.data?.resources.map((r) => (
              <TableRow key={r.id} className={r.active ? undefined : 'opacity-50'}>
                <TableCell className="font-medium">{r.name}</TableCell>
                <TableCell>{titleCase(r.kind)}</TableCell>
                <TableCell>{r.capacity}</TableCell>
                <TableCell className="text-muted-foreground">{r.hours ? `${new Set(r.hours.map((h) => h.dow)).size} days a week` : 'Business hours'}</TableCell>
                <TableCell className="text-right">
                  <Button size="icon-xs" variant="ghost" aria-label="Edit" onClick={() => setEditing(r)}><Pencil /></Button>
                  {r.active && <Button size="icon-xs" variant="ghost" aria-label="Deactivate" onClick={() => setRemoving(r)}><Trash2 /></Button>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {editing && <ResourceDialog value={editing} saving={m.create.isPending || m.update.isPending} onClose={() => setEditing(null)}
        onSave={(body) => {
          const done = { onSuccess: () => { toast.success('Saved'); setEditing(null) } }
          if (editing.id) m.update.mutate({ id: editing.id, ...body, clear_hours: body.hours === null }, done)
          else m.create.mutate(body, done)
        }} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Deactivate ${removing?.name}?`}
        description="They won't be offered for new bookings. Existing upcoming bookings must be moved first." destructive confirmLabel="Deactivate"
        loading={m.remove.isPending} onConfirm={() => removing && m.remove.mutate(removing.id, { onSuccess: () => setRemoving(null) })} />
    </Card>
  )
}

function ResourceDialog({ value, saving, onClose, onSave }: {
  value: Partial<Resource>
  saving: boolean
  onClose: () => void
  onSave: (body: Record<string, unknown> & { hours: HoursEntry[] | null }) => void
}) {
  const ctx = useCtx()
  const [r, setR] = useState(value)
  const own = r.hours != null
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader><DialogTitle>{value.id ? `Edit ${r.name}` : `New ${ctx.vertical.labels.resource.toLowerCase()}`}</DialogTitle></DialogHeader>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Name"><Input value={r.name ?? ''} onChange={(e) => setR({ ...r, name: e.target.value })} /></Field>
          <Field label="Kind"><SimpleSelect value={r.kind ?? 'staff'} onChange={(v) => setR({ ...r, kind: v as Resource['kind'] })} options={KINDS.map((k) => ({ value: k, label: titleCase(k) }))} /></Field>
          <Field label="Capacity" hint="Seats at a table, people in a room"><Input type="number" min={1} value={r.capacity ?? 1} onChange={(e) => setR({ ...r, capacity: Number(e.target.value) })} /></Field>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={own} onCheckedChange={(v) => setR({ ...r, hours: v ? ctx.tenant.hours.map((h) => ({ ...h })) : null })} />
          Has their own working hours (otherwise: business hours)
        </label>
        {own && <HoursEditor value={r.hours ?? []} onChange={(hours) => setR({ ...r, hours })} />}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!r.name?.trim() || saving}
            onClick={() => onSave({ name: r.name?.trim(), kind: r.kind, capacity: r.capacity, hours: r.hours ?? null, active: r.active ?? true })}>
            {saving && <Loader2 className="animate-spin" />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ───────────── closures ───────────── */

function ClosuresTab() {
  const ctx = useCtx()
  const q = useExceptions()
  const resources = useResources()
  const m = useExceptionMutations()
  const today = zoneToday(ctx.tenant.timezone)
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState(today)
  const [who, setWho] = useState('all')
  const [reason, setReason] = useState('')
  const names = Object.fromEntries((resources.data?.resources ?? []).map((r) => [r.id, r.name]))
  return (
    <Card className="mt-3 gap-0 py-0">
      <div className="grid gap-3 border-b p-4 sm:grid-cols-5 sm:items-end">
        <Field label="From"><Input type="date" value={from} min={today} onChange={(e) => { setFrom(e.target.value); if (e.target.value > to) setTo(e.target.value) }} /></Field>
        <Field label="To (inclusive)"><Input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></Field>
        <Field label="Who"><SimpleSelect value={who} onChange={setWho} options={[{ value: 'all', label: 'Whole business' }, ...(resources.data?.resources ?? []).map((r) => ({ value: r.id, label: r.name }))]} /></Field>
        <Field label="Reason"><Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Holiday" /></Field>
        <Button disabled={m.create.isPending}
          onClick={() => m.create.mutate({ date_from: from, date_to: to, resource_id: who === 'all' ? null : who, reason },
            { onSuccess: () => { toast.success('Closure added'); setReason('') } })}>
          <CalendarOff /> Add closure
        </Button>
      </div>
      {(q.data?.exceptions.length ?? 0) === 0 ? <EmptyState title="No closures" description="Holidays and days off block bookings automatically." /> : (
        <Table>
          <TableBody>
            {q.data?.exceptions.map((e) => (
              <TableRow key={e.id}>
                <TableCell className="font-medium">{dateOnly(e.date_from)}{e.date_to !== e.date_from && ` – ${dateOnly(e.date_to)}`}</TableCell>
                <TableCell>{e.resource_id ? names[e.resource_id] ?? 'Staff member' : 'Whole business'}</TableCell>
                <TableCell className="text-muted-foreground">{e.reason}</TableCell>
                <TableCell className="text-right"><Button size="icon-xs" variant="ghost" aria-label="Remove" onClick={() => m.remove.mutate(e.id)}><Trash2 /></Button></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  )
}
