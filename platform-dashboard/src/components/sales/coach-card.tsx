import { toast } from 'sonner'
import { AlertTriangle, GraduationCap, Loader2, RotateCcw } from 'lucide-react'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { titleCase } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { COACH_STAGES, useSalesMutations, type Attempt } from '@/lib/sales'
import { callFlags, FLAG_LABEL, SKIP_TEXT, STAGE_LABEL, scoreTone } from '@/lib/learning'
import { useCan } from '@/lib/roles'

/**
 * The AI coach's review of one call (SALES_LEARNING_PLAN.md Phase 2), shown in the call's sheet.
 * Scores are for people to read: the agent's playbook is kept or rolled back on outcomes, not on these.
 */
export function CoachCard({ attempt, onTurn }: { attempt: Attempt; onTurn?: (turn: number) => void }) {
  const m = useSalesMutations()
  const can = useCan()
  const r = attempt.review
  const flags = callFlags(attempt)
  const again = () => m.reviewAttempt.mutate(attempt.id, { onSuccess: () => toast.success('Reviewed') })
  const rerun = can('manager') && (!r || r.status === 'done' || r.status === 'failed' || r.status === 'skipped')
  return (
    <section className="space-y-3 rounded-md border p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium"><GraduationCap className="size-4" /> AI coach</h3>
        <div className="flex items-center gap-2">
          {r?.status === 'done' && <span className={cn('rounded-full px-2 py-0.5 text-sm font-semibold tabular-nums', scoreTone(r.overall))}>{r.overall ?? '—'}<span className="text-xs font-normal">/100</span></span>}
          {rerun && (
            <Button size="xs" variant="ghost" disabled={m.reviewAttempt.isPending} onClick={again} title="Ask the coach to review this call again">
              {m.reviewAttempt.isPending ? <Loader2 className="animate-spin" /> : <RotateCcw />} {r?.status === 'done' ? 'Again' : 'Review'}
            </Button>
          )}
        </div>
      </div>

      {flags.length > 0 && (
        <ul className="space-y-1 rounded-md border border-rose-200 bg-rose-50 p-2 dark:border-rose-500/30 dark:bg-rose-500/10">
          {flags.map((f, i) => (
            <li key={i} className="flex gap-1.5 text-rose-800 dark:text-rose-300">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <button type="button" className="text-left hover:underline" onClick={() => f.turn != null && onTurn?.(f.turn)}>
                <span className="font-medium">{FLAG_LABEL[f.kind] ?? titleCase(f.kind)}</span>{f.detail ? `: ${f.detail}` : ''}
              </button>
            </li>
          ))}
        </ul>
      )}

      {!r && <p className="text-muted-foreground">Not reviewed yet: the coach reviews AI calls about a minute after they end.</p>}
      {(r?.status === 'pending' || r?.status === 'running') && <p className="text-muted-foreground">Reviewing soon…</p>}
      {r?.status === 'skipped' && <p className="text-muted-foreground">{SKIP_TEXT[r.reason ?? ''] ?? 'Not reviewed.'}</p>}
      {r?.status === 'failed' && <p className="text-destructive">The review failed{r.error ? `: ${r.error}` : ''}.</p>}

      {r?.status === 'done' && (
        <>
          <div className="space-y-1">
            {COACH_STAGES.map((s) => {
              const v = r.scores?.[s]
              return (
                <div key={s} className="grid grid-cols-[9.5rem_1fr_1.5rem] items-center gap-2">
                  <span className="text-muted-foreground">{STAGE_LABEL[s]}</span>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    {v != null && <div className={cn('h-full rounded-full', v >= 4 ? 'bg-emerald-500' : v >= 3 ? 'bg-amber-500' : 'bg-rose-500')} style={{ width: `${(v / 5) * 100}%` }} />}
                  </div>
                  <span className="text-right tabular-nums">{v ?? '—'}</span>
                </div>
              )
            })}
          </div>
          {r.ended_because && <p><span className="font-medium">Why it ended this way:</span> {r.ended_because}</p>}
          {(r.moments?.length ?? 0) > 0 && (
            <div className="space-y-2">
              <div className="font-medium">Moments that decided it</div>
              {r.moments!.map((mo, i) => (
                <button key={i} type="button" className="block w-full rounded-md bg-muted/60 p-2 text-left hover:bg-muted"
                  onClick={() => mo.turn != null && onTurn?.(mo.turn)}>
                  <Badge variant="outline" className="mb-1">{titleCase(mo.kind)}</Badge>
                  {mo.prospect_said && <p><span className="font-medium">Them:</span> {mo.prospect_said}</p>}
                  {mo.agent_did && <p><span className="font-medium">Us:</span> {mo.agent_did}</p>}
                  {mo.better && <p className="text-emerald-700 dark:text-emerald-400"><span className="font-medium">Better:</span> “{mo.better}”</p>}
                </button>
              ))}
            </div>
          )}
          {(r.lessons?.length ?? 0) > 0 && (
            <div className="space-y-1">
              <div className="font-medium">Lessons for next time</div>
              <ul className="list-disc space-y-1 pl-4">
                {r.lessons!.map((l, i) => <li key={i}>{l.situation ? <span className="text-muted-foreground">{l.situation}: </span> : null}{l.do}</li>)}
              </ul>
            </div>
          )}
          {(r.unanswered?.length ?? 0) > 0 && (
            <p><span className="font-medium">Couldn’t answer:</span> {r.unanswered!.join(' · ')}</p>
          )}
          {r.metrics && (
            <p className="text-muted-foreground">
              We spoke {r.metrics.talk_ratio == null ? '—' : `${Math.round(r.metrics.talk_ratio * 100)}%`} of the time · longest turn {r.metrics.longest_turn_words} words
              {r.metrics.long_turns ? ` · ${r.metrics.long_turns} over 25 words` : ''} · {r.metrics.questions} questions
              {r.metrics.interruptions ? ` · talked over ${r.metrics.interruptions}×` : ''}
              {r.metrics.latency_p95_s ? ` · replies in ${r.metrics.latency_p95_s.toFixed(1)}s (p95)` : ''}
            </p>
          )}
          {r.text_cleared && <p className="text-muted-foreground">The coach’s notes were cleared after the retention period; the scores stay.</p>}
        </>
      )}
      {attempt.playbook_id && (
        <p className="text-muted-foreground">
          Used playbook {attempt.playbook_id.split('@')[1] ?? attempt.playbook_id}{attempt.arm && attempt.arm !== 'off' ? ` (${attempt.arm === 'challenger' ? 'the one being tested' : 'the current one'})` : ''}
          {attempt.playbook_applied === false ? ' — it could not be loaded, so the call used the baseline' : ''}
        </p>
      )}
    </section>
  )
}
