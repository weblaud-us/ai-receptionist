import { ErrorBanner, PageHeader } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateOnly, dateTime } from '@receptionist/shared/lib/format'
import { useConsentEvents, usePrivacyRequests } from '@/lib/platform'

/** Phase 6: data subject requests across every business, and opt-outs from every channel. */
export default function Compliance() {
  const requests = usePrivacyRequests()
  const events = useConsentEvents()
  return (
    <>
      <PageHeader
        title="Compliance"
        description="Evidence that requests were answered in time and that every “stop” was honoured. Checklist per market: docs/COMPLIANCE.md."
      />
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Data subject requests</CardTitle>
            <CardDescription>Logged when an owner (or you, on their written instruction) exports or deletes a caller’s data.</CardDescription>
          </CardHeader>
          <CardContent>
            {requests.error && <ErrorBanner error={requests.error} />}
            {requests.isLoading && <Skeleton className="h-32" />}
            {requests.data && requests.data.requests.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
            {requests.data && requests.data.requests.length > 0 && (
              <Table>
                <TableHeader><TableRow><TableHead>Business</TableHead><TableHead>Type</TableHead><TableHead>Number</TableHead><TableHead>Received</TableHead><TableHead>Done</TableHead><TableHead /></TableRow></TableHeader>
                <TableBody>
                  {requests.data.requests.map((r) => {
                    const late = new Date(r.completed_at) > new Date(r.due_at)
                    return (
                      <TableRow key={r.id}>
                        <TableCell>{r.business}</TableCell>
                        <TableCell>{r.kind === 'access' ? 'Access' : 'Erasure'}</TableCell>
                        <TableCell className="tabular-nums">{r.phone_masked}</TableCell>
                        <TableCell>{dateOnly(r.received_at)}</TableCell>
                        <TableCell>{dateOnly(r.completed_at)}</TableCell>
                        <TableCell>{late && <Badge variant="destructive">Late</Badge>}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Opt-outs and opt-ins</CardTitle>
            <CardDescription>SMS STOP, WhatsApp, erasure. Platform-wide ones also go on the sales do-not-call list (permanent).</CardDescription>
          </CardHeader>
          <CardContent>
            {events.error && <ErrorBanner error={events.error} />}
            {events.isLoading && <Skeleton className="h-32" />}
            {events.data && events.data.events.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
            {events.data && events.data.events.length > 0 && (
              <Table>
                <TableHeader><TableRow><TableHead>When</TableHead><TableHead>Number</TableHead><TableHead>What</TableHead><TableHead>Channel</TableHead><TableHead>Scope</TableHead></TableRow></TableHeader>
                <TableBody>
                  {events.data.events.map((e, i) => (
                    <TableRow key={i}>
                      <TableCell>{dateTime(e.at)}</TableCell>
                      <TableCell className="tabular-nums">{e.phone}</TableCell>
                      <TableCell>{e.kind === 'opt_out' ? 'Opt-out' : 'Opt-in'}{e.dnc_added && <Badge variant="secondary" className="ml-2">DNC</Badge>}</TableCell>
                      <TableCell>{e.channel}</TableCell>
                      <TableCell>{e.scope === 'platform' ? 'All businesses' : e.tenant_id}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  )
}
