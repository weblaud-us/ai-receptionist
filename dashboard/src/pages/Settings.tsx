import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { CheckCircle2, Copy, Loader2, Plus, Save, Trash2, XCircle } from 'lucide-react'
import { ErrorBanner, Field, KeyValue, PageHeader } from '@receptionist/shared/components/blocks'
import { HoursEditor } from '@/components/app/hours-editor'
import { IntegrationsPanel } from '@/components/app/integrations-card'
import { RecallCard } from '@/components/app/recall-card'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { API_BASE } from '@receptionist/shared/lib/api'
import { titleCase } from '@receptionist/shared/lib/format'
import { useSystem } from '@/lib/hooks'
import { useCtx, useTenant } from '@/lib/tenant'
import { CURRENCIES, TIMEZONES } from '@/lib/tenant-format'
import { useAudit, useUpdateSettings } from '@/lib/tenant-hooks'
import type { Tenant } from '@/lib/tenant-types'
import { useDraft } from '@/lib/use-draft'

type Draft = Pick<Tenant, 'name' | 'description' | 'phone' | 'website' | 'address' | 'timezone' | 'currency' | 'hours' |
  'scheduling' | 'policies' | 'notifications' | 'hotel' | 'answering_mode' | 'dental' | 'missed_call_recovery' |
  'legal_entity'>

const DENTAL_DEFAULTS = { accepts_nhs: false, accepts_new_nhs_patients: false, accepts_private_health_funds: false, finance_available: false }
const RECOVERY_DEFAULTS = { enabled: false, template: '', channel: 'sms' as const }
const ENTITY_DEFAULTS = { registered_name: '', abn: '', company_number: '' }

const CHANNELS = [{ key: 'sms', label: 'Text' }, { key: 'email', label: 'E-mail' }, { key: 'whatsapp', label: 'WhatsApp' }]

const ANSWERING_MODES = [
  { value: 'all_calls', label: 'Every call goes to the assistant' },
  { value: 'overflow', label: 'Only calls we can’t pick up (busy / no answer)' },
  { value: 'after_hours', label: 'Only after hours' },
]

export default function SettingsPage() {
  const { can } = useTenant()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'business'
  return (
    <>
      <PageHeader title="Settings" description="Your business details. Your assistant uses them on every call." />
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: String(v) })}>
        <TabsList>
          <TabsTrigger value="business">Business</TabsTrigger>
          <TabsTrigger value="integrations">Integrations</TabsTrigger>
          {can('admin') && <TabsTrigger value="activity">Change history</TabsTrigger>}
          {can('admin') && <TabsTrigger value="system">System</TabsTrigger>}
        </TabsList>
        <TabsContent value="business"><BusinessTab /></TabsContent>
        <TabsContent value="integrations"><IntegrationsPanel /></TabsContent>
        {can('admin') && <TabsContent value="activity"><AuditTab /></TabsContent>}
        {can('admin') && <TabsContent value="system"><SystemTab /></TabsContent>}
      </Tabs>
    </>
  )
}

function DentalCard({ draft, set, country }: { draft: Draft; set: (p: Partial<Draft>) => void; country: string }) {
  const d = draft.dental ?? DENTAL_DEFAULTS
  const r = draft.missed_call_recovery ?? RECOVERY_DEFAULTS
  const e = draft.legal_entity ?? ENTITY_DEFAULTS
  const toggle = (label: string, checked: boolean, onChange: (v: boolean) => void) => (
    <label className="flex items-center gap-2 text-sm"><Switch checked={checked} onCheckedChange={onChange} /> {label}</label>
  )
  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Your practice</CardTitle>
        <CardDescription>How your assistant covers reception, what it tells patients, and how missed callers are won back.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <div className="text-sm font-medium">Reception cover</div>
          {toggle('Overflow mode: answer when reception is busy', draft.answering_mode === 'overflow',
            (v) => set({ answering_mode: v ? 'overflow' : 'all_calls' }))}
          <p className="text-xs text-muted-foreground">
            Forward calls to your assistant after a few rings. Lunch breaks and after-hours calls follow your opening hours.
          </p>
        </div>
        <div className="space-y-2">
          <div className="text-sm font-medium">Payments and funding</div>
          {country === 'GB' && (
            <>
              {toggle('We offer NHS treatment', d.accepts_nhs,
                (v) => set({ dental: { ...d, accepts_nhs: v, accepts_new_nhs_patients: v && d.accepts_new_nhs_patients } }))}
              {d.accepts_nhs && toggle('We are taking new NHS patients', d.accepts_new_nhs_patients,
                (v) => set({ dental: { ...d, accepts_new_nhs_patients: v } }))}
            </>
          )}
          {country === 'AU' && toggle('HICAPS on-the-spot health fund claims', d.accepts_private_health_funds,
            (v) => set({ dental: { ...d, accepts_private_health_funds: v } }))}
          {toggle('Payment plans or treatment finance', d.finance_available, (v) => set({ dental: { ...d, finance_available: v } }))}
        </div>
        <div className="space-y-2 md:col-span-2">
          <div className="text-sm font-medium">Missed-call text</div>
          {toggle('Text callers who hang up before booking (at most once a day, never after a STOP)', r.enabled,
            (v) => set({ missed_call_recovery: { ...r, enabled: v } }))}
          {r.enabled && (
            <Field label="Message"
              hint="Leave empty for the standard text. You can use {business}, {link} (your booking link) and {phone}. The opt-out line is added for you.">
              <Textarea rows={2} maxLength={320} value={r.template}
                onChange={(ev) => set({ missed_call_recovery: { ...r, template: ev.target.value } })} />
            </Field>
          )}
        </div>
        <div className="grid gap-3 sm:grid-cols-2 md:col-span-2">
          <Field label="Registered business name" hint="For your phone number's regulatory registration">
            <Input value={e.registered_name} onChange={(ev) => set({ legal_entity: { ...e, registered_name: ev.target.value } })} />
          </Field>
          {country === 'GB' ? (
            <Field label="Companies House number" hint="8 characters, e.g. 01234567 (empty if you're a sole trader)">
              <Input value={e.company_number} onChange={(ev) => set({ legal_entity: { ...e, company_number: ev.target.value } })} />
            </Field>
          ) : (
            <Field label="ABN" hint="Your 11-digit Australian Business Number">
              <Input value={e.abn} onChange={(ev) => set({ legal_entity: { ...e, abn: ev.target.value } })} />
            </Field>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function BusinessTab() {
  const ctx = useCtx()
  const { can } = useTenant()
  const t = ctx.tenant
  const save = useUpdateSettings()
  const editable = can('admin') && !ctx.legacy
  const { draft, setDraft, dirty, reset } = useDraft<Draft>({
    name: t.name, description: t.description, phone: t.phone, website: t.website, address: t.address, timezone: t.timezone,
    currency: t.currency, hours: t.hours, scheduling: t.scheduling, policies: t.policies, notifications: t.notifications, hotel: t.hotel,
    answering_mode: t.answering_mode ?? 'all_calls',
    ...(ctx.vertical.key === 'dental' ? {
      dental: t.dental ?? DENTAL_DEFAULTS,
      missed_call_recovery: t.missed_call_recovery ?? RECOVERY_DEFAULTS,
      legal_entity: t.legal_entity ?? ENTITY_DEFAULTS,
    } : {}),
  })
  const [newPolicy, setNewPolicy] = useState('')
  if (!draft) return null
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch })
  const n = draft.notifications
  const toggleChannel = (event: 'on_new_booking' | 'on_message', ch: string, on: boolean) =>
    set({ notifications: { ...n, [event]: on ? [...n[event], ch] : n[event].filter((c) => c !== ch) } })

  return (
    <div className="mt-3 space-y-4">
      {ctx.legacy && <ErrorBanner error={new Error('These settings still come from .env. Run migration m0003 to edit them here.')} />}
      {!can('admin') && <p className="text-sm text-muted-foreground">Only owners and admins can change settings.</p>}
      <div className="sticky top-14 z-10 -mx-1 flex justify-end gap-2 bg-background/80 px-1 py-2 backdrop-blur">
        {dirty && <Button variant="outline" onClick={reset}>Discard</Button>}
        <Button disabled={!dirty || !editable || save.isPending} onClick={() => save.mutate(draft, { onSuccess: () => toast.success('Saved') })}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save changes
        </Button>
      </div>
      <fieldset disabled={!editable} className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Business</CardTitle></CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" className="sm:col-span-2"><Input value={draft.name} onChange={(e) => set({ name: e.target.value })} /></Field>
            <Field label="One sentence about you" hint="e.g. Family-run plumbing in north Austin since 1998." className="sm:col-span-2">
              <Input value={draft.description} onChange={(e) => set({ description: e.target.value })} />
            </Field>
            <Field label="Public phone"><Input value={draft.phone} onChange={(e) => set({ phone: e.target.value })} /></Field>
            <Field label="Website"><Input value={draft.website} onChange={(e) => set({ website: e.target.value })} /></Field>
            <Field label="Street address" className="sm:col-span-2">
              <Input value={draft.address.line1} onChange={(e) => set({ address: { ...draft.address, line1: e.target.value } })} />
            </Field>
            <Field label="City"><Input value={draft.address.city} onChange={(e) => set({ address: { ...draft.address, city: e.target.value } })} /></Field>
            <Field label="State / region"><Input value={draft.address.region} onChange={(e) => set({ address: { ...draft.address, region: e.target.value } })} /></Field>
            <Field label="Postcode"><Input value={draft.address.postal} onChange={(e) => set({ address: { ...draft.address, postal: e.target.value } })} /></Field>
            <Field label="Country"><Input value={draft.address.country} onChange={(e) => set({ address: { ...draft.address, country: e.target.value } })} /></Field>
            <Field label="Timezone" hint="Every time your assistant says uses this.">
              <SimpleSelect value={draft.timezone} onChange={(v) => set({ timezone: v })}
                options={[...new Set([draft.timezone, ...TIMEZONES])].map((z) => ({ value: z, label: z }))} />
            </Field>
            <Field label="Currency">
              <SimpleSelect value={draft.currency} onChange={(v) => set({ currency: v })}
                options={[...new Set([draft.currency, ...CURRENCIES])].map((c) => ({ value: c, label: c }))} />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Opening hours</CardTitle>
            <CardDescription>Bookings are only offered inside these hours.</CardDescription>
          </CardHeader>
          <CardContent><HoursEditor value={draft.hours} onChange={(hours) => set({ hours })} disabled={!editable}
            lunchBreak={ctx.vertical.key === 'dental'} /></CardContent>
        </Card>

        {ctx.vertical.key === 'dental' && (
          <DentalCard draft={draft} set={set} country={t.country} />
        )}

        {ctx.vertical.uses_scheduling ? (
          <Card>
            <CardHeader><CardTitle>Booking rules</CardTitle></CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              <Field label="Minimum notice (minutes)" hint="No same-hour surprises">
                <Input type="number" min={0} value={draft.scheduling.min_notice_minutes}
                  onChange={(e) => set({ scheduling: { ...draft.scheduling, min_notice_minutes: Number(e.target.value) } })} />
              </Field>
              <Field label="Book up to (days ahead)">
                <Input type="number" min={1} value={draft.scheduling.max_days_ahead}
                  onChange={(e) => set({ scheduling: { ...draft.scheduling, max_days_ahead: Number(e.target.value) } })} />
              </Field>
              <Field label="Start times every">
                <SimpleSelect value={String(draft.scheduling.slot_step_minutes)}
                  onChange={(v) => set({ scheduling: { ...draft.scheduling, slot_step_minutes: Number(v) } })}
                  options={['5', '10', '15', '20', '30', '60'].map((v) => ({ value: v, label: `${v} minutes` }))} />
              </Field>
              {ctx.vertical.key === 'restaurant' && (
                <Field label="Largest party the assistant books">
                  <Input type="number" min={1} value={draft.scheduling.max_party_size}
                    onChange={(e) => set({ scheduling: { ...draft.scheduling, max_party_size: Number(e.target.value) } })} />
                </Field>
              )}
              <Field label="Cancellation policy" hint="Read to callers who cancel." className="sm:col-span-2">
                <Textarea rows={2} value={draft.scheduling.cancellation_policy}
                  onChange={(e) => set({ scheduling: { ...draft.scheduling, cancellation_policy: e.target.value } })} />
              </Field>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader><CardTitle>Stays</CardTitle></CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              <Field label="Check-in from"><Input type="time" value={draft.hotel.checkin_time} onChange={(e) => set({ hotel: { ...draft.hotel, checkin_time: e.target.value } })} /></Field>
              <Field label="Check-out by"><Input type="time" value={draft.hotel.checkout_time} onChange={(e) => set({ hotel: { ...draft.hotel, checkout_time: e.target.value } })} /></Field>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Policies</CardTitle>
            <CardDescription>Short facts callers often ask about — parking, payment, pets…</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {Object.entries(draft.policies).map(([k, v]) => (
              <div key={k} className="flex items-start gap-2">
                <span className="w-28 shrink-0 pt-1.5 text-sm font-medium">{titleCase(k)}</span>
                <Input value={v} onChange={(e) => set({ policies: { ...draft.policies, [k]: e.target.value } })} />
                <Button size="icon-sm" variant="ghost" aria-label="Remove"
                  onClick={() => { const next = { ...draft.policies }; delete next[k]; set({ policies: next }) }}><Trash2 /></Button>
              </div>
            ))}
            <div className="flex gap-2">
              <Input placeholder="New topic, e.g. parking" value={newPolicy} onChange={(e) => setNewPolicy(e.target.value)} />
              <Button size="sm" variant="outline" disabled={!newPolicy.trim()}
                onClick={() => { set({ policies: { ...draft.policies, [newPolicy.trim().toLowerCase().replace(/\s+/g, '_')]: '' } }); setNewPolicy('') }}>
                <Plus /> Add
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Notifications to you</CardTitle>
            <CardDescription>How we tell you about new bookings and messages. Urgent messages always use every channel you've set up.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-3">
              <Field label="Your mobile (texts)"><Input value={n.owner_phone} placeholder="+1…" onChange={(e) => set({ notifications: { ...n, owner_phone: e.target.value } })} /></Field>
              <Field label="Your e-mail"><Input value={n.owner_email} onChange={(e) => set({ notifications: { ...n, owner_email: e.target.value } })} /></Field>
              <Field label="WhatsApp numbers" hint="Comma-separated"><Input value={n.owner_whatsapp} onChange={(e) => set({ notifications: { ...n, owner_whatsapp: e.target.value } })} /></Field>
            </div>
            <div className="space-y-3 text-sm">
              {([['on_new_booking', 'New booking'], ['on_message', 'Message or job request']] as const).map(([event, label]) => (
                <div key={event}>
                  <div className="mb-1 font-medium">{label}</div>
                  <div className="flex gap-4">
                    {CHANNELS.map((c) => (
                      <label key={c.key} className="flex items-center gap-2">
                        <Checkbox checked={n[event].includes(c.key)} onCheckedChange={(on) => toggleChannel(event, c.key, !!on)} /> {c.label}
                      </label>
                    ))}
                  </div>
                </div>
              ))}
              <label className="flex items-center gap-2">
                <Switch checked={n.daily_digest} onCheckedChange={(v) => set({ notifications: { ...n, daily_digest: v } })} /> Daily summary e-mail
              </label>
              <label className="flex items-center gap-2">
                <Switch checked={n.monthly_report ?? true} onCheckedChange={(v) => set({ notifications: { ...n, monthly_report: v } })} /> Monthly results e-mail
              </label>
              <Field label="Which calls reach your assistant?" hint="Used to count the calls it caught that you would have missed (Results).">
                <SimpleSelect value={draft.answering_mode ?? 'all_calls'} options={ANSWERING_MODES}
                  onChange={(v) => set({ answering_mode: v as Draft['answering_mode'] })} />
              </Field>
            </div>
          </CardContent>
        </Card>
      </fieldset>
      {ctx.vertical.key === 'dental' && !ctx.legacy && <div className="grid gap-4 lg:grid-cols-2"><RecallCard /></div>}
    </div>
  )
}

function AuditTab() {
  const q = useAudit()
  return (
    <Card className="mt-3">
      <CardContent className="space-y-1">
        {q.isLoading && <Skeleton className="h-40" />}
        {q.data?.entries.length === 0 && <p className="text-sm text-muted-foreground">No changes recorded yet.</p>}
        {q.data?.entries.map((e) => (
          <div key={e.id} className="flex flex-wrap justify-between gap-2 border-b py-1.5 text-sm">
            <span><span className="font-medium">{e.action}</span> <span className="text-muted-foreground">{Object.keys(e.diff ?? {}).join(', ')}</span></span>
            <span className="text-xs text-muted-foreground">{new Date(e.at).toLocaleString()} · {e.user_id}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function copy(text: string) {
  void navigator.clipboard.writeText(text).then(() => toast.success('Copied'))
}

function SystemTab() {
  const q = useSystem()
  const s = q.data
  return (
    <div className="mt-3 grid gap-4 lg:grid-cols-2">
      {q.error && <ErrorBanner error={q.error} />}
      {s && (
        <>
          <Card>
            <CardHeader><CardTitle>Server</CardTitle></CardHeader>
            <CardContent>
              <KeyValue label="Dashboard → API">{API_BASE}</KeyValue>
              <KeyValue label="Environment">{s.environment.app_env}</KeyValue>
              <KeyValue label="Debug mode">{s.environment.debug ? <Badge variant="destructive">On</Badge> : 'Off'}</KeyValue>
              <KeyValue label="Database"><Status ok={s.database.connected} yes="Connected" no="Disconnected" /></KeyValue>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Integrations</CardTitle><CardDescription>Whether credentials are present (values are never shown).</CardDescription></CardHeader>
            <CardContent>
              {Object.entries(s.integrations).map(([k, ok]) => (
                <KeyValue key={k} label={titleCase(k)}><Status ok={ok} yes="Configured" no="Missing" /></KeyValue>
              ))}
            </CardContent>
          </Card>
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle>Webhook URLs</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              {Object.entries(s.webhooks).map(([k, url]) => (
                <div key={k} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm">
                  <div className="min-w-0"><div className="text-xs text-muted-foreground">{titleCase(k)}</div><div className="truncate font-mono">{url}</div></div>
                  <Button size="icon-sm" variant="ghost" aria-label="Copy" onClick={() => copy(url)}><Copy /></Button>
                </div>
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}

function Status({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 text-emerald-600"><CheckCircle2 className="size-4" /> {yes}</span>
  ) : (
    <span className="inline-flex items-center gap-1 text-rose-600"><XCircle className="size-4" /> {no}</span>
  )
}
