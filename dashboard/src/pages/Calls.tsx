import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Check, History, Inbox, Loader2, MessageCircle, Phone, PhoneCall, PhoneOutgoing, RotateCcw, Send } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader, Pagination } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { dateTime, duration, isValidPhone, normalizePhone, timeAgo, titleCase } from '@/lib/format'
import { useCtx } from '@/lib/tenant'
import { OUTCOME_LABEL, zoneDateTime } from '@/lib/tenant-format'
import { useRecordingUrl, useTenantCall, useTenantCalls } from '@/lib/tenant-hooks'
import { OutcomeBadge } from './TenantOverview'
import {
  useCallerMessages,
  useLiveCalls,
  useOutboundCall,
  useSendWhatsApp,
  useSystem,
  useUpdateMessage,
} from '@/lib/hooks'

export default function Calls() {
  const live = useLiveCalls()
  const system = useSystem()
  const integrations = system.data?.integrations

  return (
    <>
      <PageHeader title="Calls & Messages" description="Watch calls as they happen, read transcripts, and reply to messages callers left." />

      <div className="space-y-4">
        <Card className="gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle className="flex items-center gap-2">
              <PhoneCall className="size-4" /> Live calls
              <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-normal text-muted-foreground">{live.data?.total ?? 0}</span>
              {live.isFetching && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
            </CardTitle>
            <CardDescription>Refreshes every 5 seconds. Phone and browser calls handled by the AI receptionist.</CardDescription>
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
                        {c.source !== 'livekit' && (
                          <Button size="xs" variant="outline" nativeButton={false} render={<Link to={`/conversations?session=${c.session_id}`} />}>
                            Transcript
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )
          )}
        </Card>

        <CallerMessages />
        <RecentCalls />

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

function CallerMessages() {
  const messages = useCallerMessages()
  const update = useUpdateMessage()

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b py-4">
        <CardTitle className="flex items-center gap-2">
          <Inbox className="size-4" /> Messages from callers
          {!!messages.data?.new && (
            <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400">
              {messages.data.new} new
            </span>
          )}
        </CardTitle>
        <CardDescription>Left with the AI when no one could take the call. Call back, then mark as handled.</CardDescription>
      </CardHeader>
      {messages.error && (
        <div className="p-4">
          <ErrorBanner error={messages.error} onRetry={() => void messages.refetch()} />
        </div>
      )}
      {messages.data && messages.data.messages.length === 0 ? (
        <EmptyState title="No messages" icon={<Inbox className="size-8" />} description="Messages callers leave will appear here." />
      ) : (
        messages.data && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>From</TableHead>
                <TableHead>Call back</TableHead>
                <TableHead>Message</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {messages.data.messages.map((m) => (
                <TableRow key={m.message_id} className={m.status === 'handled' ? 'opacity-60' : undefined}>
                  <TableCell className="whitespace-nowrap text-muted-foreground" title={dateTime(m.created_at)}>
                    {timeAgo(m.created_at)}
                  </TableCell>
                  <TableCell className="font-medium">
                    {m.caller_name}
                    {m.urgent && (
                      <span className="ml-2 rounded bg-red-500/15 px-1.5 py-0.5 text-xs text-red-700 dark:text-red-400">Urgent</span>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{m.callback_number}</TableCell>
                  <TableCell className="max-w-md whitespace-normal">{m.message}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={update.isPending}
                      onClick={() => update.mutate({ id: m.message_id, status: m.status === 'new' ? 'handled' : 'new' })}
                    >
                      {m.status === 'new' ? (
                        <>
                          <Check /> Handled
                        </>
                      ) : (
                        <>
                          <RotateCcw /> Reopen
                        </>
                      )}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )
      )}
    </Card>
  )
}

function seconds(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(2)} s`
}

function RecentCalls() {
  const ctx = useCtx()
  const tz = ctx.tenant.timezone
  const [params, setParams] = useSearchParams()
  const [page, setPage] = useState(1)
  const [outcome, setOutcome] = useState('all')
  const calls = useTenantCalls({ page, limit: 20, outcome: outcome === 'all' ? undefined : outcome })
  const openId = params.get('call')

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b py-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2">
              <History className="size-4" /> Call history
            </CardTitle>
            <CardDescription>
              Click a call for its transcript. Response time is how long callers wait after they stop talking (target: under 1 second).
            </CardDescription>
          </div>
          <SimpleSelect size="sm" value={outcome} onChange={(v) => { setOutcome(v); setPage(1) }}
            options={[{ value: 'all', label: 'All outcomes' }, ...Object.entries(OUTCOME_LABEL).map(([value, label]) => ({ value, label }))]} />
        </div>
      </CardHeader>
      {calls.error && (
        <div className="p-4">
          <ErrorBanner error={calls.error} onRetry={() => void calls.refetch()} />
        </div>
      )}
      {calls.data && calls.data.calls.length === 0 ? (
        <EmptyState title="No calls yet" icon={<History className="size-8" />} description="Calls handled by your assistant will appear here." />
      ) : (
        calls.data && (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Started</TableHead>
                  <TableHead>Caller</TableHead>
                  <TableHead>Outcome</TableHead>
                  <TableHead>Length</TableHead>
                  <TableHead>Response (median)</TableHead>
                  <TableHead>With lookup</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {calls.data.calls.map((c) => (
                  <TableRow key={c.call_id} className="cursor-pointer" onClick={() => setParams({ call: c.call_id })}>
                    <TableCell className="whitespace-nowrap">{zoneDateTime(c.started_at, tz)}</TableCell>
                    <TableCell className="font-medium">
                      {c.caller_phone ?? (c.channel === 'browser' ? 'Browser call' : 'Unknown')}
                    </TableCell>
                    <TableCell>{c.status === 'in_progress' ? 'In progress' : <OutcomeBadge outcome={c.outcome} />}</TableCell>
                    <TableCell>{c.duration_s != null ? duration(c.duration_s) : '—'}</TableCell>
                    <TableCell>{seconds(c.latency?.p50_s)}</TableCell>
                    <TableCell>{seconds(c.latency?.tool_p50_s)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pagination page={page} pages={calls.data.pages} total={calls.data.total} onPage={setPage} label="calls" />
          </>
        )
      )}
      <CallSheet id={openId} onClose={() => setParams({})} />
    </Card>
  )
}

function RecordingPlayer({ callId }: { callId: string }) {
  const rec = useRecordingUrl()
  if (rec.data) return <audio controls autoPlay src={rec.data.url} className="w-full" />
  return (
    <div className="space-y-1">
      <Button size="sm" variant="outline" disabled={rec.isPending} onClick={() => rec.mutate(callId)}>
        {rec.isPending ? <Loader2 className="animate-spin" /> : <PhoneCall />} Play recording
      </Button>
      {rec.error && <ErrorBanner error={rec.error} />}
    </div>
  )
}

function CallSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const tz = useCtx().tenant.timezone
  const q = useTenantCall(id)
  const c = q.data
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{c?.caller_phone ?? (c?.channel === 'browser' ? 'Browser call' : 'Call')}</SheetTitle>
          <SheetDescription>{c ? `${zoneDateTime(c.started_at, tz)} · ${duration(c.duration_s)}` : ''}</SheetDescription>
        </SheetHeader>
        {q.isLoading && <Loader2 className="mx-auto size-5 animate-spin" />}
        {q.error && <div className="px-4"><ErrorBanner error={q.error} /></div>}
        {c && (
          <div className="space-y-4 px-4 pb-6">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <OutcomeBadge outcome={c.outcome} />
              {c.latency?.p50_s != null && <span className="text-muted-foreground">Median response {seconds(c.latency.p50_s)}</span>}
              {!!c.tools_used?.length && <span className="text-muted-foreground">· used {[...new Set(c.tools_used)].map(titleCase).join(', ')}</span>}
            </div>
            {c.summary && <p className="rounded-lg bg-muted p-3 text-sm">{c.summary}</p>}
            {c.recording?.path && <RecordingPlayer callId={c.call_id} />}
            <div className="space-y-2">
              {(c.transcript ?? []).length === 0 && <p className="text-sm text-muted-foreground">No transcript.</p>}
              {(c.transcript ?? []).map((t, i) => (
                <div key={i} className={`flex ${t.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${t.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}>
                    {t.text}
                    {t.interrupted && <span className="ml-1 text-xs opacity-70">(interrupted)</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
