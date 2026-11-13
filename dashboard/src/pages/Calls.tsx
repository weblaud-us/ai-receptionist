import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Loader2, MessageCircle, Phone, PhoneCall, PhoneOutgoing, Send } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader } from '@/components/app/blocks'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { isValidPhone, normalizePhone } from '@/lib/format'
import { useLiveCalls, useOutboundCall, useSendWhatsApp, useSystem } from '@/lib/hooks'

export default function Calls() {
  const live = useLiveCalls()
  const system = useSystem()
  const integrations = system.data?.integrations

  return (
    <>
      <PageHeader title="Calls & Messages" description="Watch calls as they happen, and reach out to a guest by phone or WhatsApp." />

      <div className="space-y-4">
        <Card className="gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle className="flex items-center gap-2">
              <PhoneCall className="size-4" /> Live calls
              <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-normal text-muted-foreground">{live.data?.total ?? 0}</span>
              {live.isFetching && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
            </CardTitle>
            <CardDescription>Refreshes every 5 seconds. Includes phone calls handled by this server (Twilio).</CardDescription>
          </CardHeader>
          {live.error && (
            <div className="p-4">
              <ErrorBanner error={live.error} onRetry={() => void live.refetch()} />
            </div>
          )}
          {live.data && live.data.calls.length === 0 ? (
            <EmptyState title="No calls in progress" icon={<Phone className="size-8" />} description="Incoming calls will appear here while they're connected." />
          ) : (
            live.data && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Caller</TableHead>
                    <TableHead>Call SID</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Quiet for</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {live.data.calls.map((c) => (
                    <TableRow key={c.call_sid}>
                      <TableCell className="font-medium">{c.phone}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{c.call_sid}</TableCell>
                      <TableCell>
                        <span className="flex items-center gap-1.5 text-sm">
                          <span className={`size-2 rounded-full ${c.ai_speaking ? 'animate-pulse bg-indigo-500' : c.processing ? 'animate-pulse bg-amber-500' : 'bg-emerald-500'}`} />
                          {!c.stream_connected ? 'Connecting' : c.ai_speaking ? 'AI speaking' : c.processing ? 'Thinking' : 'Listening'}
                        </span>
                      </TableCell>
                      <TableCell>{c.seconds_since_speech}s</TableCell>
                      <TableCell className="text-right">
                        <Button size="xs" variant="outline" nativeButton={false} render={<Link to={`/conversations?session=${c.session_id}`} />}>
                          Transcript
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )
          )}
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <OutboundCall enabled={integrations?.twilio_voice !== false} />
          <WhatsAppForm enabled={integrations?.meta_whatsapp !== false} />
        </div>
      </div>
    </>
  )
}

function OutboundCall({ enabled }: { enabled: boolean }) {
  const call = useOutboundCall()
  const [phone, setPhone] = useState('')
  const [message, setMessage] = useState('')
  const to = normalizePhone(phone)
  const ok = isValidPhone(to)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PhoneOutgoing className="size-4" /> Outbound call
        </CardTitle>
        <CardDescription>
          Places a call that reads your message aloud. It is a one-way announcement — the guest cannot talk to the AI on this call.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!enabled && <ErrorBanner error={new Error('Twilio voice is not configured on the server (TWILIO_* settings).')} />}
        <Field label="Phone (with country code)" hint={phone && !ok ? 'Use international format, e.g. +8801712345678' : undefined}>
          <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+8801712345678" aria-invalid={!!phone && !ok} />
        </Field>
        <Field label="Message" hint="Leave empty for the default greeting.">
          <Textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Your booking is confirmed for tomorrow at 2 PM…" />
        </Field>
        <Button
          disabled={!ok || !enabled || call.isPending}
          onClick={() =>
            call.mutate(
              { to_phone: to, message },
              { onSuccess: (r) => toast.success(`Calling ${to}… (${r.call_sid})`) },
            )
          }
        >
          {call.isPending ? <Loader2 className="animate-spin" /> : <PhoneOutgoing />} Place call
        </Button>
      </CardContent>
    </Card>
  )
}

function WhatsAppForm({ enabled }: { enabled: boolean }) {
  const send = useSendWhatsApp()
  const [phone, setPhone] = useState('')
  const [message, setMessage] = useState('')
  const to = normalizePhone(phone)
  const ok = isValidPhone(to)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageCircle className="size-4" /> WhatsApp message
        </CardTitle>
        <CardDescription>Free-form text. WhatsApp only delivers it within 24 hours of the guest's last message to the hotel.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!enabled && <ErrorBanner error={new Error('Meta WhatsApp is not configured on the server (META_WA_* settings).')} />}
        <Field label="Phone (with country code)" hint={phone && !ok ? 'Use international format, e.g. +8801712345678' : undefined}>
          <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+8801712345678" aria-invalid={!!phone && !ok} />
        </Field>
        <Field label="Message">
          <Textarea rows={4} value={message} onChange={(e) => setMessage(e.target.value)} />
        </Field>
        <Button
          disabled={!ok || !message.trim() || !enabled || send.isPending}
          onClick={() =>
            send.mutate(
              { to, message: message.trim() },
              {
                onSuccess: () => {
                  toast.success(`Message sent to ${to}`)
                  setMessage('')
                },
              },
            )
          }
        >
          {send.isPending ? <Loader2 className="animate-spin" /> : <Send />} Send
        </Button>
      </CardContent>
    </Card>
  )
}
