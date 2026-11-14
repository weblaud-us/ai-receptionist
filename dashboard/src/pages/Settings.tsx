import { toast } from 'sonner'
import { CheckCircle2, Copy, XCircle } from 'lucide-react'
import { ErrorBanner, KeyValue, PageHeader } from '@/components/app/blocks'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { API_BASE } from '@/lib/api'
import { titleCase } from '@/lib/format'
import { useSystem } from '@/lib/hooks'

const INTEGRATION_LABELS: Record<string, string> = {
  openai: 'OpenAI (LLM + speech-to-text)',
  elevenlabs: 'ElevenLabs (phone voice)',
  twilio_voice: 'Twilio voice',
  meta_whatsapp: 'Meta WhatsApp Cloud API',
  meta_webhook_verify_token: 'WhatsApp webhook verify token',
  meta_webhook_signature: 'WhatsApp webhook signature check (app secret)',
  twilio_signature_check: 'Twilio webhook signature check',
  livekit: 'LiveKit (browser calls)',
  public_base_url: 'Public base URL (webhooks, links)',
}

const FEATURE_LABELS: Record<string, string> = {
  one_day_reminder: '1-day booking reminder',
  whatsapp: 'WhatsApp',
  voice_calls: 'Voice calls',
  call_recording: 'Call recording',
  livekit_realtime: 'LiveKit realtime model',
}

const WEBHOOK_LABELS: Record<string, string> = {
  twilio_voice: 'Twilio — voice webhook (POST)',
  twilio_status: 'Twilio — status callback (POST)',
  meta_whatsapp: 'Meta — WhatsApp webhook (GET/POST)',
}

function copy(text: string) {
  void navigator.clipboard.writeText(text).then(() => toast.success('Copied'))
}

export default function SettingsPage() {
  const q = useSystem()
  const s = q.data

  return (
    <>
      <PageHeader
        title="Settings"
        description="Read-only view of how the server is configured. Change values in the backend's .env file and restart it."
      />
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.isLoading && <Skeleton className="h-96" />}

      {s && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Hotel profile</CardTitle>
              <CardDescription>Used in every greeting, quote and confirmation.</CardDescription>
            </CardHeader>
            <CardContent>
              <KeyValue label="Name">{s.hotel.name}</KeyValue>
              <KeyValue label="Receptionist">{s.hotel.receptionist_name}</KeyValue>
              <KeyValue label="Phone">{s.hotel.phone}</KeyValue>
              <KeyValue label="Address">{s.hotel.address}</KeyValue>
              <KeyValue label="Check-in">{s.hotel.checkin_time}</KeyValue>
              <KeyValue label="Check-out">{s.hotel.checkout_time}</KeyValue>
              <KeyValue label="Currency">{s.hotel.currency}</KeyValue>
              <KeyValue label="Timezone">{s.hotel.timezone}</KeyValue>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Server</CardTitle>
              <CardDescription>Connection and environment.</CardDescription>
            </CardHeader>
            <CardContent>
              <KeyValue label="Dashboard → API">{API_BASE}</KeyValue>
              <KeyValue label="Environment">{s.environment.app_env}</KeyValue>
              <KeyValue label="Debug mode">
                {s.environment.debug ? <Badge variant="destructive">On — turn off in production</Badge> : 'Off'}
              </KeyValue>
              <KeyValue label="AI model">{s.environment.llm_model}</KeyValue>
              <KeyValue label="Database">
                <Status ok={s.database.connected} yes={`Connected · ${s.environment.db_name}`} no="Disconnected" />
              </KeyValue>
              <KeyValue label="Conversation memory">
                {s.memory.days} days · {s.memory.max_messages} messages
              </KeyValue>
              {Object.entries(s.collections).map(([k, v]) => (
                <KeyValue key={k} label={`${titleCase(k)} stored`}>
                  {v.toLocaleString()}
                </KeyValue>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Integrations</CardTitle>
              <CardDescription>Whether credentials are present (values are never shown).</CardDescription>
            </CardHeader>
            <CardContent>
              {Object.entries(s.integrations).map(([k, ok]) => (
                <KeyValue key={k} label={INTEGRATION_LABELS[k] ?? titleCase(k)}>
                  <Status ok={ok} yes="Configured" no="Missing" />
                </KeyValue>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Feature flags</CardTitle>
            </CardHeader>
            <CardContent>
              {Object.entries(s.features).map(([k, on]) => (
                <KeyValue key={k} label={FEATURE_LABELS[k] ?? titleCase(k)}>
                  <Badge variant={on ? 'default' : 'outline'}>{on ? 'On' : 'Off'}</Badge>
                </KeyValue>
              ))}
            </CardContent>
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Webhook URLs</CardTitle>
              <CardDescription>Paste these into the Twilio and Meta developer consoles.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {Object.entries(s.webhooks).map(([k, url]) => (
                <div key={k} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground">{WEBHOOK_LABELS[k] ?? k}</div>
                    <div className="truncate font-mono">{url.startsWith('/') ? `(set WEBHOOK_BASE_URL) ${url}` : url}</div>
                  </div>
                  <Button size="icon-sm" variant="ghost" aria-label="Copy" disabled={url.startsWith('/')} onClick={() => copy(url)}>
                    <Copy />
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
    </>
  )
}

function Status({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 text-emerald-600">
      <CheckCircle2 className="size-4" /> {yes}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-rose-600">
      <XCircle className="size-4" /> {no}
    </span>
  )
}
