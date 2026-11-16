import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { Ban, CheckCircle2, Loader2, ShieldCheck, Upload, XCircle } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, KeyValue, Pagination, TableSkeleton } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { dateTime, timeAgo, titleCase } from '@/lib/format'
import { failureText, MODE_LABEL, useCampaigns, useSalesLead, useSalesLeads, useSalesMutations, type CampaignMode, type GateDecision, type ImportResult, type SalesLead } from '@/lib/sales'
import { useDebounced } from '@/lib/use-debounced'
import { cn } from '@/lib/utils'

const STATUS_STYLE: Record<string, string> = {
  queued: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
  in_progress: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  callback: 'bg-violet-100 text-violet-800 dark:bg-violet-500/20 dark:text-violet-300',
  interested: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  demo_booked: 'bg-emerald-200 text-emerald-900 dark:bg-emerald-500/30 dark:text-emerald-200',
  dnc: 'bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300',
  blocked: 'bg-orange-100 text-orange-800 dark:bg-orange-500/20 dark:text-orange-300',
  bad_number: 'bg-orange-100 text-orange-800 dark:bg-orange-500/20 dark:text-orange-300',
}
const STATUS_LABEL: Record<string, string> = { dnc: 'Do not call', blocked: 'Cannot be called', new: 'Not in a campaign' }
const statusLabel = (s: string) => STATUS_LABEL[s] ?? titleCase(s)

export function LeadStatus({ status }: { status: string }) {
  return <Badge variant="secondary" className={cn('border-0', STATUS_STYLE[status])}>{statusLabel(status)}</Badge>
}

function GateSummary({ gate }: { gate: GateDecision | null }) {
  if (!gate) return <span className="text-muted-foreground">Not checked</span>
  if (gate.allowed) return <span className="flex items-center gap-1 text-emerald-600"><CheckCircle2 className="size-3.5" /> Passed</span>
  return <span className={cn('flex items-center gap-1', gate.permanent ? 'text-destructive' : 'text-amber-600')}><XCircle className="size-3.5 shrink-0" /> {failureText(gate.failures[0])}</span>
}

export function Leads() {
  const [status, setStatus] = useState('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [importing, setImporting] = useState(false)
  const [queueing, setQueueing] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const q = useSalesLeads({ status: status === 'all' ? undefined : status, q: useDebounced(search, 300), page })
  const counts = q.data?.by_status ?? {}

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input className="max-w-xs" placeholder="Search name or number" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
        <SimpleSelect size="sm" value={status} onChange={(v) => { setStatus(v); setPage(1) }}
          options={[{ value: 'all', label: 'All leads' }, ...Object.keys(counts).sort().map((s) => ({ value: s, label: `${statusLabel(s)} (${counts[s]})` }))]} />
        <div className="flex-1" />
        <Button variant="outline" disabled={!counts.new} onClick={() => setQueueing(true)}>Add to a campaign</Button>
        <Button onClick={() => setImporting(true)}><Upload /> Import CSV</Button>
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={6} cols={6} /> : (q.data?.leads.length ?? 0) === 0 ? (
          <EmptyState title="No leads here" description="Import a CSV of businesses. Each number is checked and the ones that can never be called are marked straight away." />
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow><TableHead>Business</TableHead><TableHead>Number</TableHead><TableHead>Status</TableHead><TableHead>Compliance check</TableHead><TableHead>Attempts</TableHead><TableHead>Source</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {q.data?.leads.map((l) => (
                  <TableRow key={l.id} className="cursor-pointer" onClick={() => setOpen(l.id)}>
                    <TableCell><div className="font-medium">{l.business_name || '—'}</div><div className="text-xs text-muted-foreground">{l.contact_name}</div></TableCell>
                    <TableCell className="font-mono text-xs">{l.phone}<div className="font-sans text-muted-foreground">{[l.region_code, l.line_type && titleCase(l.line_type)].filter(Boolean).join(' · ')}</div></TableCell>
                    <TableCell><LeadStatus status={l.status} /></TableCell>
                    <TableCell className="max-w-xs whitespace-normal text-xs"><GateSummary gate={l.gate} /></TableCell>
                    <TableCell className="tabular-nums">{l.attempts}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{l.source}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pagination page={q.data?.page ?? 1} pages={q.data?.pages ?? 1} total={q.data?.total ?? 0} onPage={setPage} label="leads" />
          </>
        )}
      </Card>
      {importing && <ImportDialog onClose={() => setImporting(false)} />}
      {queueing && <QueueDialog sources={q.data?.sources ?? []} onClose={() => setQueueing(false)} />}
      <LeadSheet id={open} onClose={() => setOpen(null)} />
    </>
  )
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const m = useSalesMutations()
  const campaigns = useCampaigns()
  const file = useRef<HTMLInputElement>(null)
  const [csv, setCsv] = useState('')
  const [source, setSource] = useState('')
  const [campaignId, setCampaignId] = useState('none')
  const [mode, setMode] = useState<CampaignMode>('ai_cold_b2b_us')
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const body = (dry_run: boolean, map?: Record<string, string>) => ({
    csv, source, mapping: map ?? (Object.keys(mapping).length ? mapping : undefined), dry_run, mode,
    campaign_id: campaignId === 'none' ? undefined : campaignId,
  })
  const runPreview = (map?: Record<string, string>) =>
    m.importLeads.mutate(body(true, map), { onSuccess: (r) => { setPreview(r); setMapping(r.mapping) }, onError: (e) => toast.error(e.message) })
  const pick = async (f: File | undefined) => {
    if (!f) return
    setCsv(await f.text())
    setPreview(null)
    setMapping({})
    if (!source) setSource(f.name.replace(/\.csv$/i, ''))
  }
  const remap = (field: string, header: string) => {
    const next = { ...mapping }
    if (header === '-') delete next[field]
    else next[field] = header
    setMapping(next)
    runPreview(next)
  }
  const skipped = preview ? preview.invalid + preview.duplicates_in_file + preview.already_leads + preview.customers : 0

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import leads</DialogTitle>
          <DialogDescription>A CSV with one business per row. Only the phone number is required. Keep a note of where the list came from: you may be asked.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="File" className="sm:col-span-2">
            <input ref={file} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => void pick(e.target.files?.[0])} />
            <Button variant="outline" onClick={() => file.current?.click()}><Upload /> {csv ? 'Choose a different file' : 'Choose a CSV file'}</Button>
          </Field>
          <Field label="Where the list came from" hint="e.g. austin-plumbers-nov-2026"><Input value={source} onChange={(e) => setSource(e.target.value)} /></Field>
          <Field label="Add to campaign">
            <SimpleSelect className="w-full" value={campaignId} onChange={setCampaignId}
              options={[{ value: 'none', label: 'Not yet' }, ...(campaigns.data?.campaigns ?? []).filter((c) => c.status !== 'completed').map((c) => ({ value: c.id, label: c.name }))]} />
          </Field>
          {campaignId === 'none' && (
            <Field label="Check them as" className="sm:col-span-2">
              <SimpleSelect className="w-full" value={mode} onChange={(v) => setMode(v as CampaignMode)}
                options={(Object.keys(MODE_LABEL) as CampaignMode[]).map((k) => ({ value: k, label: MODE_LABEL[k] }))} />
            </Field>
          )}
        </div>
        {preview && (
          <div className="space-y-3 text-sm">
            {preview.error && <ErrorBanner error={new Error(preview.error)} />}
            <div className="grid gap-2 sm:grid-cols-3">
              {preview.fields.filter((f) => ['phone', 'business_name', 'contact_name', 'email', 'state', 'vertical'].includes(f)).map((f) => (
                <Field key={f} label={titleCase(f)}>
                  <SimpleSelect size="sm" className="w-full" value={mapping[f] ?? '-'} onChange={(v) => remap(f, v)}
                    options={[{ value: '-', label: '(none)' }, ...preview.headers.map((h) => ({ value: h, label: h }))]} />
                </Field>
              ))}
            </div>
            <div className="rounded-md bg-muted p-3">
              <KeyValue label="Rows in the file">{preview.rows}</KeyValue>
              <KeyValue label="New leads">{preview.imported}</KeyValue>
              <KeyValue label="…of which can never be called">{preview.blocked}</KeyValue>
              <KeyValue label="Skipped">{skipped}</KeyValue>
              <p className="pt-1 text-xs text-muted-foreground">
                Skipped: {preview.invalid} not a valid number{preview.invalid_rows.length ? ` (rows ${preview.invalid_rows.join(', ')})` : ''}, {preview.duplicates_in_file} repeated in the file, {preview.already_leads} already leads, {preview.customers} existing customers.
              </p>
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          {!preview ? (
            <Button disabled={!csv || source.trim().length < 2 || m.importLeads.isPending} onClick={() => runPreview()}>{m.importLeads.isPending && <Loader2 className="animate-spin" />} Preview</Button>
          ) : (
            <Button disabled={!!preview.error || preview.imported === 0 || m.importLeads.isPending}
              onClick={() => m.importLeads.mutate(body(false), { onSuccess: (r) => { toast.success(`${r.imported} leads imported`); onClose() }, onError: (e) => toast.error(e.message) })}>
              {m.importLeads.isPending && <Loader2 className="animate-spin" />} Import {preview.imported} leads
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function QueueDialog({ sources, onClose }: { sources: string[]; onClose: () => void }) {
  const m = useSalesMutations()
  const campaigns = (useCampaigns().data?.campaigns ?? []).filter((c) => c.status !== 'completed')
  const [campaignId, setCampaignId] = useState('')
  const [source, setSource] = useState(sources[0] ?? '')
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add leads to a campaign</DialogTitle>
          <DialogDescription>Leads from one import that are not in a campaign yet. They are checked for that campaign's call type first.</DialogDescription>
        </DialogHeader>
        <Field label="Imported list"><SimpleSelect className="w-full" value={source} onChange={setSource} options={sources.map((s) => ({ value: s, label: s }))} /></Field>
        <Field label="Campaign"><SimpleSelect className="w-full" placeholder="Choose…" value={campaignId} onChange={setCampaignId} options={campaigns.map((c) => ({ value: c.id, label: c.name }))} /></Field>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!campaignId || !source || m.queueLeads.isPending}
            onClick={() => m.queueLeads.mutate({ campaign_id: campaignId, source }, {
              onSuccess: (r) => { toast.success(`${r.queued} queued, ${r.blocked} cannot be called`); onClose() }, onError: (e) => toast.error(e.message) })}>
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Checks({ gate }: { gate: GateDecision }) {
  return (
    <div className="space-y-1">
      {Object.entries(gate.checks).map(([name, result]) => (
        <div key={name} className="flex items-start gap-2 text-xs">
          {result.startsWith('fail') ? <XCircle className="mt-0.5 size-3.5 shrink-0 text-destructive" /> : <CheckCircle2 className={cn('mt-0.5 size-3.5 shrink-0', result === 'n/a' ? 'text-muted-foreground' : 'text-emerald-600')} />}
          <span><span className="font-medium">{titleCase(name)}</span> <span className="text-muted-foreground">{result.replace(/^(pass|fail):?\s*/, '') || (result === 'n/a' ? 'not needed' : '')}</span></span>
        </div>
      ))}
    </div>
  )
}

function LeadSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useSalesLead(id)
  const m = useSalesMutations()
  const [confirmDnc, setConfirmDnc] = useState(false)
  const lead: SalesLead | undefined = q.data?.lead
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{lead?.business_name || lead?.phone || 'Lead'}</SheetTitle>
          <SheetDescription>{lead ? `${lead.phone} · ${lead.source}` : ''}</SheetDescription>
        </SheetHeader>
        {lead && (
          <div className="space-y-5 px-4 pb-6 text-sm">
            <div>
              <KeyValue label="Status"><LeadStatus status={lead.status} /></KeyValue>
              <KeyValue label="Contact">{[lead.contact_name, lead.email].filter(Boolean).join(' · ') || '—'}</KeyValue>
              <KeyValue label="Where">{[lead.region_code, lead.country, lead.timezone || lead.timezones.join(' / ')].filter(Boolean).join(' · ') || 'Unknown'}</KeyValue>
              <KeyValue label="Line">{lead.line_type ? titleCase(lead.line_type) : 'Not checked'}</KeyValue>
              <KeyValue label="Attempts">{lead.attempts}{lead.next_eligible_at ? ` · next ${dateTime(lead.next_eligible_at)}` : ''}</KeyValue>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={m.checkLead.isPending}
                onClick={() => m.checkLead.mutate(lead.id, { onSuccess: (r) => toast[r.decision.allowed ? 'success' : 'message'](r.decision.allowed ? 'Passes every check right now' : failureText(r.decision.failures[0])), onError: (e) => toast.error(e.message) })}>
                {m.checkLead.isPending ? <Loader2 className="animate-spin" /> : <ShieldCheck />} Check now
              </Button>
              {lead.status !== 'dnc' && <Button size="sm" variant="outline" className="text-destructive" onClick={() => setConfirmDnc(true)}><Ban /> Do not call</Button>}
            </div>
            <section>
              <h3 className="mb-2 font-medium">Compliance checks</h3>
              {(lead.gate_log ?? []).length === 0 ? <p className="text-muted-foreground">Not checked yet.</p> : (
                <div className="space-y-3">
                  {(lead.gate_log ?? []).slice(0, 6).map((g, i) => (
                    <div key={i} className="rounded-md border p-2">
                      <div className="mb-1 flex items-center justify-between text-xs">
                        <span className={g.allowed ? 'font-medium text-emerald-600' : 'font-medium text-destructive'}>{g.allowed ? 'Allowed' : 'Not allowed'}</span>
                        <span className="text-muted-foreground">{dateTime(g.at)}</span>
                      </div>
                      <Checks gate={g} />
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section>
              <h3 className="mb-2 font-medium">Calls</h3>
              {(q.data?.attempts.length ?? 0) === 0 ? <p className="text-muted-foreground">Never called.</p> : q.data?.attempts.map((a) => (
                <div key={a.id} className="border-b py-2 last:border-0">
                  <div className="flex justify-between"><span className="font-medium">{titleCase(a.outcome ?? a.status)}</span><span className="text-xs text-muted-foreground">{timeAgo(a.created_at)}</span></div>
                  {a.summary && <p className="text-xs text-muted-foreground">{a.summary}</p>}
                </div>
              ))}
            </section>
            {(q.data?.consent.length ?? 0) > 0 && (
              <section>
                <h3 className="mb-2 font-medium">Consent</h3>
                {q.data?.consent.map((c, i) => (
                  <div key={i} className="text-xs">{titleCase(c.type)} via {c.source}, {dateTime(c.at)}{c.revoked_at ? ` — withdrawn ${dateTime(c.revoked_at)}` : ''}</div>
                ))}
              </section>
            )}
          </div>
        )}
        <ConfirmDialog open={confirmDnc} onOpenChange={setConfirmDnc} destructive title="Never call this number again?" confirmLabel="Add to do-not-call list"
          description="This is permanent. The number can't be taken off the list from the dashboard." loading={m.dncLead.isPending}
          onConfirm={() => lead && m.dncLead.mutate(lead.id, { onSuccess: () => { toast.success('Added to the do-not-call list'); setConfirmDnc(false) } })} />
      </SheetContent>
    </Sheet>
  )
}
