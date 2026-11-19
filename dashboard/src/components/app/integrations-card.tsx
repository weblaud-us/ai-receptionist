import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { CalendarDays, Loader2, RefreshCw } from 'lucide-react'
import { ErrorBanner, Field, KeyValue } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { timeAgo } from '@receptionist/shared/lib/format'
import { useTenant } from '@/lib/tenant'
import { useGoogleCalendar, useIntegrations } from '@/lib/tenant-hooks'
import { WebhooksCard } from '@/components/app/webhooks-card'

const CALLBACK_MESSAGES: Record<string, [string, 'success' | 'error']> = {
  connected: ['Google Calendar connected — your busy times now block bookings', 'success'],
  cancelled: ['Google Calendar connection was cancelled', 'error'],
  failed: ['Google refused the connection. Try again.', 'error'],
  invalid_link: ['That connection link expired. Start again.', 'error'],
}

export function IntegrationsPanel() {
  const { can } = useTenant()
  const q = useIntegrations()
  const g = useGoogleCalendar()
  const [params, setParams] = useSearchParams()
  const status = q.data?.google_calendar
  const [calendarId, setCalendarId] = useState('')

  // Back from Google's consent screen: show the result once, then clean the URL.
  const result = params.get('google')
  useEffect(() => {
    if (!result) return
    const [msg, kind] = CALLBACK_MESSAGES[result] ?? ['Calendar connection finished', 'success']
    if (kind === 'success') toast.success(msg)
    else toast.error(msg)
    params.delete('google')
    setParams(params, { replace: true })
  }, [result, params, setParams])

  return (
    <div className="mt-3 grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CalendarDays className="size-4" /> Google Calendar
            {status?.connected ? <Badge className="ml-auto">Connected</Badge> : <Badge variant="outline" className="ml-auto">Not connected</Badge>}
          </CardTitle>
          <CardDescription>Busy times in your calendar block bookings, and every booking your assistant makes appears in it.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {q.error && <ErrorBanner error={q.error} />}
          {status && !status.available && (
            <p className="text-sm text-muted-foreground">Calendar sync isn't enabled on this server yet (GOOGLE_OAUTH_CLIENT_ID).</p>
          )}
          {status?.available && !status.connected && can('admin') && (
            <Button disabled={g.connect.isPending} onClick={() => g.connect.mutate(undefined, { onSuccess: (r) => window.location.assign(r.url) })}>
              {g.connect.isPending && <Loader2 className="animate-spin" />} Connect Google Calendar
            </Button>
          )}
          {status?.connected && (
            <>
              <KeyValue label="Calendar">{status.calendar_id === 'primary' ? 'Your main calendar' : status.calendar_id}</KeyValue>
              <KeyValue label="Last synced">{status.last_sync_at ? timeAgo(status.last_sync_at) : 'Not yet'}</KeyValue>
              {status.last_error && <ErrorBanner error={new Error(`Last sync failed: ${status.last_error}`)} />}
              {can('admin') && (
                <div className="space-y-3 border-t pt-3">
                  <Field label="Use a different calendar" hint="A calendar ID from Google Calendar settings, e.g. bookings@group.calendar.google.com">
                    <div className="flex gap-2">
                      <Input value={calendarId} placeholder="primary" onChange={(e) => setCalendarId(e.target.value)} />
                      <Button variant="outline" disabled={!calendarId.trim() || g.setCalendar.isPending}
                        onClick={() => g.setCalendar.mutate(calendarId.trim(), { onSuccess: () => { setCalendarId(''); toast.success('Calendar changed') } })}>
                        Save
                      </Button>
                    </div>
                  </Field>
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" disabled={g.sync.isPending} onClick={() => g.sync.mutate(undefined, { onSuccess: () => toast.success('Synced') })}>
                      {g.sync.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Sync now
                    </Button>
                    <Button size="sm" variant="ghost" disabled={g.disconnect.isPending}
                      onClick={() => g.disconnect.mutate(undefined, { onSuccess: () => toast.success('Disconnected') })}>
                      Disconnect
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>WhatsApp</CardTitle>
          <CardDescription>Customers can message your assistant on WhatsApp. Connecting your own WhatsApp Business number is done by our team.</CardDescription>
        </CardHeader>
        <CardContent>
          <KeyValue label="Business number ID">{q.data?.whatsapp.phone_number_id ?? 'Not connected'}</KeyValue>
        </CardContent>
      </Card>
      <div className="lg:col-span-2"><WebhooksCard /></div>
    </div>
  )
}
