import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Plus, Rocket } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { dateOnly } from '@receptionist/shared/lib/format'
import { usePilots, usePlatformMutations, usePlatformTenants, type Pilot } from '@/lib/platform'

const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v)}%`)

/** YYYY-MM-DD, `days` from now — computed in state initialisers, never during render. */
const isoDay = (days = 0) => new Date(Date.now() + days * 86400_000).toISOString().slice(0, 10)

function PilotDialog({ open, onOpenChange, pilot }: { open: boolean; onOpenChange: (o: boolean) => void; pilot?: Pilot }) {
  const tenants = usePlatformTenants(open && !pilot)
  const { update } = usePlatformMutations()
  const [tenantId, setTenantId] = useState(pilot?.id ?? '')
  const [contact, setContact] = useState(pilot?.pilot.contact_name ?? '')
  const [day, setDay] = useState(pilot?.pilot.feedback_day ?? '')
  const [start, setStart] = useState(() => pilot?.pilot.started_at?.slice(0, 10) ?? isoDay())
  const [end, setEnd] = useState(() => pilot?.pilot.ends_at?.slice(0, 10) ?? isoDay(60))
  const [caseStudy, setCaseStudy] = useState(pilot?.pilot.case_study_ok ?? false)
  const [notes, setNotes] = useState(pilot?.pilot.notes ?? '')
  const save = (active: boolean) =>
    update.mutate(
      {
        id: tenantId,
        pilot: { active, contact_name: contact, feedback_day: day, started_at: `${start}T00:00:00Z`, ends_at: `${end}T00:00:00Z`, case_study_ok: caseStudy, notes },
      },
      {
        onSuccess: () => {
          toast.success(active ? 'Pilot saved' : 'Pilot ended')
          onOpenChange(false)
        },
        onError: (e) => toast.error(e.message),
      },
    )
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{pilot ? `Pilot: ${pilot.name}` : 'Start a pilot'}</DialogTitle></DialogHeader>
        <div className="grid gap-3 text-sm">
          {!pilot && (
            <Field label="Business" hint="Provision it on Businesses first; set its plan to manual so it isn’t billed.">
              <SimpleSelect value={tenantId} onChange={setTenantId} placeholder="Choose a business"
                options={(tenants.data?.tenants ?? []).map((t) => ({ value: t.id, label: t.name }))} />
            </Field>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Contact"><Input value={contact} onChange={(e) => setContact(e.target.value)} /></Field>
            <Field label="Weekly call" hint="e.g. Friday 10:00"><Input value={day} onChange={(e) => setDay(e.target.value)} /></Field>
            <Field label="Starts"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
            <Field label="Ends"><Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          </div>
          <label className="flex items-center gap-2"><Checkbox checked={caseStudy} onCheckedChange={(v) => setCaseStudy(v === true)} /> Agreed to a testimonial / case study</label>
          <Field label="Notes"><Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        </div>
        <DialogFooter>
          {pilot && <Button variant="outline" onClick={() => save(false)} disabled={update.isPending}>End pilot</Button>}
          <Button onClick={() => save(true)} disabled={!tenantId || update.isPending}>
            {update.isPending && <Loader2 className="animate-spin" />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Phase 7, Task 7.1: free pilot businesses and their weekly scorecard. */
export default function Pilots() {
  const q = usePilots()
  const [editing, setEditing] = useState<Pilot | undefined>()
  const [open, setOpen] = useState(false)
  const pilots = q.data?.pilots ?? []
  return (
    <>
      <PageHeader
        title="Pilots"
        description="3–5 free businesses, overflow calls first, a 15-minute call every week. Each one gets this scorecard by e-mail on Mondays."
        actions={<Button onClick={() => { setEditing(undefined); setOpen(true) }}><Plus /> Start a pilot</Button>}
      />
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.isLoading && <Skeleton className="h-64 w-full" />}
      {!q.isLoading && pilots.length === 0 && (
        <EmptyState icon={<Rocket className="size-8" />} title="No pilots yet" description="Pick businesses in your launch verticals you can visit in person." />
      )}
      {pilots.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Last 7 days</CardTitle>
            <CardDescription>“Would have missed” counts every call for overflow / after-hours forwarding, otherwise calls while closed.</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Business</TableHead>
                  <TableHead className="text-right">Answered</TableHead>
                  <TableHead className="text-right">Would have missed</TableHead>
                  <TableHead className="text-right">Bookings</TableHead>
                  <TableHead className="text-right">No human</TableHead>
                  <TableHead className="text-right">p50</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                  <TableHead className="text-right">Reviewed (30 d)</TableHead>
                  <TableHead>Ends</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pilots.map((p) => {
                  const r = p.last_7_days
                  return (
                    <TableRow key={p.id} className="cursor-pointer" onClick={() => { setEditing(p); setOpen(true) }}>
                      <TableCell>
                        <div className="font-medium">{p.name}</div>
                        <div className="text-xs text-muted-foreground">{p.vertical} · {p.answering_mode.replace('_', ' ')}{p.pilot.contact_name && ` · ${p.pilot.contact_name}`}</div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{r.calls_answered}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.would_have_missed}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.bookings}</TableCell>
                      <TableCell className="text-right tabular-nums">{pct(r.handled_without_human_pct)}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.latency_p50_s === null ? '—' : `${r.latency_p50_s.toFixed(1)} s`}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.satisfaction ? `${r.satisfaction.score}/10` : '—'}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {p.reviews_30d.reviewed}
                        {p.reviews_30d.issues > 0 && <Badge variant="destructive" className="ml-2">{p.reviews_30d.issues} issues</Badge>}
                      </TableCell>
                      <TableCell>{dateOnly(p.pilot.ends_at)}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      {open && <PilotDialog key={editing?.id ?? 'new'} open={open} onOpenChange={setOpen} pilot={editing} />}
    </>
  )
}
