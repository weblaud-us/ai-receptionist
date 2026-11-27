import { useState } from 'react'
import { toast } from 'sonner'
import { Download, ShieldCheck } from 'lucide-react'
import { EmptyState, ErrorBanner, Pagination, TableSkeleton } from '@receptionist/shared/components/blocks'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateTime } from '@receptionist/shared/lib/format'
import { downloadSalesCsv, useAudit, verifyAudit } from '@/lib/sales'

const summary = (diff: Record<string, unknown>) =>
  Object.entries(diff ?? {}).filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && !v.length))
    .slice(0, 4).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v).slice(0, 60) : String(v).slice(0, 60)}`).join(' · ')

/** Who did what in the sales dialer (SALES_SUITE_PLAN.md Phase 18). Entries are hash-chained and never edited. */
export function ActivityLog() {
  const [action, setAction] = useState('')
  const [since, setSince] = useState('')
  const [page, setPage] = useState(1)
  const [checking, setChecking] = useState(false)
  const q = useAudit({ action: action ? `sales.${action.replace(/^sales\./, '')}` : undefined, since: since || undefined, page })
  const verify = () => {
    setChecking(true)
    verifyAudit()
      .then((r) => (r.intact ? toast.success('The audit trail is intact: nothing was edited, removed or inserted.') : toast.error(`The chain is broken: ${r.problems[0]}`)))
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setChecking(false))
  }
  const exportCsv = () =>
    downloadSalesCsv('audit', { action: action ? `sales.${action.replace(/^sales\./, '')}` : undefined, since: since || undefined })
      .catch((e: Error) => toast.error(e.message))
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input className="max-w-xs" placeholder="Filter by action, e.g. offer or campaign.start" value={action} onChange={(e) => { setAction(e.target.value); setPage(1) }} />
        <Input className="w-40" type="date" value={since} onChange={(e) => { setSince(e.target.value); setPage(1) }} />
        <div className="flex-1" />
        <Button size="sm" variant="outline" disabled={checking} onClick={verify}><ShieldCheck /> Check the chain</Button>
        <Button size="sm" variant="outline" onClick={() => void exportCsv()}><Download /> CSV</Button>
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={8} cols={4} /> : (q.data?.entries.length ?? 0) === 0 ? <EmptyState title="Nothing logged yet" /> : (
          <>
            <Table>
              <TableHeader><TableRow><TableHead>When</TableHead><TableHead>Who</TableHead><TableHead>What</TableHead><TableHead>Details</TableHead></TableRow></TableHeader>
              <TableBody>
                {q.data?.entries.map((e, i) => (
                  <TableRow key={e.id ?? i}>
                    <TableCell className="whitespace-nowrap text-xs">{dateTime(e.at)}</TableCell>
                    <TableCell className="text-xs">{e.user_name}</TableCell>
                    <TableCell className="text-xs"><span className="font-mono">{e.action.replace(/^sales\./, '')}</span><div className="text-muted-foreground">{e.target_type} {e.target_id}</div></TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground" title={JSON.stringify(e.diff)}>{summary(e.diff)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pagination page={q.data?.page ?? 1} pages={q.data?.pages ?? 1} total={q.data?.total ?? 0} onPage={setPage} label="entries" />
          </>
        )}
      </Card>
    </div>
  )
}
