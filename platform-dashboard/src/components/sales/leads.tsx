import { useState } from 'react'
import { toast } from 'sonner'
import { Bookmark, CheckCircle2, Copy, Upload, XCircle } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, Pagination, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { titleCase } from '@receptionist/shared/lib/format'
import { failureText, useCrmMutations, usePeople, useSalesLeads, useViews, type GateDecision } from '@/lib/sales'
import { useCan } from '@/lib/roles'
import { useConfirm } from '@/lib/use-confirm'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { cn } from '@receptionist/shared/lib/utils'
import { LeadStatus } from '@/components/sales/lead-status'
import { statusLabel } from '@/components/sales/lead-logic'
import { LeadSheet } from '@/components/sales/lead-sheet'
import { BulkBar, DuplicatesDialog, ImportDialog, QueueDialog } from '@/components/sales/lead-dialogs'

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
  const [naming, setNaming] = useState<string | null>(null)       // the "Save view" dialog's name, while open
  const saveView = (name: string) => {
    if (name.trim().length < 2) return
    crm.saveView.mutate({ name: name.trim(), kind: 'leads', shared: false,
      filters: Object.fromEntries(Object.entries({ status: status === 'all' ? '' : status, owner_id: owner, min_fit: minFit, q: search }).filter(([, v]) => v !== '')) },
      { onSuccess: () => { toast.success('View saved'); setNaming(null) } })
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
        <Button size="sm" variant="ghost" onClick={() => setNaming('')}><Bookmark /> Save view</Button>
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
                  {can('manager') && <TableHead className="w-8"><Checkbox aria-label="Select every lead on this page" checked={rows.every((r) => picked.has(r.id))}
                    onCheckedChange={(v) => setPicked(v ? new Set(rows.map((r) => r.id)) : new Set())} /></TableHead>}
                  <TableHead>Business</TableHead><TableHead>Number</TableHead><TableHead>Status</TableHead><TableHead>Fit</TableHead><TableHead>Owner</TableHead><TableHead>Compliance check</TableHead><TableHead>Attempts</TableHead><TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((l) => (
                  <TableRow key={l.id} className="cursor-pointer" tabIndex={0} aria-label={`Open ${l.business_name || l.phone}`}
                    onClick={() => setOpen(l.id)} onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setOpen(l.id) } }}>
                    {can('manager') && <TableCell onClick={(e) => e.stopPropagation()}><Checkbox aria-label={`Select ${l.business_name || l.phone}`} checked={picked.has(l.id)} onCheckedChange={() => togglePick(l.id)} /></TableCell>}
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
      <Dialog open={naming !== null} onOpenChange={(o) => !o && setNaming(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Save this view</DialogTitle>
            <DialogDescription>Keeps the current filters under a name, for you.</DialogDescription>
          </DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); saveView(naming ?? '') }}>
            <Field label="Name"><Input autoFocus value={naming ?? ''} maxLength={60} onChange={(e) => setNaming(e.target.value)} /></Field>
            <DialogFooter className="pt-3">
              <Button type="button" variant="outline" onClick={() => setNaming(null)}>Cancel</Button>
              <Button type="submit" disabled={(naming ?? '').trim().length < 2 || crm.saveView.isPending}>Save view</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}

// Kept here so existing imports (pipeline, search, crm board) don't change.
export { LeadSheet } from '@/components/sales/lead-sheet'
export { LeadStatus } from '@/components/sales/lead-status'
