import { toast } from 'sonner'
import { Copy, Phone, PhoneForwarded } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader } from '@/components/app/blocks'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { usePhoneNumbers } from '@/lib/tenant-hooks'
import { TestCallButton } from './Assistant'

export default function PhoneNumbers() {
  const q = usePhoneNumbers()
  const numbers = q.data?.numbers ?? []
  return (
    <>
      <PageHeader title="Phone numbers" description="Your assistant's number, and how to send your existing line to it." />
      {q.error && <ErrorBanner error={q.error} />}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Phone className="size-4" /> Your AI number</CardTitle>
            <CardDescription>Calls to this number are answered by your assistant.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {numbers.length === 0 && !q.isLoading && (
              <EmptyState title="No number yet" description="We'll assign one during setup. Until then, try your assistant from the browser." />
            )}
            {numbers.map((n) => (
              <div key={n.e164} className="flex items-center justify-between rounded-lg border px-3 py-2">
                <span className="font-mono text-lg">{n.e164}</span>
                <span className="flex items-center gap-2">
                  <Badge variant="secondary">{n.purpose === 'inbound' ? 'Inbound' : n.purpose}</Badge>
                  <Button size="icon-sm" variant="ghost" aria-label="Copy"
                    onClick={() => void navigator.clipboard.writeText(n.e164).then(() => toast.success('Copied'))}><Copy /></Button>
                </span>
              </div>
            ))}
            <TestCallButton label="Test from the browser" />
          </CardContent>
        </Card>
        <Card className="lg:col-span-2 gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle className="flex items-center gap-2"><PhoneForwarded className="size-4" /> Keep your number — forward it</CardTitle>
            <CardDescription>{q.data?.note}</CardDescription>
          </CardHeader>
          <Table>
            <TableHeader>
              <TableRow><TableHead>Carrier</TableHead><TableHead>No answer</TableHead><TableHead>Busy</TableHead><TableHead>Unreachable</TableHead><TableHead>Turn off</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {q.data?.forwarding.map((r) => (
                <TableRow key={r.carrier}>
                  <TableCell className="max-w-48 whitespace-normal font-medium">{r.carrier}</TableCell>
                  <TableCell className="font-mono text-xs whitespace-normal">{r.no_answer}</TableCell>
                  <TableCell className="font-mono text-xs whitespace-normal">{r.busy}</TableCell>
                  <TableCell className="font-mono text-xs whitespace-normal">{r.unreachable}</TableCell>
                  <TableCell className="font-mono text-xs whitespace-normal">{r.cancel}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <p className="border-t px-4 py-3 text-xs text-muted-foreground">
            Dial the code from the phone whose calls you want answered. Codes differ between carriers and plans — if one doesn't work, ask your carrier for "conditional call forwarding".
          </p>
        </Card>
      </div>
    </>
  )
}
