/** The Leads tab's bulk bar and dialogs: import, add to a campaign, duplicates. */
import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Upload } from 'lucide-react'
import { ErrorBanner, Field, KeyValue } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { titleCase } from '@receptionist/shared/lib/format'
import { MODE_LABEL, STAGE_LABEL, useCampaigns, useCrmMutations, useDuplicates, useSalesMutations, useSalesStatus, type Person, type CampaignMode, type ImportResult } from '@/lib/sales'
import { useCan } from '@/lib/roles'
import { useConfirm } from '@/lib/use-confirm'
import { STAGE_MANUAL } from '@/components/sales/lead-logic'

export function BulkBar({ count, people, onClear, onAction, pending }: {
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

export function DuplicatesDialog({ onClose, onOpen }: { onClose: () => void; onOpen: (id: string) => void }) {
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

export function ImportDialog({ onClose }: { onClose: () => void }) {
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

export function QueueDialog({ sources, onClose }: { sources: string[]; onClose: () => void }) {
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
