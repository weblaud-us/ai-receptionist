import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { Ban, Bookmark, Brain, CheckCircle2, Copy, Loader2, Plus, RotateCcw, Search, ShieldCheck, Upload, X, XCircle } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, KeyValue, Pagination, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateTime, timeAgo, titleCase } from '@receptionist/shared/lib/format'
import {
  failureText, MODE_LABEL, STAGE_LABEL, useCampaigns, useCrmMutations, useDuplicates, useFollowUpMutations, usePeople, useSalesLead, useSalesLeads,
  useSalesMutations, useSalesStatus, useViews,
  type Activity, type Person, type CampaignMode, type GateDecision, type ImportResult, type LeadPatch, type SalesLead, type SalesTask, type Stage,
} from '@/lib/sales'
import { useCan } from '@/lib/roles'
import { useConfirm } from '@/lib/use-confirm'
import { LeadCrm } from '@/components/sales/crm-lead'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { cn } from '@receptionist/shared/lib/utils'

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
  const can = useCan()
  const confirm = useConfirm()
  const [status, setStatus] = useState('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [owner, setOwner] = useState('')
  const [minFit, setMinFit] = useState('')
  const [sort, setSort] = useState<'newest' | 'fit' | 'updated'>('newest')
  const [importing, setImporting] = useState(false)
  const [queueing, setQueueing] = useState(false)
  const [dupes, setDupes] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const people = usePeople().data?.people ?? []
  const views = useViews('leads').data?.views ?? []
  const crm = useCrmMutations()
  const filters = { status: status === 'all' ? undefined : status, owner_id: owner || undefined, min_fit: minFit ? Number(minFit) : undefined, sort }
  const q = useSalesLeads({ ...filters, q: useDebounced(search, 300), page })
  const counts = q.data?.by_status ?? {}
  const rows = q.data?.leads ?? []
  const togglePick = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const applyView = (id: string) => {
    const v = views.find((x) => x.id === id)
    if (!v) return
    setStatus(String(v.filters.status ?? 'all'))
    setOwner(String(v.filters.owner_id ?? ''))
    setMinFit(v.filters.min_fit !== undefined ? String(v.filters.min_fit) : '')
    setSearch(String(v.filters.q ?? ''))
    setPage(1)
  }
  const saveView = () => {
    const name = window.prompt('Name this view')
    if (!name || name.trim().length < 2) return
    crm.saveView.mutate({ name: name.trim(), kind: 'leads', shared: false,
      filters: Object.fromEntries(Object.entries({ status: status === 'all' ? '' : status, owner_id: owner, min_fit: minFit, q: search }).filter(([, v]) => v !== '')) },
      { onSuccess: () => toast.success('View saved') })
  }
  const runBulk = (action: 'assign' | 'stage' | 'tag' | 'queue', value: unknown) =>
    crm.bulk.mutate({ lead_ids: [...picked], action, value }, {
      onSuccess: (r) => { toast.success(`Done: ${Object.entries(r).map(([k, v]) => `${v} ${k}`).join(', ')}`); setPicked(new Set()) },
      onError: (e) => toast.error(e.message),
    })
  // Queueing starts real calls (once each lead passes the checks): ask first.
  const bulk = (action: 'assign' | 'stage' | 'tag' | 'queue', value: unknown) =>
    action === 'queue'
      ? confirm.ask({
        title: `Add ${picked.size} lead${picked.size === 1 ? '' : 's'} to this campaign?`,
        description: 'Each one is called once it passes the compliance checks, if the campaign is running.',
        confirmLabel: 'Add to campaign', run: () => runBulk(action, value),
      })
      : runBulk(action, value)

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input className="max-w-xs" placeholder="Search name or number" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
        <SimpleSelect size="sm" value={status} onChange={(v) => { setStatus(v); setPage(1) }}
          options={[{ value: 'all', label: 'All leads' }, ...Object.keys(counts).filter((s) => s !== 'merged').sort().map((s) => ({ value: s, label: `${statusLabel(s)} (${counts[s]})` }))]} />
        <SimpleSelect size="sm" value={owner} onChange={(v) => { setOwner(v); setPage(1) }}
          options={[{ value: '', label: 'Any owner' }, { value: 'me', label: 'Mine' }, { value: 'none', label: 'Nobody' }, ...people.map((p) => ({ value: p.id, label: p.name }))]} />
        <SimpleSelect size="sm" value={minFit} onChange={(v) => { setMinFit(v); setPage(1) }}
          options={[{ value: '', label: 'Any fit' }, { value: '60', label: 'Fit 60+' }, { value: '80', label: 'Fit 80+' }]} />
        <SimpleSelect size="sm" value={sort} onChange={(v) => setSort(v as typeof sort)}
          options={[{ value: 'newest', label: 'Newest' }, { value: 'fit', label: 'Best fit' }, { value: 'updated', label: 'Recently active' }]} />
        {views.length > 0 && (
          <SimpleSelect size="sm" value="" onChange={applyView} options={[{ value: '', label: 'Saved views…' }, ...views.map((v) => ({ value: v.id, label: v.name }))]} />
        )}
        <Button size="sm" variant="ghost" onClick={saveView}><Bookmark /> Save view</Button>
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={() => setDupes(true)}><Copy /> Duplicates</Button>
        {can('manager') && <>
          <Button variant="outline" disabled={!counts.new} onClick={() => setQueueing(true)}>Add to a campaign</Button>
          <Button onClick={() => setImporting(true)}><Upload /> Import CSV</Button>
        </>}
      </div>
      {picked.size > 0 && can('manager') && (
        <BulkBar count={picked.size} people={people} onClear={() => setPicked(new Set())} onAction={bulk} pending={crm.bulk.isPending} />
      )}
      {q.error && <ErrorBanner error={q.error} />}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={6} cols={6} /> : rows.length === 0 ? (
          <EmptyState title="No leads here" description="Import a CSV of businesses. Each number is checked and the ones that can never be called are marked straight away." />
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  {can('manager') && <TableHead className="w-8"><Checkbox checked={rows.every((r) => picked.has(r.id))}
                    onCheckedChange={(v) => setPicked(v ? new Set(rows.map((r) => r.id)) : new Set())} /></TableHead>}
                  <TableHead>Business</TableHead><TableHead>Number</TableHead><TableHead>Status</TableHead><TableHead>Fit</TableHead><TableHead>Owner</TableHead><TableHead>Compliance check</TableHead><TableHead>Attempts</TableHead><TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((l) => (
                  <TableRow key={l.id} className="cursor-pointer" onClick={() => setOpen(l.id)}>
                    {can('manager') && <TableCell onClick={(e) => e.stopPropagation()}><Checkbox checked={picked.has(l.id)} onCheckedChange={() => togglePick(l.id)} /></TableCell>}
                    <TableCell><div className="font-medium">{l.business_name || '—'}</div><div className="text-xs text-muted-foreground">{l.contact_name}</div></TableCell>
                    <TableCell className="font-mono text-xs">{l.phone}<div className="font-sans text-muted-foreground">{[l.region_code, l.line_type && titleCase(l.line_type)].filter(Boolean).join(' · ')}</div></TableCell>
                    <TableCell><LeadStatus status={l.status} /></TableCell>
                    <TableCell className="tabular-nums" title={l.fit?.reasons.join('\n')}>{l.fit_score ?? '—'}</TableCell>
                    <TableCell className="text-xs">{people.find((p) => p.id === l.owner_id)?.name ?? '—'}</TableCell>
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
      {dupes && <DuplicatesDialog onClose={() => setDupes(false)} onOpen={(id) => { setDupes(false); setOpen(id) }} />}
      <LeadSheet id={open} onClose={() => setOpen(null)} />
      {confirm.dialog}
    </>
  )
}

function BulkBar({ count, people, onClear, onAction, pending }: {
  count: number; people: Person[]; onClear: () => void; pending: boolean
  onAction: (action: 'assign' | 'stage' | 'tag' | 'queue', value: unknown) => void
}) {
  const campaigns = useCampaigns().data?.campaigns.filter((c) => c.status !== 'completed') ?? []
  const [tag, setTag] = useState('')
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary/5 p-2 text-sm">
      <span className="font-medium">{count} selected</span>
      <SimpleSelect size="sm" value="" onChange={(v) => onAction('assign', v === 'none' ? '' : v)}
        options={[{ value: '', label: 'Assign to…' }, { value: 'none', label: 'Nobody' }, ...people.map((p) => ({ value: p.id, label: p.name }))]} />
      <SimpleSelect size="sm" value="" onChange={(v) => v && onAction('stage', v)}
        options={[{ value: '', label: 'Set stage…' }, ...STAGE_MANUAL.map((st) => ({ value: st, label: STAGE_LABEL[st] }))]} />
      <SimpleSelect size="sm" value="" onChange={(v) => v && onAction('queue', v)}
        options={[{ value: '', label: 'Add to campaign…' }, ...campaigns.map((c) => ({ value: c.id, label: c.name }))]} />
      <Input className="h-7 w-32" placeholder="Tag" value={tag} onChange={(e) => setTag(e.target.value)} />
      <Button size="xs" variant="outline" disabled={!tag.trim() || pending} onClick={() => { onAction('tag', tag.trim()); setTag('') }}>Tag</Button>
      <div className="flex-1" />
      {pending && <Loader2 className="size-4 animate-spin" />}
      <Button size="xs" variant="ghost" onClick={onClear}>Clear</Button>
    </div>
  )
}

function DuplicatesDialog({ onClose, onOpen }: { onClose: () => void; onOpen: (id: string) => void }) {
  const q = useDuplicates(true)
  const m = useCrmMutations()
  const can = useCan()
  const confirm = useConfirm()
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Possible duplicates</DialogTitle>
          <DialogDescription>Leads with the same website, or the same name in the same city. Merging keeps the stricter compliance state.</DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto">
          {q.isLoading && <Loader2 className="animate-spin" />}
          {q.data?.groups.length === 0 && <p className="text-sm text-muted-foreground">No duplicates found.</p>}
          {q.data?.groups.map((g, i) => (
            <div key={i} className="space-y-1 rounded-md border p-2 text-sm">
              <div className="text-xs text-muted-foreground">{g.reason}</div>
              {g.leads.map((l, j) => (
                <div key={l.id} className="flex items-center gap-2">
                  <button type="button" className="flex-1 text-left hover:underline" onClick={() => onOpen(l.id)}>{l.business_name} <span className="text-xs text-muted-foreground">{l.phone} · {l.status}</span></button>
                  {can('manager') && j > 0 && (
                    <Button size="xs" variant="outline" disabled={m.merge.isPending}
                      onClick={() => confirm.ask({
                        title: `Merge ${l.business_name || l.phone} into ${g.leads[0].business_name || g.leads[0].phone}?`,
                        description: 'Their calls, notes, tasks and deals move to the first lead, which keeps the stricter compliance state. This can’t be undone.',
                        confirmLabel: 'Merge', destructive: true,
                        run: () => m.merge.mutate({ keep: g.leads[0].id, merge: l.id }, { onSuccess: () => toast.success('Merged'), onError: (e) => toast.error(e.message) }),
                      })}>
                      Merge into first
                    </Button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
        {confirm.dialog}
      </DialogContent>
    </Dialog>
  )
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const m = useSalesMutations()
  const campaigns = useCampaigns()
  const file = useRef<HTMLInputElement>(null)
  const [csv, setCsv] = useState('')
  const [source, setSource] = useState('')
  const [campaignId, setCampaignId] = useState('none')
  const [mode, setMode] = useState<CampaignMode>('human_assisted')
  const [country, setCountry] = useState('AU')
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const researchOn = !!useSalesStatus().data?.research.enabled
  const [doResearch, setDoResearch] = useState(true)
  const body = (dry_run: boolean, map?: Record<string, string>) => ({
    csv, source, mapping: map ?? (Object.keys(mapping).length ? mapping : undefined), dry_run, mode, default_country: country,
    campaign_id: campaignId === 'none' ? undefined : campaignId, research: researchOn && doResearch,
  })
  const runPreview = (map?: Record<string, string>) =>
    m.importLeads.mutate(body(true, map), { onSuccess: (r) => { setPreview(r); setMapping(r.mapping) } })
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
          <Field label="Where the list came from" hint="e.g. sydney-agencies-dec-2026"><Input value={source} onChange={(e) => setSource(e.target.value)} /></Field>
          <Field label="Numbers without a country code are in" hint="A number written as +61… or +44… is read as written.">
            <SimpleSelect className="w-full" value={country} onChange={(v) => { setCountry(v); setPreview(null) }}
              options={[{ value: 'AU', label: 'Australia' }, { value: 'GB', label: 'United Kingdom' }, { value: 'US', label: 'United States' }, { value: 'CA', label: 'Canada' }]} />
          </Field>
          <Field label="Add to campaign">
            <SimpleSelect className="w-full" value={campaignId} onChange={setCampaignId}
              options={[{ value: 'none', label: 'Not yet' }, ...(campaigns.data?.campaigns ?? []).filter((c) => c.status !== 'completed').map((c) => ({ value: c.id, label: c.name }))]} />
          </Field>
          {researchOn && (
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <Switch checked={doResearch} onCheckedChange={setDoResearch} />
              Read each lead's website before it's called, so the agent can open with something specific
            </label>
          )}
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
              {preview.fields.filter((f) => ['phone', 'business_name', 'contact_name', 'email', 'website', 'state', 'country', 'timezone', 'vertical', 'notes', 'agent_notes'].includes(f)).map((f) => (
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
              onClick={() => m.importLeads.mutate(body(false), { onSuccess: (r) => { toast.success(`${r.imported} leads imported`); onClose() } })}>
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
              onSuccess: (r) => { toast.success(`${r.queued} queued, ${r.blocked} cannot be called`); onClose() } })}>
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

function ResearchCard({ lead }: { lead: SalesLead }) {
  const m = useSalesMutations()
  const r = lead.research
  const d = r?.data
  return (
    <section className="space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">What their website says</h3>
        <Button size="xs" variant="outline" disabled={!lead.website || m.researchLead.isPending || r?.status === 'running'}
          title={lead.website ? 'Read their website now (10–40 seconds)' : 'Add their website first'}
          onClick={() => m.researchLead.mutate(lead.id, { onSuccess: (x) => toast[x.research.status === 'done' ? 'success' : 'message'](x.research.status === 'done' ? 'Research updated' : x.research.error || 'Nothing found') })}>
          {m.researchLead.isPending ? <Loader2 className="animate-spin" /> : <Search />} {d ? 'Refresh' : 'Research now'}
        </Button>
      </div>
      {!lead.website ? <p className="text-xs text-muted-foreground">No website on file.</p>
        : !r ? <p className="text-xs text-muted-foreground">Not researched yet.</p>
        : r.status === 'pending' || r.status === 'running' ? <p className="text-xs text-muted-foreground">Queued: it will be read before the call.</p>
        : r.status !== 'done' || !d ? <p className="text-xs text-muted-foreground">Couldn't read it{r.error ? `: ${r.error}` : ''}.</p>
        : (
          <div className="space-y-1 text-xs">
            <p className="text-sm">{d.summary}</p>
            {d.hook && <p><span className="font-medium">Opening hook:</span> {d.hook}</p>}
            {d.likely_pains.length > 0 && <p><span className="font-medium">Likely problems:</span> {d.likely_pains.join('; ')}</p>}
            {d.services.length > 0 && <p><span className="font-medium">They offer:</span> {d.services.join(', ')}</p>}
            {d.size_signals && <p><span className="font-medium">Size:</span> {d.size_signals}</p>}
            <p className="text-muted-foreground">
              Website {d.tech_signals.website_quality || 'not rated'}{d.tech_signals.online_booking != null ? ` · online booking: ${d.tech_signals.online_booking ? 'yes' : 'no'}` : ''}{d.tech_signals.cms_guess ? ` · ${d.tech_signals.cms_guess}` : ''} · confidence {Math.round(d.confidence * 100)}% · read {r.fetched_at ? timeAgo(r.fetched_at) : ''}
            </p>
          </div>
        )}
    </section>
  )
}

export function LeadSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useSalesLead(id)
  const m = useSalesMutations()
  const f = useFollowUpMutations()
  const [confirmDnc, setConfirmDnc] = useState(false)
  const [requeue, setRequeue] = useState(false)
  const can = useCan()
  const lead = q.data?.lead
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{lead?.business_name || lead?.phone || 'Lead'}</SheetTitle>
          <SheetDescription>{lead ? `${lead.phone} · ${lead.source}` : ''}</SheetDescription>
        </SheetHeader>
        {lead && (
          <div className="space-y-5 px-4 pb-6 text-sm">
            <div>
              <KeyValue label="Status"><LeadStatus status={lead.status} /></KeyValue>
              <KeyValue label="Stage">{STAGE_LABEL[lead.stage] ?? lead.stage}</KeyValue>
              <KeyValue label="Where">{[lead.region_code, lead.country, lead.timezone || lead.timezones.join(' / ')].filter(Boolean).join(' · ') || 'Unknown'}</KeyValue>
              <KeyValue label="Line">{lead.line_type ? titleCase(lead.line_type) : 'Not checked'}</KeyValue>
              <KeyValue label="Attempts">{lead.attempts}{lead.next_eligible_at ? ` · next ${dateTime(lead.next_eligible_at)}` : ''}</KeyValue>
              {lead.cooldown_until && new Date(lead.cooldown_until) > new Date() && (
                <KeyValue label="Resting until">{dateTime(lead.cooldown_until)} <span className="text-xs text-muted-foreground">(said no to an offer)</span></KeyValue>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              {can('manager') && (
                <Button size="sm" variant="outline" disabled={m.checkLead.isPending}
                  title="Runs every compliance check now. A US number may need a paid line-type lookup."
                  onClick={() => m.checkLead.mutate(lead.id, { onSuccess: (r) => toast[r.decision.allowed ? 'success' : 'message'](r.decision.allowed ? 'Passes every check right now' : failureText(r.decision.failures[0])) })}>
                  {m.checkLead.isPending ? <Loader2 className="animate-spin" /> : <ShieldCheck />} Check now
                </Button>
              )}
              {can('manager') && !['dnc', 'bad_number', 'queued', 'in_progress', 'callback'].includes(lead.status) && (
                <Button size="sm" variant="outline" onClick={() => setRequeue(true)}><RotateCcw /> Call again</Button>
              )}
              {can('caller') && lead.status !== 'dnc' && <Button size="sm" variant="outline" className="text-destructive" onClick={() => setConfirmDnc(true)}><Ban /> Do not call</Button>}
            </div>
            <LeadDetails key={lead.id + (lead.stage ?? '')} lead={lead} pending={f.patchLead.isPending}
              onSave={(patch) => f.patchLead.mutate({ id: lead.id, ...patch }, { onSuccess: () => toast.success('Saved') })} />
            {lead.qualification && (
              <section className="space-y-1 rounded-md border p-3 text-xs">
                <h3 className="text-sm font-medium">From the last call</h3>
                <p>Interest {lead.qualification.interest}/5{lead.qualification.role ? ` · ${lead.qualification.role}` : ''}{lead.qualification.decision_maker ? ' · decides' : lead.qualification.decision_maker === false ? ' · not the decision maker' : ''}{lead.qualification.budget_signal !== 'unknown' ? ` · budget ${lead.qualification.budget_signal}` : ''}</p>
                {lead.qualification.pains.length > 0 && <p><span className="font-medium">Pains:</span> {lead.qualification.pains.join('; ')}</p>}
                {lead.qualification.current_solution && <p><span className="font-medium">Today they use:</span> {lead.qualification.current_solution}</p>}
                {lead.qualification.objections.length > 0 && <p><span className="font-medium">Objections:</span> {lead.qualification.objections.join('; ')}</p>}
                {lead.qualification.next_step && <p><span className="font-medium">Next step:</span> {lead.qualification.next_step}</p>}
                {(lead.qualification.people?.length ?? 0) > 0 && <p><span className="font-medium">Spoke to:</span> {lead.qualification.people!.map((p) => [p.name, p.role].filter(Boolean).join(', ')).join('; ')}</p>}
                {lead.qualification.best_time_to_reach && <p><span className="font-medium">Best time to reach them:</span> {lead.qualification.best_time_to_reach}</p>}
              </section>
            )}
            <MemoryCard lead={lead} />
            <LeadCrm lead={lead} deals={q.data?.deals ?? []} scheduled={q.data?.scheduled_emails ?? []} />
            <ResearchCard lead={lead} />
            <LeadTasks leadId={lead.id} tasks={q.data?.tasks ?? []} />
            <Timeline leadId={lead.id} activity={q.data?.activity ?? []} />
            {((lead.offer_history?.length ?? 0) > 0 || (lead.history?.length ?? 0) > 0) && (
              <section>
                <h3 className="mb-2 font-medium">Earlier campaigns</h3>
                <div className="space-y-1 text-xs text-muted-foreground">
                  {lead.offer_history?.map((h, i) => <div key={`o${i}`}>Not interested in {h.offer_id}{h.reason ? ` (“${h.reason}”)` : ''}, {dateTime(h.at)}</div>)}
                  {lead.history?.map((h, i) => <div key={`h${i}`}>{h.offer_id} v{h.offer_version}: {titleCase(h.status)} after {h.attempts} attempt{h.attempts === 1 ? '' : 's'}, until {dateTime(h.until)}</div>)}
                </div>
              </section>
            )}
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
        {requeue && lead && <RequeueDialog leadId={lead.id} onClose={() => setRequeue(false)} />}
      </SheetContent>
    </Sheet>
  )
}

function LeadDetails({ lead, pending, onSave }: { lead: SalesLead & { stage: Stage }; pending: boolean; onSave: (patch: LeadPatch) => void }) {
  const initial = { business_name: lead.business_name ?? '', contact_name: lead.contact_name ?? '', email: lead.email ?? '',
    website: lead.website ?? '', agent_notes: lead.agent_notes ?? '', stage: STAGE_MANUAL.includes(lead.stage) ? lead.stage : '' }
  const [form, setForm] = useState(initial)
  const dirty = JSON.stringify(form) !== JSON.stringify(initial)
  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch })
  return (
    <section className="grid gap-2 rounded-md border p-3 sm:grid-cols-2">
      <Field label="Business"><Input value={form.business_name} onChange={(e) => set({ business_name: e.target.value })} /></Field>
      <Field label="Contact"><Input value={form.contact_name} onChange={(e) => set({ contact_name: e.target.value })} /></Field>
      <Field label="Email"><Input type="email" value={form.email} onChange={(e) => set({ email: e.target.value })} /></Field>
      <Field label="Website" hint="This business's own website. With research on, the agent reads it before calling.">
        <Input value={form.website} placeholder="harbourdental.com.au" onChange={(e) => set({ website: e.target.value })} />
      </Field>
      <Field label="Notes for the agent" className="sm:col-span-2"
        hint={`What the agent should know about this business before it calls, e.g. “3 clinics, new owner since March, said they miss calls at lunch”. Used on the call, never read out. ${600 - form.agent_notes.length} characters left.`}>
        <Textarea rows={2} maxLength={600} value={form.agent_notes} placeholder="Anything that makes the call more relevant to them"
          onChange={(e) => set({ agent_notes: e.target.value })} />
      </Field>
      <Field label="Stage" className="sm:col-span-2" hint="Automatic follows the calls. Proposal, won and lost stay until you change them.">
        <SimpleSelect className="w-full" value={form.stage || 'auto'} onChange={(v) => set({ stage: v === 'auto' ? '' : v })}
          options={[{ value: 'auto', label: `Automatic (${STAGE_LABEL[lead.stage] ?? lead.stage})` },
            ...STAGE_MANUAL.map((s) => ({ value: s, label: STAGE_LABEL[s] }))]} />
      </Field>
      {dirty && (
        <div className="flex justify-end gap-2 sm:col-span-2">
          <Button size="sm" variant="ghost" onClick={() => setForm(initial)}>Undo</Button>
          <Button size="sm" disabled={pending} onClick={() => onSave(form)}>{pending && <Loader2 className="animate-spin" />} Save</Button>
        </div>
      )}
    </section>
  )
}

const STAGE_MANUAL: Stage[] = ['interested', 'meeting_booked', 'proposal', 'won', 'lost']

/** What the agent will know about earlier calls the next time it rings them (SALES_LEARNING_PLAN.md Phase 1). */
function MemoryCard({ lead }: { lead: SalesLead }) {
  const f = useFollowUpMutations()
  const can = useCan()
  const [confirm, setConfirm] = useState(false)
  const memory = lead.memory
  if (!memory) return null
  return (
    <section className="space-y-2 rounded-md border p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium"><Brain className="size-4" /> What the agent remembers</h3>
        {can('caller') && memory.lines.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setConfirm(true)}>Forget</Button>
        )}
      </div>
      {!memory.enabled ? (
        <p className="text-muted-foreground">Memory of earlier calls is switched off (SALES_LEAD_MEMORY_ENABLED).</p>
      ) : memory.lines.length === 0 ? (
        <p className="text-muted-foreground">
          Nothing yet{memory.cleared_at ? ` (forgotten ${dateTime(memory.cleared_at)})` : ''}. After a conversation, the agent picks up where it left off on the next call.
        </p>
      ) : (
        <>
          <ul className="list-disc space-y-1 pl-4">{memory.lines.map((line, i) => <li key={i}>{line}</li>)}</ul>
          <p className="text-muted-foreground">
            Built from the last {memory.days} days of calls for this company{memory.cleared_at ? `, since ${dateTime(memory.cleared_at)}` : ''}. The agent is told it may be wrong and never reads it out.
          </p>
        </>
      )}
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Forget earlier calls?" confirmLabel="Forget"
        description="The agent won't mention earlier calls with this business from now on. The calls themselves stay on record."
        loading={f.forgetMemory.isPending}
        onConfirm={() => f.forgetMemory.mutate(lead.id, { onSuccess: () => { toast.success('The agent will start fresh'); setConfirm(false) } })} />
    </section>
  )
}

function LeadTasks({ leadId, tasks }: { leadId: string; tasks: SalesTask[] }) {
  const f = useFollowUpMutations()
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const open = tasks.filter((t) => !t.done_at && !t.cancelled)
  return (
    <section>
      <h3 className="mb-2 font-medium">Follow-ups</h3>
      <div className="space-y-1">
        {open.map((t) => (
          <div key={t.id} className="flex items-center gap-2 text-sm">
            <Checkbox checked={false} onCheckedChange={() => f.updateTask.mutate({ id: t.id, done: true })} aria-label="Done" />
            <span className="flex-1">{t.title}</span>
            {t.due_at && <span className="text-xs text-muted-foreground">{dateTime(t.due_at)}</span>}
            <Button size="icon-xs" variant="ghost" aria-label="Cancel" onClick={() => f.updateTask.mutate({ id: t.id, cancelled: true })}><X /></Button>
          </div>
        ))}
        {open.length === 0 && <p className="text-xs text-muted-foreground">Nothing to do.</p>}
      </div>
      <div className="mt-2 flex gap-2">
        <Input value={title} placeholder="Send the proposal" onChange={(e) => setTitle(e.target.value)} />
        <Input type="datetime-local" className="w-48" value={due} onChange={(e) => setDue(e.target.value)} />
        <Button size="sm" variant="outline" disabled={title.trim().length < 2 || f.createTask.isPending}
          onClick={() => f.createTask.mutate({ lead_id: leadId, title: title.trim(), due_at: due ? new Date(due).toISOString() : null },
            { onSuccess: () => { setTitle(''); setDue('') } })}><Plus /> Add</Button>
      </div>
    </section>
  )
}

const ACTIVITY_ICON: Record<Activity['type'], string> = { call: '📞', note: '📝', email: '✉️', stage: '➜', task: '☑', meeting: '📅' }

function Timeline({ leadId, activity }: { leadId: string; activity: Activity[] }) {
  const f = useFollowUpMutations()
  const [note, setNote] = useState('')
  return (
    <section>
      <h3 className="mb-2 font-medium">Timeline</h3>
      <div className="mb-2 flex gap-2">
        <Textarea rows={2} value={note} placeholder="Add a note" onChange={(e) => setNote(e.target.value)} />
        <Button size="sm" variant="outline" className="self-end" disabled={!note.trim() || f.addNote.isPending}
          onClick={() => f.addNote.mutate({ id: leadId, text: note.trim() }, { onSuccess: () => setNote('') })}>Add</Button>
      </div>
      <div className="space-y-1.5">
        {activity.map((a) => (
          <div key={a.id} className="flex gap-2 text-xs">
            <span aria-hidden className="w-4 shrink-0 text-center">{ACTIVITY_ICON[a.type]}</span>
            <span className="flex-1 whitespace-pre-wrap">{a.text}</span>
            <span className="shrink-0 text-muted-foreground">{timeAgo(a.at)}</span>
          </div>
        ))}
        {activity.length === 0 && <p className="text-xs text-muted-foreground">Nothing yet.</p>}
      </div>
    </section>
  )
}

function RequeueDialog({ leadId, onClose }: { leadId: string; onClose: () => void }) {
  const m = useSalesMutations()
  const campaigns = (useCampaigns().data?.campaigns ?? []).filter((c) => c.status !== 'completed')
  const [campaignId, setCampaignId] = useState('')
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Call again in another campaign</DialogTitle>
          <DialogDescription>Their attempts start again and this campaign's results are kept in their history. A “not interested” offer is never pitched to them again, and they rest after any “no” before another offer calls.</DialogDescription>
        </DialogHeader>
        <Field label="Campaign"><SimpleSelect className="w-full" placeholder="Choose…" value={campaignId} onChange={setCampaignId} options={campaigns.map((c) => ({ value: c.id, label: c.name }))} /></Field>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!campaignId || m.queueLeads.isPending}
            onClick={() => m.queueLeads.mutate({ campaign_id: campaignId, lead_ids: [leadId] }, {
              onSuccess: (r) => {
                if (r.queued) toast.success('Queued')
                else toast.message(r.resting ? 'They said no recently, so they are resting.' : 'The compliance checks won’t allow this campaign to call them.')
                onClose()
              } })}>
            Queue
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
