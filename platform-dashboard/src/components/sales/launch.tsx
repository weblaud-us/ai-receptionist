import { useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, Rocket, Sparkles, XCircle } from 'lucide-react'
import { Field } from '@receptionist/shared/components/blocks'
import { Button } from '@receptionist/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { cn } from '@receptionist/shared/lib/utils'
import { MODE_LABEL, suggestCampaigns, usePreflight, type Campaign, type CampaignDraft, type PreflightCheck } from '@/lib/sales'

const ICON: Record<PreflightCheck['status'], typeof CheckCircle2> = { pass: CheckCircle2, warn: AlertTriangle, fail: XCircle }
const TONE: Record<PreflightCheck['status'], string> = {
  pass: 'text-emerald-600', warn: 'text-amber-600', fail: 'text-destructive',
}
const FIX_LABEL: Record<string, string> = {
  offers: 'Offers tab', leads: 'Leads tab', compliance: 'Compliance tab', campaigns: 'Edit the campaign', status: 'Dialer settings',
}

/**
 * The launch checklist (SALES_SUITE_PLAN.md Phase 10): what must be true before a campaign starts.
 * A failed item blocks starting; a warning doesn't, because the compliance gate still checks every
 * lead before every call.
 */
export function LaunchDialog({ campaign, onClose, onContinue, starting }: {
  campaign: Campaign | null
  onClose: () => void
  onContinue: (c: Campaign) => void
  starting: boolean
}) {
  const q = usePreflight(campaign?.id ?? null)
  const checks = q.data?.checks ?? []
  return (
    <Dialog open={!!campaign} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Launch checklist: {campaign?.name}</DialogTitle>
          <DialogDescription>{campaign && MODE_LABEL[campaign.mode]}. Red items must be fixed first; amber ones are worth a look.</DialogDescription>
        </DialogHeader>
        {q.isLoading ? <Loader2 className="animate-spin" /> : (
          <ul className="max-h-[55vh] space-y-2 overflow-y-auto">
            {checks.map((c) => {
              const Icon = ICON[c.status]
              return (
                <li key={c.id} className="flex gap-2 text-sm">
                  <Icon className={cn('mt-0.5 size-4 shrink-0', TONE[c.status])} />
                  <span>
                    <span className="font-medium">{c.label}</span>
                    {c.detail && <span className="block text-muted-foreground">{c.detail}</span>}
                    {c.fix && c.status !== 'pass' && <span className="block text-xs text-muted-foreground">Fix it on: {FIX_LABEL[c.fix] ?? c.fix}</span>}
                  </span>
                </li>
              )
            })}
          </ul>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!q.data?.can_start || starting} onClick={() => campaign && onContinue(campaign)}>
            {starting ? <Loader2 className="animate-spin" /> : <Rocket />} Continue
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * "Create a campaign from this offer" (SALES_SUITE_PLAN.md Phase 9): settings from the rules for the
 * leads you pick, one draft per call type. Nothing is created until you save the campaign.
 */
export function QuickSetupDialog({ offerId, offerName, onClose, onPick }: {
  offerId: string | null
  offerName: string
  onClose: () => void
  onPick: (draft: CampaignDraft, leads: { source?: string; tag?: string }) => void
}) {
  const [source, setSource] = useState('')
  const [tag, setTag] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ drafts: CampaignDraft[]; warnings: string[]; leads_considered: number } | null>(null)
  const run = () => {
    if (!offerId) return
    setLoading(true)
    setError('')
    suggestCampaigns({ offer_id: offerId, source: source || undefined, tag: tag || undefined })
      .then(setResult)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }
  return (
    <Dialog open={!!offerId} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Sparkles className="size-4 text-primary" /> New campaign for {offerName}</DialogTitle>
          <DialogDescription>Pick the leads; the call type, hours, caller IDs and limits are set from the calling rules for their countries. You review everything before saving.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <Field label="Import (source)" hint="Empty = every lead you can call"><Input value={source} placeholder="csv:sydney-expo" onChange={(e) => setSource(e.target.value)} /></Field>
          <Field label="Tag"><Input value={tag} placeholder="dental" onChange={(e) => setTag(e.target.value)} /></Field>
          <Button disabled={loading} onClick={run}>{loading && <Loader2 className="animate-spin" />} Suggest</Button>
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {result && (
          <div className="max-h-[50vh] space-y-3 overflow-y-auto">
            {result.drafts.length === 0 && <p className="text-sm text-muted-foreground">No leads found that could be called for this offer ({result.leads_considered} looked at).</p>}
            {result.warnings.map((w) => <p key={w} className="text-sm text-amber-700 dark:text-amber-400">{w}</p>)}
            {result.drafts.map((d) => (
              <div key={d.mode} className="space-y-2 rounded-lg border p-3 text-sm">
                <div className="flex items-start gap-2">
                  <div className="flex-1">
                    <div className="font-medium">{d.name}</div>
                    <div className="text-muted-foreground">{MODE_LABEL[d.mode]} · {d.lead_count} lead{d.lead_count === 1 ? '' : 's'}</div>
                  </div>
                  <Button size="sm" onClick={() => onPick(d, { source: source || undefined, tag: tag || undefined })}>Use this</Button>
                </div>
                <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">{d.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                {d.warnings.map((w) => <p key={w} className="flex gap-1.5 text-amber-700 dark:text-amber-400"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{w}</p>)}
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
