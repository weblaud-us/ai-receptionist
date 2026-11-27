import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'
import { ErrorBanner, PageHeader } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { dateTime } from '@receptionist/shared/lib/format'
import { useNumberOrderMutations, useNumberOrders, usePlatformTenants, type NumberOrder } from '@/lib/platform'

const FILTERS = [
  { value: 'needs_documents', label: 'Waiting for documents' },
  { value: 'failed', label: 'Failed' },
  { value: '', label: 'All orders' },
]

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  active: 'default', needs_documents: 'secondary', pending: 'outline', running: 'outline',
  failed: 'destructive', verify_failed: 'destructive', cancelled: 'outline',
}

/**
 * Number orders. Outside the US and Canada a number needs a Twilio regulatory bundle (business address
 * and ID in that country) before it can be bought, so those orders wait here: create the bundle in the
 * Twilio console, and when Twilio approves it paste its SID (and the address SID) and approve.
 */
export default function Numbers() {
  const [status, setStatus] = useState('needs_documents')
  const orders = useNumberOrders(status)
  const tenants = usePlatformTenants()
  const names = Object.fromEntries((tenants.data?.tenants ?? []).map((t) => [t.id, t.name]))
  return (
    <>
      <PageHeader
        title="Numbers"
        help="numbers"
        description="Australian, UK and EU numbers need a regulatory bundle approved by Twilio before they can be bought. You get an e-mail for each new order."
        actions={<SimpleSelect value={status} onChange={setStatus} options={FILTERS} />}
      />
      <Card>
        <CardHeader>
          <CardTitle>Orders</CardTitle>
          <CardDescription>
            Twilio console → Phone Numbers → Regulatory Compliance → Bundles. Use the business's own details; approval usually takes 1–3 business days.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {orders.error && <ErrorBanner error={orders.error} />}
          {orders.isLoading && <Skeleton className="h-32" />}
          {orders.data && orders.data.orders.length === 0 && <p className="text-sm text-muted-foreground">Nothing here.</p>}
          {orders.data && orders.data.orders.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Business</TableHead><TableHead>Number</TableHead><TableHead>Country</TableHead>
                  <TableHead>Status</TableHead><TableHead>Ordered</TableHead><TableHead className="w-[28rem]">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {orders.data.orders.map((o) => (
                  <TableRow key={o.id}>
                    <TableCell>{names[o.tenant_id] ?? o.tenant_id}</TableCell>
                    <TableCell className="tabular-nums">{o.e164}</TableCell>
                    <TableCell>{o.country}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[o.status] ?? 'outline'}>{o.status.replace('_', ' ')}</Badge>
                      {o.error && <div className="mt-1 max-w-56 text-xs text-destructive">{o.error}</div>}
                    </TableCell>
                    <TableCell>{dateTime(o.created_at)}</TableCell>
                    <TableCell><OrderAction order={o} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  )
}

function OrderAction({ order }: { order: NumberOrder }) {
  const m = useNumberOrderMutations()
  const [bundle, setBundle] = useState('')
  const [address, setAddress] = useState('')
  if (order.status === 'needs_documents') {
    const ok = /^BU[0-9a-fA-F]{32}$/.test(bundle.trim()) && (!address.trim() || /^AD[0-9a-fA-F]{32}$/.test(address.trim()))
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Input className="h-8 w-44" placeholder="Bundle SID (BU…)" value={bundle} onChange={(e) => setBundle(e.target.value)} />
        <Input className="h-8 w-44" placeholder="Address SID (AD…)" value={address} onChange={(e) => setAddress(e.target.value)} />
        <Button size="sm" disabled={!ok || m.approve.isPending}
          onClick={() => m.approve.mutate({ id: order.id, bundle_sid: bundle.trim(), address_sid: address.trim() },
            { onSuccess: () => toast.success(`Buying ${order.e164}…`) })}>
          {m.approve.isPending && <Loader2 className="animate-spin" />} Approve
        </Button>
      </div>
    )
  }
  if (order.status === 'failed' || order.status === 'verify_failed' || order.status === 'pending') {
    return (
      <Button size="sm" variant="outline" disabled={m.retry.isPending}
        onClick={() => m.retry.mutate(order.id, { onSuccess: () => toast.success('Retried') })}>
        {m.retry.isPending && <Loader2 className="animate-spin" />} Retry
      </Button>
    )
  }
  return null
}
