import { useState } from 'react'
import { toast } from 'sonner'
import { Ear, Mic, PhoneCall, PhoneOff } from 'lucide-react'
import { EmptyState, ErrorBanner } from '@receptionist/shared/components/blocks'
import { CallAudio } from '@/components/sales/call-audio'
import { PowerDialer } from '@/components/sales/power-dialer'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { timeAgo, titleCase } from '@receptionist/shared/lib/format'
import { MODE_LABEL, useLiveSalesCalls, useSalesMutations, type JoinInfo } from '@/lib/sales'

const STAGE: Record<string, string> = { dispatched: 'Starting', dialing: 'Ringing', in_progress: 'On the call' }

export function Live() {
  const q = useLiveSalesCalls()
  const m = useSalesMutations()
  const [joined, setJoined] = useState<{ join: JoinInfo; title: string; attemptId: string } | null>(null)

  const join = (id: string, title: string, mode: 'listen' | 'takeover') =>
    m.join.mutate({ id, mode }, { onSuccess: (j) => setJoined({ join: j, title, attemptId: id }) })
  const hangUp = (id: string) => m.hangUp.mutate(id, { onSuccess: () => toast.success('Call ended') })

  return (
    <div className="space-y-4">
      {joined && <CallAudio key={joined.join.token} join={joined.join} title={joined.title} onLeave={() => setJoined(null)}
        onHangUp={joined.join.mode === 'takeover' ? () => hangUp(joined.attemptId) : undefined} />}
      <PowerDialer />
      {q.error && <ErrorBanner error={q.error} />}
      <Card className="gap-0 py-0">
        {(q.data?.total ?? 0) === 0 ? (
          <EmptyState title="No sales calls in progress" icon={<PhoneCall className="size-8" />} description="Calls appear here while they ring and while they are connected." />
        ) : (
          <Table>
            <TableHeader><TableRow><TableHead>Business</TableHead><TableHead>Stage</TableHead><TableHead>Type</TableHead><TableHead>Started</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {q.data?.calls.map((c) => {
                const title = c.business_name || c.to
                return (
                  <TableRow key={c.id}>
                    <TableCell><div className="font-medium">{title}</div><div className="font-mono text-xs text-muted-foreground">{c.to}</div></TableCell>
                    <TableCell>{STAGE[c.status] ?? titleCase(c.status)}{c.amd ? ` · ${titleCase(c.amd.replace('-', ' '))}` : ''}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{MODE_LABEL[c.mode]}</TableCell>
                    <TableCell className="text-muted-foreground">{timeAgo(c.created_at)}</TableCell>
                    <TableCell className="space-x-1 text-right">
                      <Button size="sm" variant="outline" disabled={!!joined || m.join.isPending} onClick={() => join(c.id, title, 'listen')}><Ear /> Listen</Button>
                      <Button size="sm" variant="outline" disabled={!!joined || m.join.isPending} onClick={() => join(c.id, title, 'takeover')}><Mic /> Take over</Button>
                      <Button size="sm" variant="ghost" className="text-destructive" onClick={() => hangUp(c.id)}><PhoneOff /> End</Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  )
}
