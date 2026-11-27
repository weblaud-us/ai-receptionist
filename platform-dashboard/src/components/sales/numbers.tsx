import { toast } from 'sonner'
import { AlertTriangle, PauseCircle, PlayCircle } from 'lucide-react'
import { ErrorBanner } from '@receptionist/shared/components/blocks'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateTime } from '@receptionist/shared/lib/format'
import { useCan } from '@/lib/roles'
import { pct, useNumberHealth, useSafetyMutations } from '@/lib/sales'

/** Caller ID health (SALES_SUITE_PLAN.md Phase 12): how each outbound number is doing over 7 days. */
export function NumbersCard() {
  const q = useNumberHealth()
  const m = useSafetyMutations()
  const can = useCan()
  const rows = q.data?.numbers ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle>Caller IDs</CardTitle>
        <CardDescription>
          The last 7 days for each number. A number answering far less than your others (after {q.data?.min_dials ?? 30} calls) is probably labelled “Spam likely”; it rests for {q.data?.rest_hours ?? 72} hours while another number for that country keeps calling.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {q.error && <ErrorBanner error={q.error} />}
        {q.isLoading ? <p className="text-sm text-muted-foreground">Loading…</p>
          : rows.length === 0 ? (!q.error && <p className="text-sm text-muted-foreground">No caller IDs yet.</p>) : (
          <Table>
            <TableHeader><TableRow><TableHead>Number</TableHead><TableHead>Calls</TableHead><TableHead>Answered</TableHead><TableHead>A person</TableHead><TableHead>Hung up in 5 s</TableHead><TableHead>Opt-outs</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.number}>
                  <TableCell>
                    <div className="font-mono text-sm">{r.number}</div>
                    {r.rested_until ? <div className="text-xs text-amber-700 dark:text-amber-400">Resting until {dateTime(r.rested_until)}{r.rest_reason && ` · ${r.rest_reason}`}</div>
                      : r.suspect && <div className="flex items-center gap-1 text-xs text-destructive"><AlertTriangle className="size-3" /> {r.suspect}</div>}
                  </TableCell>
                  <TableCell className="tabular-nums">{r.dials}</TableCell>
                  <TableCell className="tabular-nums">{pct(r.answer_rate)} <span className="text-xs text-muted-foreground">(was {pct(r.previous.answer_rate)})</span></TableCell>
                  <TableCell className="tabular-nums">{pct(r.human_rate)}</TableCell>
                  <TableCell className="tabular-nums">{pct(r.short_hangup_rate)}</TableCell>
                  <TableCell className="tabular-nums">{r.opt_outs}</TableCell>
                  <TableCell className="text-right">
                    {can('manager') && (r.rested_until ? (
                      <Button size="sm" variant="ghost" onClick={() => m.reactivate.mutate(r.number, { onSuccess: () => toast.success('Calling from it again') })}><PlayCircle /> Reactivate</Button>
                    ) : (
                      <Button size="sm" variant="ghost" onClick={() => m.rest.mutate({ number: r.number }, { onSuccess: () => toast.success('Resting for 3 days') })}><PauseCircle /> Rest</Button>
                    ))}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
