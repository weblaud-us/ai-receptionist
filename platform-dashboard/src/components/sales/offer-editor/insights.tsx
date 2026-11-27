import { useState } from 'react'
import { Check, Lightbulb, Loader2, Plus } from 'lucide-react'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { cn } from '@receptionist/shared/lib/utils'
import { offerInsights, pct, type Insights, type OfferSettings } from '@/lib/sales'

/**
 * Learn from real calls (SALES_SUITE_PLAN.md Phase 14): what prospects objected to, whether the offer
 * answers it, which script is winning, and suggestions to add to the draft.
 */
export function InsightsTab({ id, form, canEdit, onAdd }: {
  id: string | null
  form: OfferSettings
  canEdit: boolean
  onAdd: (patch: Partial<OfferSettings>) => void
}) {
  const [days, setDays] = useState<7 | 30 | 90>(30)
  const [data, setData] = useState<Insights | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [added, setAdded] = useState<Set<string>>(new Set())
  if (!id) return <p className="text-sm text-muted-foreground">Publish the offer and run a campaign; what prospects say shows up here.</p>
  const run = () => {
    setLoading(true)
    setError('')
    offerInsights(id, days).then(setData).catch((e: Error) => setError(e.message)).finally(() => setLoading(false))
  }
  const add = (key: string, patch: Partial<OfferSettings>) => {
    onAdd(patch)
    setAdded((s) => new Set(s).add(key))
  }
  const s = data?.suggestions
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground">Calls in the last</span>
        <SimpleSelect size="sm" value={String(days)} onChange={(v) => setDays(Number(v) as 7 | 30 | 90)}
          options={[{ value: '7', label: '7 days' }, { value: '30', label: '30 days' }, { value: '90', label: '90 days' }]} />
        <Button size="sm" disabled={loading} onClick={run}>{loading ? <Loader2 className="animate-spin" /> : <Lightbulb />} Read the calls</Button>
        <span className="text-xs text-muted-foreground">This summary reads only the notes taken after each call. (The AI coach reviews each call's transcript once, with names masked.)</span>
      </div>
      {error && <p className="text-destructive">{error}</p>}
      {data && (
        <>
          <p className="text-muted-foreground">{data.calls} calls, {data.answered} answered by a person.{data.note ? ` ${data.note}` : ''}</p>
          <section className="space-y-2">
            <h3 className="font-medium">Scripts</h3>
            <p className="text-muted-foreground">{data.scripts.note}</p>
            {data.scripts.variants.length > 0 && (
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground"><tr><th>Script</th><th>Dialled</th><th>Reached</th><th>Meetings</th><th>Rate</th></tr></thead>
                <tbody>
                  {data.scripts.variants.map((v) => (
                    <tr key={`${v.offer_version}-${v.variant_id}`} className={cn(data.scripts.leader === v.variant_id && 'font-medium text-emerald-700 dark:text-emerald-400')}>
                      <td>{v.variant_id} <span className="text-xs text-muted-foreground">v{v.offer_version}</span></td>
                      <td className="tabular-nums">{v.dialled}</td><td className="tabular-nums">{v.human}</td>
                      <td className="tabular-nums">{v.demo_booked}</td><td className="tabular-nums">{pct(v.demo_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          {data.objections.length > 0 && (
            <section className="space-y-1">
              <h3 className="font-medium">What they said</h3>
              {data.objections.map((o) => (
                <div key={o.text} className="flex items-center gap-2">
                  <span className="flex-1">{o.text}</span>
                  <span className="text-xs tabular-nums text-muted-foreground">{o.count}×</span>
                  <Badge variant="secondary" className={cn('border-0', o.covered ? 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300' : 'bg-amber-500/15 text-amber-800 dark:text-amber-300')}>
                    {o.covered ? 'answered' : 'not answered'}
                  </Badge>
                </div>
              ))}
            </section>
          )}
          {s && (
            <section className="space-y-2">
              <h3 className="font-medium">Suggestions for the next version</h3>
              {s.objections.map((o) => {
                const key = `o:${o.objection}`
                return (
                  <Suggestion key={key} text={`“${o.objection}” → ${o.response}`} done={added.has(key)} disabled={!canEdit || form.objections.length >= 12}
                    onAdd={() => add(key, { objections: [...form.objections, o] })} />
                )
              })}
              {s.discovery_questions.map((q) => (
                <Suggestion key={`d:${q}`} text={`Discovery question: ${q}`} done={added.has(`d:${q}`)} disabled={!canEdit || form.discovery_questions.length >= 6}
                  onAdd={() => add(`d:${q}`, { discovery_questions: [...form.discovery_questions, q] })} />
              ))}
              {s.pain_hypotheses.map((p) => (
                <Suggestion key={`p:${p}`} text={`Likely problem: ${p}`} done={added.has(`p:${p}`)} disabled={!canEdit || form.pain_hypotheses.length >= 6}
                  onAdd={() => add(`p:${p}`, { pain_hypotheses: [...form.pain_hypotheses, p] })} />
              ))}
              <p className="text-xs text-muted-foreground">Added items go into the draft. Save and publish a new version, run the test calls, then move campaigns to it.</p>
            </section>
          )}
        </>
      )}
    </div>
  )
}

function Suggestion({ text, done, disabled, onAdd }: { text: string; done: boolean; disabled: boolean; onAdd: () => void }) {
  return (
    <div className="flex items-start gap-2 rounded-md border p-2">
      <span className="flex-1">{text}</span>
      {done ? <span className="inline-flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400"><Check className="size-3" /> Added</span>
        : <Button size="xs" variant="outline" disabled={disabled} onClick={onAdd}><Plus /> Add to draft</Button>}
    </div>
  )
}
