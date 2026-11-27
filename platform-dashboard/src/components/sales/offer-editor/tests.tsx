import { useState } from 'react'
import { toast } from 'sonner'
import { CheckCircle2, ChevronRight, FlaskConical, Loader2, XCircle } from 'lucide-react'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { dateTime } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useSafetyMutations, useSimulation, useSimulations, type OfferSettings, type Simulation } from '@/lib/sales'

/**
 * Automated test calls (SALES_SUITE_PLAN.md Phase 8): the real agent talks to a simulated prospect
 * through a dozen scenarios, in a dry run. A campaign can't start until its version passed.
 */
export function TestCallsTab({ id, form, versions, canRun, problems }: {
  id: string | null
  form: OfferSettings
  versions: { version: number }[]
  canRun: boolean
  problems: number
}) {
  const sims = useSimulations(id)
  const m = useSafetyMutations()
  const [target, setTarget] = useState<string>('draft')
  const [open, setOpen] = useState<string | null>(null)
  const busy = sims.data?.simulations.some((s) => s.status === 'queued' || s.status === 'running')

  if (!id) return <p className="text-sm text-muted-foreground">Save the draft first, then run the test calls.</p>
  const run = () =>
    m.simulate.mutate(
      target === 'draft' ? { offerId: id, offer: form } : { offerId: id, version: Number(target) },
      { onSuccess: (r) => toast.success(`Running ${r.scenarios.length} scenarios for every script. This takes a few minutes.`) },
    )

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        The real agent talks to a simulated prospect in text: a gatekeeper, “not interested”, “stop calling”, the price question, a callback, an off-topic request, hidden instructions, your two main objections, and a check that it claims nothing beyond your proof points. Nothing is dialled, booked or emailed. A campaign can only start once its offer version passed for every chosen script.
      </p>
      {canRun && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          Test
          <SimpleSelect size="sm" value={target} onChange={setTarget}
            options={[{ value: 'draft', label: 'the current draft' }, ...versions.map((v) => ({ value: String(v.version), label: `published v${v.version}` }))]} />
          <Button size="sm" disabled={busy || m.simulate.isPending || (target === 'draft' && problems > 0)} onClick={run}
            title={target === 'draft' && problems ? 'Fix the problems first' : undefined}>
            {m.simulate.isPending || busy ? <Loader2 className="animate-spin" /> : <FlaskConical />} Run test calls
          </Button>
          {busy && <span className="text-muted-foreground">Running… this page updates by itself.</span>}
        </div>
      )}
      <ul className="space-y-2">
        {(sims.data?.simulations ?? []).map((s) => (
          <li key={s.id} className="rounded-lg border">
            <button type="button" className="flex w-full items-center gap-3 p-3 text-left text-sm" onClick={() => setOpen(open === s.id ? null : s.id)}>
              <StatusIcon s={s} />
              <span className="flex-1">
                <span className="font-medium">{s.version ? `v${s.version}` : 'Draft'}</span>
                <span className="text-muted-foreground"> · {s.variant_ids.length} script{s.variant_ids.length === 1 ? '' : 's'} · {dateTime(s.created_at)}</span>
                {s.error && <span className="block text-xs text-destructive">{s.error}</span>}
              </span>
              {s.status === 'done' && <Badge variant="secondary" className={cn('border-0', s.passed === s.total ? 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300' : 'bg-destructive/10 text-destructive')}>{s.passed}/{s.total} passed</Badge>}
              {(s.status === 'queued' || s.status === 'running') && <Badge variant="secondary">{s.status}</Badge>}
              <ChevronRight className={cn('size-4 text-muted-foreground transition-transform', open === s.id && 'rotate-90')} />
            </button>
            {open === s.id && <SimulationDetail id={s.id} />}
          </li>
        ))}
        {sims.data && sims.data.simulations.length === 0 && <li className="text-sm text-muted-foreground">No test calls yet.</li>}
      </ul>
    </div>
  )
}

function StatusIcon({ s }: { s: Simulation }) {
  if (s.status === 'queued' || s.status === 'running') return <Loader2 className="size-4 animate-spin text-muted-foreground" />
  if (s.status === 'failed' || s.passed < s.total) return <XCircle className="size-4 text-destructive" />
  return <CheckCircle2 className="size-4 text-emerald-600" />
}

function SimulationDetail({ id }: { id: string }) {
  const q = useSimulation(id)
  const [shown, setShown] = useState<number | null>(null)
  if (!q.data) return <div className="border-t p-3"><Loader2 className="size-4 animate-spin" /></div>
  const results = q.data.simulation.results
  return (
    <ul className="divide-y border-t text-sm">
      {results.map((r, i) => (
        <li key={i} className="p-3">
          <button type="button" className="flex w-full items-start gap-2 text-left" onClick={() => setShown(shown === i ? null : i)}>
            {r.passed ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />}
            <span className="flex-1">
              <span className="font-medium">{r.label}</span> <span className="text-xs text-muted-foreground">· {r.variant_id}</span>
              {!r.passed && <span className="block text-xs text-destructive">{r.reason}</span>}
            </span>
          </button>
          {shown === i && (
            <div className="mt-2 space-y-1 rounded-md bg-muted/50 p-2 text-xs">
              {(r.transcript ?? []).map((t, j) => (
                <p key={j}><span className="font-medium">{t.role === 'user' ? 'Prospect' : 'Agent'}:</span> {t.text}</p>
              ))}
              {r.tools.length > 0 && <p className="text-muted-foreground">Tools: {r.tools.join(', ')}</p>}
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
