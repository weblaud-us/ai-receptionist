import { useState } from 'react'
import { toast } from 'sonner'
import { Copy, Loader2, Plus, Send, Trash2, Webhook } from 'lucide-react'
import { ConfirmDialog, ErrorBanner, Field } from '@/components/app/blocks'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { timeAgo } from '@/lib/format'
import { useTenant } from '@/lib/tenant'
import { useWebhookMutations, useWebhooks } from '@/lib/tenant-hooks'
import type { WebhookEndpoint } from '@/lib/tenant-types'

const EVENT_LABEL: Record<string, string> = {
  'appointment.created': 'New booking',
  'appointment.cancelled': 'Booking cancelled',
  'message.created': 'Message taken',
  'lead.created': 'Job request',
  'call.completed': 'Call finished',
}

/** Outgoing webhooks (Roadmap Task 2.8): Zapier, Make, or the owner's own software. */
export function WebhooksCard() {
  const { can } = useTenant()
  const q = useWebhooks(can('admin'))
  const m = useWebhookMutations()
  const [url, setUrl] = useState('')
  const [events, setEvents] = useState<string[]>([])
  const [secret, setSecret] = useState<{ id: string; secret: string } | null>(null)
  const [removing, setRemoving] = useState<WebhookEndpoint | null>(null)
  const admin = can('admin')

  const toggle = (e: string) => setEvents((cur) => (cur.includes(e) ? cur.filter((x) => x !== e) : [...cur, e]))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Webhook className="size-4" /> Webhooks</CardTitle>
        <CardDescription>
          Send bookings, messages and finished calls to Zapier, Make or your own software the moment they happen.
          Each request is signed with the endpoint's secret.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {q.error && <ErrorBanner error={q.error} />}
        {!admin && <p className="text-sm text-muted-foreground">Ask an admin to manage webhooks.</p>}
        {q.data?.endpoints.map((h) => (
          <div key={h.id} className="space-y-1 rounded-lg border px-3 py-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <code className="truncate text-xs">{h.url}</code>
              <span className="flex shrink-0 items-center gap-1">
                <Button size="icon-xs" variant="ghost" aria-label="Send test" disabled={m.test.isPending}
                  onClick={() => m.test.mutate(h.id, {
                    onSuccess: (r) => (r.ok ? toast.success(`Delivered (HTTP ${r.status})`) : toast.error(`Not delivered (HTTP ${r.status ?? 'error'})`)),
                  })}>
                  <Send />
                </Button>
                <Button size="icon-xs" variant="ghost" aria-label="Remove" onClick={() => setRemoving(h)}><Trash2 /></Button>
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
              {h.events.length === 0 ? <Badge variant="secondary">All events</Badge> : h.events.map((e) => <Badge key={e} variant="secondary">{EVENT_LABEL[e] ?? e}</Badge>)}
              {h.last_delivery && (
                <span className="ml-auto">
                  {h.last_delivery.ok ? 'Delivered' : `Failed (HTTP ${h.last_delivery.status ?? 'error'})`} {timeAgo(h.last_delivery.at)}
                </span>
              )}
            </div>
            {secret?.id === h.id && (
              <div className="mt-1 rounded border border-amber-300 bg-amber-50 p-2 text-xs dark:border-amber-800 dark:bg-amber-950">
                <p className="mb-1 font-medium">Signing secret — copy it now, it won't be shown again.</p>
                <div className="flex items-center gap-1">
                  <code className="flex-1 truncate">{secret.secret}</code>
                  <Button size="icon-xs" variant="ghost" aria-label="Copy secret"
                    onClick={() => void navigator.clipboard.writeText(secret.secret).then(() => toast.success('Copied'))}><Copy /></Button>
                </div>
              </div>
            )}
          </div>
        ))}
        {admin && (
          <div className="space-y-2 border-t pt-3">
            <Field label="Add an endpoint" hint="An https:// URL that accepts POST requests with a JSON body.">
              <Input value={url} placeholder="https://hooks.zapier.com/hooks/catch/…" onChange={(e) => setUrl(e.target.value)} />
            </Field>
            <div className="flex flex-wrap gap-3 text-sm">
              {(q.data?.events ?? Object.keys(EVENT_LABEL)).map((e) => (
                <label key={e} className="flex items-center gap-1.5">
                  <Checkbox checked={events.includes(e)} onCheckedChange={() => toggle(e)} /> {EVENT_LABEL[e] ?? e}
                </label>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">Nothing ticked = every event.</p>
            <Button size="sm" variant="outline" disabled={!/^https?:\/\/\S+/.test(url.trim()) || m.create.isPending}
              onClick={() => m.create.mutate({ url: url.trim(), events }, {
                onSuccess: (r) => { setSecret({ id: r.endpoint.id, secret: r.secret }); setUrl(''); setEvents([]); toast.success('Webhook added') },
              })}>
              {m.create.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Add webhook
            </Button>
          </div>
        )}
      </CardContent>
      {removing && (
        <ConfirmDialog
          open
          onOpenChange={(o) => !o && setRemoving(null)}
          title="Remove this webhook?"
          description={`${removing.url} will stop receiving events.`}
          confirmLabel="Remove"
          destructive
          loading={m.remove.isPending}
          onConfirm={() => m.remove.mutate(removing.id, { onSuccess: () => { setRemoving(null); toast.success('Removed') } })}
        />
      )}
    </Card>
  )
}
