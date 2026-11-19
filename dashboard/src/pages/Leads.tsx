import { useState } from 'react'
import { ClipboardList, Phone } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { timeAgo, titleCase } from '@receptionist/shared/lib/format'
import { URGENCY_STYLE } from '@/lib/tenant-format'
import { useLeads, useUpdateLead } from '@/lib/tenant-hooks'
import { cn } from '@receptionist/shared/lib/utils'

const STATUSES = ['new', 'contacted', 'won', 'lost']

export default function Leads() {
  const [filter, setFilter] = useState('open')
  const q = useLeads(filter === 'open' ? 'new' : filter === 'all' ? undefined : filter)
  const update = useUpdateLead()
  return (
    <>
      <PageHeader title="Job requests" description="Every job your assistant captured: who, where, what's wrong and how urgent."
        actions={<SimpleSelect size="sm" value={filter} onChange={setFilter}
          options={[{ value: 'open', label: 'New' }, { value: 'contacted', label: 'Contacted' }, { value: 'won', label: 'Won' }, { value: 'lost', label: 'Lost' }, { value: 'all', label: 'All' }]} />} />
      {q.error && <ErrorBanner error={q.error} />}
      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={5} cols={5} /> : (q.data?.leads.length ?? 0) === 0 ? (
          <EmptyState title="No job requests here" icon={<ClipboardList className="size-8" />} description="When callers describe a job, it lands here and you get a text." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>When</TableHead><TableHead>Urgency</TableHead><TableHead>Customer</TableHead><TableHead>Job</TableHead><TableHead>Status</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {q.data?.leads.map((l) => (
                <TableRow key={l.id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{timeAgo(l.created_at)}</TableCell>
                  <TableCell><Badge variant="secondary" className={cn('border-0', URGENCY_STYLE[l.urgency])}>{titleCase(l.urgency)}</Badge></TableCell>
                  <TableCell>
                    <div className="font-medium">{l.name}</div>
                    <Button size="xs" variant="link" className="h-auto px-0" nativeButton={false} render={<a href={`tel:${l.phone}`} />}><Phone /> {l.phone}</Button>
                    {l.address && <div className="text-xs text-muted-foreground">{l.address}</div>}
                  </TableCell>
                  <TableCell className="max-w-md whitespace-normal">
                    {l.problem}
                    {l.preferred_time && <div className="text-xs text-muted-foreground">Prefers: {l.preferred_time}</div>}
                  </TableCell>
                  <TableCell>
                    <SimpleSelect size="sm" value={l.status} onChange={(v) => update.mutate({ id: l.id, status: v })}
                      options={STATUSES.map((s) => ({ value: s, label: titleCase(s) }))} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  )
}
