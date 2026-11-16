import { useState } from 'react'
import { toast } from 'sonner'
import { Ear, Loader2, Mic, PhoneCall, PhoneOff, PhoneOutgoing } from 'lucide-react'
import { EmptyState, ErrorBanner } from '@/components/app/blocks'
import { CallAudio } from '@/components/sales/call-audio'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { timeAgo, titleCase } from '@/lib/format'
import { MODE_LABEL, useCampaigns, useLiveSalesCalls, useSalesMutations, type JoinInfo } from '@/lib/sales'

const REASON: Record<string, string> = {
  no_leads: 'No leads are due to be called right now.',
  busy: 'This campaign already has its maximum number of calls in progress.',
  daily_cap: "This campaign has reached today's call limit.",
  numbers_resting: 'Every caller ID has reached its daily volume. Try again later.',
  dispatch_failed: 'Could not start the call. Is the sales worker running?',
  gate: 'The next lead did not pass the compliance checks.',
}
const STAGE: Record<string, string> = { dispatched: 'Starting', dialing: 'Ringing', in_progress: 'On the call' }

export function Live() {
  const q = useLiveSalesCalls()
  const m = useSalesMutations()
  const campaigns = (useCampaigns().data?.campaigns ?? []).filter((c) => c.mode === 'human_assisted' && c.status === 'running')
  const [joined, setJoined] = useState<{ join: JoinInfo; title: string; attemptId: string } | null>(null)

  const join = (id: string, title: string, mode: 'listen' | 'takeover') =>
    m.join.mutate({ id, mode }, { onSuccess: (j) => setJoined({ join: j, title, attemptId: id }), onError: (e) => toast.error(e.message) })
  const hangUp = (id: string) => m.hangUp.mutate(id, { onSuccess: () => toast.success('Call ended'), onError: (e) => toast.error(e.message) })
  const dialNext = (campaignId: string) =>
    m.dialNext.mutate(campaignId, {
      onSuccess: (r) => {
        if (r.dialed && r.join && r.attempt_id) setJoined({ join: r.join, title: `Calling ${r.lead?.business_name || r.lead?.phone}`, attemptId: r.attempt_id })
        else toast.message(REASON[r.reason ?? ''] ?? 'Nothing was dialled.')
      },
      onError: (e) => toast.error(e.message),
    })

  return (
    <div className="space-y-4">
      {joined && <CallAudio key={joined.join.token} join={joined.join} title={joined.title} onLeave={() => setJoined(null)}
        onHangUp={joined.join.mode === 'takeover' ? () => hangUp(joined.attemptId) : undefined} />}
      {campaigns.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><PhoneOutgoing className="size-4" /> Dial for me</CardTitle>
            <CardDescription>Rings the next lead that passes the checks and puts you on the call. Answering machines are hung up on automatically; allow the microphone when asked.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {campaigns.map((c) => (
              <Button key={c.id} disabled={m.dialNext.isPending || !!joined} onClick={() => dialNext(c.id)}>
                {m.dialNext.isPending ? <Loader2 className="animate-spin" /> : <PhoneCall />} Dial next — {c.name} ({c.progress?.to_dial ?? 0} left)
              </Button>
            ))}
          </CardContent>
        </Card>
      )}
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
