import { useState } from 'react'
import { toast } from 'sonner'
import { Archive, ArchiveRestore, Copy, Loader2, Megaphone, Pencil, Plus } from 'lucide-react'
import { EmptyState, ErrorBanner, TableSkeleton } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Sheet, SheetContent } from '@receptionist/shared/components/ui/sheet'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateTime, titleCase } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useCan } from '@/lib/roles'
import { BRAND_LABEL, useOffer, useOfferMutations, useOffers, useSalesStatus, type Offer } from '@/lib/sales'
import { useMarket } from './offer-editor/market'
import { AiDraftDialog, NewOfferChooser } from './offer-editor/ai-draft'
import { startFromAi, type EditorStart } from './offer-editor/draft'
import { OfferForm } from './offer-editor/form'

const STATUS_STYLE: Record<Offer['status'], string> = {
  draft: 'bg-slate-200 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300',
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  archived: 'bg-muted text-muted-foreground',
}

type Open = { id: string | null; start?: EditorStart; key: number }
let nextKey = 0

export function Offers({ onCreateCampaign }: { onCreateCampaign?: (offerId: string) => void }) {
  const [archived, setArchived] = useState(false)
  const q = useOffers(archived)
  const m = useOfferMutations()
  const can = useCan()
  const ai = useSalesStatus().data?.ai_assist
  const [market, setMarket] = useMarket()
  const [open, setOpen] = useState<Open | null>(null)
  const [choosing, setChoosing] = useState(false)
  const [drafting, setDrafting] = useState(false)
  const openId = (id: string | null, start?: EditorStart) => setOpen({ id, start, key: ++nextKey })
  const copy = (id: string) => m.duplicate.mutate(id, { onSuccess: (r) => { toast.success('Copied as a draft'); setChoosing(false); openId(r.offer.id) } })

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">
          An offer is what a campaign sells: who you call for, what you sell, the questions to ask, answers to objections, the price policy and the scripts. Publish a version, then pick it in a campaign.
        </p>
        <div className="flex-1" />
        <label className="flex items-center gap-2 text-sm text-muted-foreground"><Switch checked={archived} onCheckedChange={setArchived} /> Show archived</label>
        {can('manager') && <Button onClick={() => setChoosing(true)}><Plus /> New offer</Button>}
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={3} cols={5} /> : (q.data?.offers.length ?? 0) === 0 ? (
          <EmptyState title="No offers yet" description="Draft one with AI from your website. The built-in LaudLine and WebLaud offers appear here after the next deploy." />
        ) : (
          <Table>
            <TableHeader><TableRow><TableHead>Offer</TableHead><TableHead>Status</TableHead><TableHead>Published</TableHead><TableHead>Campaigns</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {q.data?.offers.map((o) => (
                <TableRow key={o.id}>
                  <TableCell>
                    <button type="button" className="text-left font-medium hover:underline" onClick={() => openId(o.id)}>{o.name}</button>
                    <div className="text-xs text-muted-foreground">{BRAND_LABEL[o.brand]} · calls as {o.company_name}{o.published ? ` · ${o.published.variants.length} script${o.published.variants.length === 1 ? '' : 's'}` : ''}</div>
                    {o.learning && (o.learning.live_n || o.learning.testing) && (
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        {o.learning.live_n && <Badge variant="outline" className="text-xs">Playbook v{o.learning.live_n}</Badge>}
                        {o.learning.testing && <Badge variant="outline" className="border-sky-300 text-xs text-sky-800 dark:border-sky-500/40 dark:text-sky-300">Testing v{o.learning.testing.n}{o.learning.testing.next ? ` · ${o.learning.testing.done}/${o.learning.testing.next}` : ''}</Badge>}
                      </div>
                    )}
                  </TableCell>
                  <TableCell><Badge variant="secondary" className={cn('border-0', STATUS_STYLE[o.status])}>{titleCase(o.status)}</Badge></TableCell>
                  <TableCell className="text-sm">{o.published ? <>v{o.published.version} <span className="text-muted-foreground">· {dateTime(o.published.published_at)}</span></> : <span className="text-muted-foreground">Not yet</span>}</TableCell>
                  <TableCell className="tabular-nums">{o.running_campaigns} running <span className="text-muted-foreground">of {o.campaigns}</span></TableCell>
                  <TableCell className="space-x-1 text-right whitespace-nowrap">
                    {can('manager') && o.published && o.status !== 'archived' && onCreateCampaign && (
                      <Button size="sm" variant="ghost" onClick={() => onCreateCampaign(o.id)}><Megaphone /> Campaign</Button>
                    )}
                    <Button size="sm" variant="ghost" onClick={() => openId(o.id)}><Pencil /> {can('manager') ? 'Edit' : 'Open'}</Button>
                    {can('manager') && (
                      <>
                        <Button size="sm" variant="ghost" disabled={m.duplicate.isPending} onClick={() => copy(o.id)}><Copy /> Copy</Button>
                        {o.status === 'archived' ? (
                          <Button size="sm" variant="ghost" onClick={() => m.unarchive.mutate(o.id, { onSuccess: () => toast.success('Unarchived') })}><ArchiveRestore /> Unarchive</Button>
                        ) : (
                          <Button size="sm" variant="ghost" disabled={o.running_campaigns > 0} title={o.running_campaigns ? 'Pause its campaigns first' : undefined}
                            onClick={() => m.archive.mutate(o.id, { onSuccess: () => toast.success('Archived. Campaigns already using it keep working.') })}><Archive /> Archive</Button>
                        )}
                      </>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      <NewOfferChooser open={choosing} onClose={() => setChoosing(false)} offers={q.data?.offers ?? []} ai={ai}
        onBlank={() => { setChoosing(false); openId(null) }} onCopy={copy}
        onAi={() => { setChoosing(false); setDrafting(true) }} />
      <AiDraftDialog open={drafting} onClose={() => setDrafting(false)} market={market} onMarket={setMarket}
        onDrafted={(r) => { setDrafting(false); openId(null, startFromAi(r)); toast.success('Drafted. Review each tab, confirm the facts, then save.') }} />
      {open && <OfferEditor key={open.key} id={open.id} start={open.start} onClose={() => setOpen(null)}
        onCreated={(id) => openId(id)} onReload={() => open.id && openId(open.id)}
        onCreateCampaign={onCreateCampaign ? (id) => { setOpen(null); onCreateCampaign(id) } : undefined} />}
    </>
  )
}

function OfferEditor({ id, start, onClose, onCreated, onReload, onCreateCampaign }: {
  id: string | null; start?: EditorStart; onClose: () => void; onCreated: (id: string) => void; onReload: () => void
  onCreateCampaign?: (offerId: string) => void
}) {
  const q = useOffer(id)
  if (id && !q.data) {
    return (
      <Sheet open onOpenChange={(o) => !o && onClose()}>
        <SheetContent className="w-full sm:max-w-6xl">
          <div className="p-6">{q.error ? <ErrorBanner error={q.error} /> : <Loader2 className="animate-spin" />}</div>
        </SheetContent>
      </Sheet>
    )
  }
  // Keyed on the offer so the form starts from what was loaded (or the AI draft / blank template).
  return <OfferForm key={id ?? 'new'} id={id} data={q.data} start={start} onClose={onClose} onCreated={onCreated} onReload={onReload} onCreateCampaign={onCreateCampaign} />
}
