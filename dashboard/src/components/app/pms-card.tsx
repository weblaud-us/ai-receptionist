import { useState } from 'react'
import { toast } from 'sonner'
import { ClipboardPlus, Loader2, RefreshCw } from 'lucide-react'
import { ErrorBanner, Field, KeyValue } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { timeAgo } from '@receptionist/shared/lib/format'
import { useTenant } from '@/lib/tenant'
import { type IntegrationsStatus, type PmsProvider, usePms } from '@/lib/tenant-hooks'

const PROVIDERS: Record<PmsProvider, { name: string; market: string; keyLabel: string; keyHint: string }> = {
  cliniko: {
    name: 'Cliniko',
    market: 'Australia',
    keyLabel: 'Cliniko API key',
    keyHint: 'In Cliniko: My info → Manage API keys → Add an API key. It ends with your region, e.g. -au1.',
  },
  dentally: {
    name: 'Dentally',
    market: 'United Kingdom',
    keyLabel: 'Dentally API token',
    keyHint: 'Ask your Dentally account owner for an API token with appointment and patient access.',
  },
}

/**
 * A dental practice's own diary (PMS). When connected, the assistant books straight into it, so the
 * front desk never re-types a booking. Cliniko is listed first for Australian practices and Dentally
 * first for UK practices.
 */
export function PmsCard({ status, country }: { status: IntegrationsStatus['pms']; country: string }) {
  const { can } = useTenant()
  const pms = usePms()
  const [key, setKey] = useState('')
  const order: PmsProvider[] = country === 'GB' ? ['dentally', 'cliniko'] : ['cliniko', 'dentally']
  const offered = order.filter((p) => status?.available[p])
  const [choice, setChoice] = useState<PmsProvider>(offered[0] ?? 'cliniko')
  if (!status) return null
  const current = status.connected && status.provider !== 'builtin' ? PROVIDERS[status.provider] : null

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ClipboardPlus className="size-4" /> Practice management system
          {current ? <Badge className="ml-auto">{current.name} connected</Badge> : <Badge variant="outline" className="ml-auto">Not connected</Badge>}
        </CardTitle>
        <CardDescription>
          Connect your practice diary and your assistant books straight into it, with real availability, new patients
          created for you and no double entry. If the diary is ever slow, the assistant takes a callback instead.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {status.last_error && <ErrorBanner error={new Error(`Last sync failed: ${status.last_error}`)} />}
        {current ? (
          <>
            <KeyValue label="System">{current.name}</KeyValue>
            {status.account.business_name && <KeyValue label="Practice">{String(status.account.business_name)}</KeyValue>}
            {status.account.site_name && <KeyValue label="Site">{String(status.account.site_name)}</KeyValue>}
            <KeyValue label="Dentists and appointment types synced">
              {status.last_sync_at ? timeAgo(status.last_sync_at) : 'Not yet'}
            </KeyValue>
            {can('admin') && (
              <div className="flex flex-wrap gap-2 border-t pt-3">
                <Button size="sm" variant="outline" disabled={pms.sync.isPending}
                  onClick={() => pms.sync.mutate(undefined, { onSuccess: () => toast.success('Synced from your diary') })}>
                  {pms.sync.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Sync now
                </Button>
                <Button size="sm" variant="outline" disabled={pms.test.isPending}
                  onClick={() => pms.test.mutate(undefined, {
                    onSuccess: () => toast.success('Connection works'),
                    onError: (e) => toast.error(e instanceof Error ? e.message : 'Connection failed'),
                  })}>
                  Test connection
                </Button>
                <Button size="sm" variant="ghost" disabled={pms.disconnect.isPending}
                  onClick={() => pms.disconnect.mutate(undefined, { onSuccess: () => toast.success('Disconnected') })}>
                  Disconnect
                </Button>
              </div>
            )}
          </>
        ) : offered.length === 0 ? (
          <p className="text-sm text-muted-foreground">Practice-system connections aren't enabled on this server yet.</p>
        ) : can('admin') ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {offered.map((p) => (
                <Button key={p} size="sm" variant={choice === p ? 'default' : 'outline'} onClick={() => setChoice(p)}>
                  {PROVIDERS[p].name} <span className="text-xs opacity-70">({PROVIDERS[p].market})</span>
                </Button>
              ))}
            </div>
            <Field label={PROVIDERS[choice].keyLabel} hint={PROVIDERS[choice].keyHint}>
              <div className="flex gap-2">
                <Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
                <Button disabled={key.trim().length < 10 || pms.connect.isPending}
                  onClick={() => pms.connect.mutate({ provider: choice, api_key: key.trim() }, {
                    onSuccess: (r) => {
                      setKey('')
                      toast.success(`Connected: ${r.sync.practitioners} dentists, ${r.sync.types} appointment types`)
                    },
                    onError: (e) => toast.error(e instanceof Error ? e.message : 'Connection failed'),
                  })}>
                  {pms.connect.isPending && <Loader2 className="animate-spin" />} Connect
                </Button>
              </div>
            </Field>
            <p className="text-xs text-muted-foreground">The key is checked with {PROVIDERS[choice].name}, then stored encrypted. It is never shown again.</p>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">An admin can connect your practice system.</p>
        )}
      </CardContent>
    </Card>
  )
}
