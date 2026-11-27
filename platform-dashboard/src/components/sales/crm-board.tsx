import { useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, CalendarClock, ListTodo } from 'lucide-react'
import { ErrorBanner, Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { dateTime, timeAgo } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useCan } from '@/lib/roles'
import { DEAL_STAGE_LABEL, DEAL_STAGES, useBoard, useCrmMutations, useMyWork, usePeople, type Board, type Deal, type DealStage } from '@/lib/sales'
import { money, moveDeal } from './crm-logic'

const COLUMN_STYLE: Record<DealStage, string> = {
  qualified: 'border-t-sky-500', meeting_booked: 'border-t-violet-500', proposal: 'border-t-amber-500',
  won: 'border-t-emerald-500', lost: 'border-t-slate-400',
}

/** The deal board (SALES_SUITE_PLAN.md Phase 15): drag a deal to move it on; totals and weighted value per stage. */
export function DealBoard({ onOpenLead }: { onOpenLead: (leadId: string) => void }) {
  const [owner, setOwner] = useState('')
  const q = useBoard(owner)
  const people = usePeople().data?.people ?? []
  const m = useCrmMutations()
  const can = useCan()
  const [optimistic, setOptimistic] = useState<Board | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)
  const [losing, setLosing] = useState<Deal | null>(null)
  const [reason, setReason] = useState('')
  const board = optimistic ?? q.data

  const move = (deal: Deal, to: DealStage, lostReason?: string) => {
    if (!board || deal.stage === to) return
    if (to === 'lost' && !lostReason) {
      setLosing(deal)
      return
    }
    setOptimistic(moveDeal(board, deal.id, to))
    m.updateDeal.mutate({ id: deal.id, stage: to, ...(lostReason ? { lost_reason: lostReason } : {}) }, {
      onSuccess: () => toast.success(`${deal.title}: ${DEAL_STAGE_LABEL[to]}`),
      onError: (e) => toast.error(e.message),
      onSettled: () => setOptimistic(null),
    })
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">Drag a deal to move it on. Calls open deals by themselves when someone is interested or books a meeting.</span>
        <div className="flex-1" />
        <SimpleSelect size="sm" value={owner} onChange={setOwner}
          options={[{ value: '', label: 'Everyone’s deals' }, ...people.map((p) => ({ value: p.id, label: p.name }))]} />
        {board && (
          <span className="tabular-nums text-muted-foreground">
            Open {money(DEAL_STAGES.slice(0, 3).reduce((n, s) => n + board.totals[s].value, 0))} · forecast {money(DEAL_STAGES.slice(0, 3).reduce((n, s) => n + board.totals[s].weighted, 0))}
          </span>
        )}
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
        {DEAL_STAGES.map((stage) => (
          <div key={stage}
            className={cn('rounded-lg border border-t-4 bg-muted/30 p-2 transition-colors', COLUMN_STYLE[stage], dragging && 'border-dashed')}
            onDragOver={(e) => can('caller') && e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              const deal = board && Object.values(board.columns).flat().find((d) => d.id === e.dataTransfer.getData('text/deal'))
              if (deal) move(deal, stage)
              setDragging(null)
            }}>
            <div className="mb-1 flex items-center justify-between px-1 text-sm font-medium">
              {DEAL_STAGE_LABEL[stage]} <span className="text-muted-foreground tabular-nums">{board?.totals[stage].count ?? 0}</span>
            </div>
            <div className="mb-2 px-1 text-xs text-muted-foreground tabular-nums">
              {money(board?.totals[stage].value ?? 0)}{stage !== 'won' && stage !== 'lost' && ` · ${Math.round((board?.probability[stage] ?? 0) * 100)}%`}
            </div>
            <div className="max-h-[28rem] space-y-2 overflow-y-auto">
              {(board?.columns[stage] ?? []).map((d) => (
                <button key={d.id} type="button" draggable={can('caller')}
                  onDragStart={(e) => { e.dataTransfer.setData('text/deal', d.id); setDragging(d.id) }}
                  onDragEnd={() => setDragging(null)}
                  onClick={() => onOpenLead(d.lead_id)}
                  className={cn('w-full rounded-md border bg-background p-2 text-left text-sm shadow-xs hover:border-primary/50', dragging === d.id && 'opacity-50')}>
                  <div className="font-medium">{d.lead?.business_name || d.title}</div>
                  <div className="text-xs text-muted-foreground">{d.title}</div>
                  <div className="mt-1 flex items-center justify-between text-xs">
                    <span className="font-medium tabular-nums">{money(d.amount, d.currency)}</span>
                    <span className="text-muted-foreground">{d.expected_close ? `closes ${d.expected_close}` : timeAgo(d.updated_at)}</span>
                  </div>
                  {d.owner_id && <div className="mt-0.5 text-xs text-muted-foreground">{people.find((p) => p.id === d.owner_id)?.name ?? 'Owner'}</div>}
                  {stage === 'lost' && d.lost_reason && <div className="mt-0.5 text-xs text-muted-foreground">{d.lost_reason}</div>}
                </button>
              ))}
              {!q.isLoading && (board?.columns[stage]?.length ?? 0) === 0 && <p className="px-1 py-3 text-xs text-muted-foreground">No deals.</p>}
            </div>
          </div>
        ))}
      </div>
      <Dialog open={!!losing} onOpenChange={(o) => !o && setLosing(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader><DialogTitle>Why was it lost?</DialogTitle></DialogHeader>
          <Field label="Reason" hint="It shows in Reports, so you can see what loses deals.">
            <Input autoFocus value={reason} maxLength={300} placeholder="Went with a cheaper provider" onChange={(e) => setReason(e.target.value)} />
          </Field>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLosing(null)}>Cancel</Button>
            <Button disabled={reason.trim().length < 3} onClick={() => { if (losing) move(losing, 'lost', reason.trim()); setLosing(null); setReason('') }}>Mark lost</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/** Today's work for the person signed in: tasks due, open deals, their leads. */
export function MyWorkCard({ onOpenLead }: { onOpenLead: (leadId: string) => void }) {
  const q = useMyWork()
  const w = q.data
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><ListTodo className="size-4" /> My work</CardTitle>
        <CardDescription>{w ? `${w.tasks.length} task${w.tasks.length === 1 ? '' : 's'} due${w.overdue ? `, ${w.overdue} overdue` : ''} · ${w.deals.length} open deal${w.deals.length === 1 ? '' : 's'} (${money(w.pipeline_value)} weighted)` : 'Loading…'}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        {w?.tasks.slice(0, 8).map((t) => (
          <button key={t.id} type="button" onClick={() => onOpenLead(t.lead_id)} className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-muted">
            {t.due_at && new Date(t.due_at) < new Date() ? <AlertTriangle className="size-3.5 text-destructive" /> : <CalendarClock className="size-3.5 text-muted-foreground" />}
            <span className="flex-1 truncate">{t.title}</span>
            {t.due_at && <span className="text-xs text-muted-foreground">{dateTime(t.due_at)}</span>}
          </button>
        ))}
        {w && w.tasks.length === 0 && <p className="text-muted-foreground">Nothing due. Leads and deals assigned to you show up here.</p>}
        {w?.deals.slice(0, 5).map((d) => (
          <button key={d.id} type="button" onClick={() => onOpenLead(d.lead_id)} className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-muted">
            <Badge variant="secondary">{DEAL_STAGE_LABEL[d.stage]}</Badge><span className="flex-1 truncate">{d.title}</span>
            <span className="text-xs tabular-nums">{money(d.amount, d.currency)}</span>
          </button>
        ))}
      </CardContent>
    </Card>
  )
}
