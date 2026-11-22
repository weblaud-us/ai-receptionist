import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, Check, CheckCheck, Clock, Loader2, MessageCircle, Send, X } from 'lucide-react'
import { ErrorBanner, Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Input } from '@receptionist/shared/components/ui/input'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { timeAgo } from '@receptionist/shared/lib/format'
import { useCtx } from '@/lib/tenant'
import { zoneDateTime } from '@/lib/tenant-format'
import { useContactWhatsApp, useSendContactWhatsApp, useWhatsAppStatus, useWhatsAppTemplates } from '@/lib/tenant-hooks'
import type { Contact, WhatsAppOutboxItem, WhatsAppTemplate } from '@/lib/tenant-types'

const STATUS: Record<WhatsAppOutboxItem['status'], { label: string; icon: typeof Check; className: string }> = {
  accepted: { label: 'Sending', icon: Clock, className: 'text-muted-foreground' },
  sent: { label: 'Sent', icon: Check, className: 'text-muted-foreground' },
  delivered: { label: 'Delivered', icon: CheckCheck, className: 'text-muted-foreground' },
  read: { label: 'Read', icon: CheckCheck, className: 'text-sky-600' },
  failed: { label: 'Failed', icon: X, className: 'text-destructive' },
}

const tplKey = (t: WhatsAppTemplate) => `${t.name}|${t.language}`

/** WhatsApp a patient from their profile: free text inside the 24-hour window, approved templates outside it. */
export function ContactWhatsApp({ contact }: { contact: Contact }) {
  const tz = useCtx().tenant.timezone
  const status = useWhatsAppStatus()
  const configured = !!status.data?.configured
  const q = useContactWhatsApp(contact.id, configured)

  if (status.isLoading) return null
  if (!configured) {
    return (
      <Section>
        <p className="text-sm text-muted-foreground">WhatsApp isn't connected for your practice yet. Ask your account manager to connect your WhatsApp Business number.</p>
      </Section>
    )
  }
  const data = q.data
  return (
    <Section>
      {q.error && <ErrorBanner error={q.error} onRetry={() => q.refetch()} />}
      {data && (
        <>
          {data.do_not_contact ? (
            <p className="text-sm text-muted-foreground">This patient is marked <strong>do not contact</strong>. Turn that off under Details to message them.</p>
          ) : (
            <>
              <div className={`rounded-md px-3 py-2 text-xs ${data.window.open ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400' : 'bg-muted text-muted-foreground'}`}>
                {data.window.open && data.window.expires_at
                  ? <>They messaged you {timeAgo(data.window.last_inbound_at)} — you can write freely until {zoneDateTime(data.window.expires_at, tz)}.</>
                  : <>They haven't messaged you in the last 24 hours, so WhatsApp only allows an approved template to start the conversation.</>}
              </div>
              {data.window.open ? <FreeText contactId={contact.id} /> : <TemplateForm contact={contact} />}
            </>
          )}
          {data.messages.length > 0 && (
            <div className="space-y-1.5 pt-1">
              <div className="text-xs font-medium text-muted-foreground">Sent from the dashboard</div>
              {data.messages.map((m) => <OutboxRow key={m.id} m={m} />)}
            </div>
          )}
        </>
      )}
      {q.isLoading && <Loader2 className="mx-auto size-4 animate-spin" />}
    </Section>
  )
}

function Section({ children }: { children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold"><MessageCircle className="size-4" /> WhatsApp</h3>
      {children}
    </section>
  )
}

function FreeText({ contactId }: { contactId: string }) {
  const send = useSendContactWhatsApp(contactId)
  const [text, setText] = useState('')
  return (
    <div className="space-y-2">
      <Textarea rows={3} value={text} maxLength={4000} onChange={(e) => setText(e.target.value)} placeholder="Type a message…" />
      {send.error && <ErrorBanner error={send.error} />}
      <Button size="sm" disabled={!text.trim() || send.isPending}
        onClick={() => send.mutate({ text: text.trim() }, { onSuccess: () => { setText(''); toast.success('Sent on WhatsApp') } })}>
        {send.isPending ? <Loader2 className="animate-spin" /> : <Send />} Send
      </Button>
    </div>
  )
}

function TemplateForm({ contact }: { contact: Contact }) {
  const templates = useWhatsAppTemplates(true)
  const send = useSendContactWhatsApp(contact.id)
  const list = useMemo(() => templates.data?.templates ?? [], [templates.data])
  const [key, setKey] = useState('')
  const tpl = list.find((t) => tplKey(t) === key)
  const [params, setParams] = useState<string[]>([])
  const firstName = (contact.name ?? '').split(' ')[0]

  // New template → reset the fields, pre-filling {{1}} with the patient's first name.
  const choose = (k: string) => {
    const t = list.find((x) => tplKey(x) === k)
    setKey(k)
    setParams(t ? Array.from({ length: t.params }, (_, i) => (i === 0 ? firstName : '')) : [])
  }

  if (templates.isLoading) return <Loader2 className="size-4 animate-spin" />
  if (templates.error) return <ErrorBanner error={templates.error} onRetry={() => templates.refetch()} />
  if (list.length === 0) {
    return <p className="text-sm text-muted-foreground">No approved templates yet. Create one in Meta's WhatsApp Manager (e.g. an appointment reminder); once Meta approves it, it appears here.</p>
  }
  const preview = tpl ? tpl.body.replace(/\{\{\s*(\d+)\s*\}\}/g, (m, n) => params[Number(n) - 1] || m) : ''
  return (
    <div className="space-y-2">
      <SimpleSelect value={key} onChange={choose} placeholder="Choose a template" className="w-full"
        options={list.map((t) => ({ value: tplKey(t), label: `${t.name.replace(/_/g, ' ')} (${t.language})` }))} />
      {tpl && (
        <>
          {params.map((p, i) => (
            <Field key={i} label={`Field {{${i + 1}}}`}>
              <Input value={p} onChange={(e) => setParams(params.map((x, j) => (j === i ? e.target.value : x)))} />
            </Field>
          ))}
          <div className="rounded-md border bg-muted/40 p-2.5 text-sm whitespace-pre-line">{preview}</div>
          {send.error && <ErrorBanner error={send.error} />}
          <Button size="sm" disabled={send.isPending || params.some((p) => !p.trim())}
            onClick={() => send.mutate({ template: tpl.name, language: tpl.language, params: params.map((p) => p.trim()) },
              { onSuccess: () => { choose(''); toast.success('Template sent on WhatsApp') } })}>
            {send.isPending ? <Loader2 className="animate-spin" /> : <Send />} Send template
          </Button>
        </>
      )}
    </div>
  )
}

function OutboxRow({ m }: { m: WhatsAppOutboxItem }) {
  const s = STATUS[m.status] ?? STATUS.accepted
  const Icon = s.icon
  return (
    <div className="rounded-md border p-2 text-sm">
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 whitespace-pre-line">{m.body}</p>
        <span className={`flex shrink-0 items-center gap-1 text-xs ${s.className}`}><Icon className="size-3.5" /> {s.label}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        {m.kind === 'template' && <Badge variant="outline" className="font-normal">template</Badge>}
        <span>{timeAgo(m.created_at)}</span>
      </div>
      {m.status === 'failed' && m.error && (
        <p className="mt-1 flex items-start gap-1 text-xs text-destructive"><AlertTriangle className="mt-0.5 size-3 shrink-0" /> {m.error}</p>
      )}
    </div>
  )
}
