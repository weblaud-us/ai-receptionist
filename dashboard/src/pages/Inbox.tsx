import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, Check, Clock, Inbox as InboxIcon, Phone } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { timeAgo } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useAuth } from '@receptionist/shared/auth/auth'
import { useCtx } from '@/lib/tenant'
import { zoneDateTime } from '@/lib/tenant-format'
import { useInbox, useInboxMutation, useTeam } from '@/lib/tenant-hooks'
import type { InboxItem } from '@/lib/tenant-types'

type Filter = 'all' | 'mine' | 'unassigned' | 'overdue'

const KIND_LABEL: Record<InboxItem['kind'], string> = {
  message: 'Message', lead: 'Lead', escalation: 'Emergency', confirmation: 'Confirmation',
}

/** Plan 4.1: everything the front desk must follow up, oldest-overdue first. */
export default function InboxPage() {
  const ctx = useCtx()
  const { user } = useAuth()
  const inbox = useInbox()
  const team = useTeam()
  const update = useInboxMutation()
  const [filter, setFilter] = useState<Filter>('all')
  const members = (team.data?.members ?? []).filter((m) => !m.disabled && m.role !== 'dentist')
  const names = Object.fromEntries(members.map((m) => [m.id, m.name || m.email]))
  const items = useMemo(() => {
    const all = inbox.data?.items ?? []
    if (filter === 'mine') return all.filter((i) => i.assignee_id && i.assignee_id === user?.id)
    if (filter === 'unassigned') return all.filter((i) => !i.assignee_id)
    if (filter === 'overdue') return all.filter((i) => i.overdue)
    return all
  }, [inbox.data, filter, user?.id])
  const assignOptions = [{ value: '', label: 'Unassigned' }, ...members.map((m) => ({ value: m.id, label: m.name || m.email }))]

  return (
    <>
      <PageHeader title="Inbox" help="inbox" description="Callbacks, leads, emergencies nobody has taken yet, and patients the confirmation call didn't reach. Assign each one, and mark it done when it's handled." />
      {inbox.error && <ErrorBanner error={inbox.error} onRetry={() => void inbox.refetch()} />}
      <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)} className="mb-3">
        <TabsList>
          <TabsTrigger value="all">All <Badge variant="secondary" className="ml-1">{inbox.data?.items.length ?? 0}</Badge></TabsTrigger>
          <TabsTrigger value="overdue">Overdue <Badge variant="secondary" className="ml-1">{inbox.data?.overdue ?? 0}</Badge></TabsTrigger>
          <TabsTrigger value="unassigned">Unassigned <Badge variant="secondary" className="ml-1">{inbox.data?.unassigned ?? 0}</Badge></TabsTrigger>
          <TabsTrigger value="mine">Mine</TabsTrigger>
        </TabsList>
      </Tabs>
      <Card className="gap-0 py-0">
        {inbox.isLoading ? (
          <div className="p-4"><TableSkeleton rows={4} /></div>
        ) : items.length === 0 ? (
          <EmptyState title="Nothing to follow up" icon={<InboxIcon className="size-8" />}
            description="New callbacks, leads and missed confirmations appear here." />
        ) : (
          <ul className="divide-y">
            {items.map((i) => (
              <li key={`${i.kind}:${i.id}`}
                className={cn('flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:gap-6', i.overdue && 'border-l-4 border-l-red-500')}>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{KIND_LABEL[i.kind]}</Badge>
                    {i.urgent && <Badge variant="secondary" className="gap-1 border-0 bg-red-100 text-red-800 dark:bg-red-500/20 dark:text-red-300"><AlertTriangle className="size-3" /> Urgent</Badge>}
                    <span className="font-medium">{i.title}</span>
                  </div>
                  {i.detail && <p className="text-sm text-muted-foreground">{i.detail}</p>}
                  <p className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Clock className="size-3" />
                    {i.due_at ? (i.overdue ? `Overdue since ${zoneDateTime(i.due_at, ctx.tenant.timezone)}` : `Due ${zoneDateTime(i.due_at, ctx.tenant.timezone)}`) : ''}
                    {i.created_at && <span className="ml-2">· {timeAgo(i.created_at)}</span>}
                    {i.link && <Link to={i.link} className="ml-2 underline">Open</Link>}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                  {i.phone && (
                    <Button size="sm" variant="outline" nativeButton={false} render={<a href={`tel:${i.phone}`} />}>
                      <Phone /> Call
                    </Button>
                  )}
                  <SimpleSelect size="sm" value={i.assignee_id ?? ''} options={assignOptions}
                    onChange={(v) => update.mutate({ kind: i.kind, id: i.id, assignee_id: v || null },
                      { onSuccess: () => toast.success(v ? `Assigned to ${names[v] ?? 'them'}` : 'Unassigned') })} />
                  <Button size="sm" disabled={update.isPending}
                    onClick={() => update.mutate({ kind: i.kind, id: i.id, done: true }, { onSuccess: () => toast.success('Done') })}>
                    <Check /> Done
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}
