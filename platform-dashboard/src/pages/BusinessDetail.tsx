import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { ArrowLeft, Copy, ExternalLink, KeyRound, Loader2, Pause, Play, Plus, Trash2, UserPlus, XCircle } from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, KeyValue } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@receptionist/shared/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { COUNTRIES, CURRENCIES, TIMEZONES, dateOnly, dateTime, duration, timeAgo, titleCase } from '@receptionist/shared/lib/format'
import { impersonateUrl } from '@receptionist/shared/lib/urls'
import { AddNumberDialog } from '@/components/add-number-dialog'
import { OrderProgress } from '@/components/number-chooser'
import {
  useNumberOrderMutations,
  usePlatformMutations,
  usePlatformTenant,
  useTenantTeamMutations,
  useVerticals,
  type AuditEntry,
  type Role,
  type TenantDetails,
  type TenantDoc,
} from '@/lib/platform'

const STATUSES = ['trial', 'active', 'past_due', 'suspended', 'cancelled']
const ROLES: Role[] = ['owner', 'admin', 'staff']
const ANSWERING = [
  { value: 'all_calls', label: 'Every call' },
  { value: 'overflow', label: 'When staff are busy' },
  { value: 'after_hours', label: 'After hours only' },
]

function statusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'active') return 'default'
  if (status === 'past_due' || status === 'suspended') return 'destructive'
  if (status === 'cancelled') return 'outline'
  return 'secondary'
}

const copy = (text: string, what = 'Copied') => void navigator.clipboard.writeText(text).then(() => toast.success(what))

export default function BusinessDetail() {
  const { id } = useParams<{ id: string }>()
  const q = usePlatformTenant(id)
  const t = q.data?.tenant

  return (
    <>
      <Button size="sm" variant="ghost" className="-ml-2 mb-3" nativeButton={false} render={<Link to="/" />}>
        <ArrowLeft /> Businesses
      </Button>
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.isLoading || !t || !q.data ? (
        !q.error && (
          <div className="space-y-4">
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        )
      ) : (
        <>
          <Header tenant={t} />
          <StatStrip data={q.data} />
          <Tabs defaultValue="overview" className="mt-6">
            <TabsList variant="line" className="mb-2 flex-wrap">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="controls">Controls</TabsTrigger>
              <TabsTrigger value="team">Team ({q.data.members.length})</TabsTrigger>
              <TabsTrigger value="numbers">Numbers ({q.data.numbers.filter((n) => n.status !== 'released').length})</TabsTrigger>
              <TabsTrigger value="activity">Activity</TabsTrigger>
            </TabsList>
            <TabsContent value="overview"><OverviewTab data={q.data} /></TabsContent>
            <TabsContent value="controls"><ControlsTab tenant={t} /></TabsContent>
            <TabsContent value="team"><TeamTab data={q.data} /></TabsContent>
            <TabsContent value="numbers"><NumbersTab data={q.data} /></TabsContent>
            <TabsContent value="activity"><ActivityTab data={q.data} /></TabsContent>
          </Tabs>
        </>
      )}
    </>
  )
}

/* ───────────── header + stats ───────────── */

function Header({ tenant: t }: { tenant: TenantDoc }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{t.name}</h1>
          <Badge variant={statusVariant(t.status)}>{titleCase(t.status)}</Badge>
          {t.pilot?.active && <Badge variant="outline">Pilot</Badge>}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {titleCase(t.vertical)} · {t.timezone} · {t.country} · region {t.region.toUpperCase()}
          {t.created_at && <> · created {dateOnly(t.created_at)}</>}
        </p>
        <button type="button" className="mt-1 inline-flex items-center gap-1 font-mono text-xs text-muted-foreground hover:text-foreground"
          onClick={() => copy(t.id, 'Business ID copied')} title="Copy business ID">
          {t.id} <Copy className="size-3" />
        </button>
      </div>
      <Button onClick={() => window.open(impersonateUrl(t.id), '_blank', 'noopener')}>
        Open as owner <ExternalLink />
      </Button>
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <Card className="gap-1 px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </Card>
  )
}

function StatStrip({ data }: { data: TenantDetails }) {
  const { stats: s, tenant: t } = data
  const included = t.limits?.included_minutes
  const pct = included ? Math.round((s.minutes_30d / included) * 100) : null
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Stat label="Calls (30 d)" value={s.calls_30d} hint={`${s.calls_total} all time`} />
      <Stat label="Minutes (30 d)" value={s.minutes_30d} hint={included ? `of ${included} · ${pct}%` : undefined} />
      <Stat label="Contacts" value={s.contacts} />
      <Stat label="Upcoming appts" value={s.appointments_upcoming} />
      <Stat label="Leads" value={s.leads} hint={`${s.messages} messages`} />
      <Stat label="Knowledge" value={s.knowledge} hint={`${s.services} services`} />
    </div>
  )
}

/* ───────────── overview ───────────── */

function OverviewTab({ data }: { data: TenantDetails }) {
  const t = data.tenant
  const b = t.billing
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Profile</CardTitle></CardHeader>
          <CardContent className="divide-y">
            <KeyValue label="Slug">{t.slug}</KeyValue>
            <KeyValue label="Public phone">{t.phone || '—'}</KeyValue>
            <KeyValue label="Website">{t.website || '—'}</KeyValue>
            <KeyValue label="Locale · currency">{t.locale} · {t.currency}</KeyValue>
            <KeyValue label="Assistant">{t.agent?.name || '—'}</KeyValue>
            <KeyValue label="Answers">{ANSWERING.find((a) => a.value === t.answering_mode)?.label ?? t.answering_mode}</KeyValue>
            <KeyValue label="Transfer line">{t.transfer?.number || 'Off'}</KeyValue>
            <KeyValue label="Onboarding">{t.onboarding?.completed_at ? `Done ${dateOnly(t.onboarding.completed_at)}` : 'Not finished'}</KeyValue>
            <KeyValue label="DPA accepted">{t.legal?.dpa_accepted_at ? `${t.legal.dpa_version} · ${dateOnly(t.legal.dpa_accepted_at)}` : 'No'}</KeyValue>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Plan & billing</CardTitle></CardHeader>
          <CardContent className="divide-y">
            <KeyValue label="Plan">{titleCase(b?.plan)} · {b?.interval}ly</KeyValue>
            <KeyValue label="Subscription">{b?.subscription_status ? titleCase(b.subscription_status) : 'None'}</KeyValue>
            <KeyValue label="Trial ends">{dateOnly(b?.trial_ends_at)}</KeyValue>
            <KeyValue label="Period ends">{dateOnly(b?.period_end)}</KeyValue>
            <KeyValue label="Usage cap">{b?.hard_cap ? 'Stops at 100 %' : 'Overage allowed'}</KeyValue>
            <KeyValue label="Stripe customer">
              {b?.stripe_customer_id ? (
                <button type="button" className="inline-flex items-center gap-1 font-mono text-xs" onClick={() => copy(b.stripe_customer_id)}>
                  {b.stripe_customer_id} <Copy className="size-3" />
                </button>
              ) : '—'}
            </KeyValue>
            {b?.cancelled_at && <KeyValue label="Cancelled">{dateOnly(b.cancelled_at)} · data deleted {dateOnly(b.delete_after)}</KeyValue>}
          </CardContent>
        </Card>
      </div>
      <Card className="gap-0 py-0">
        <CardHeader className="py-4"><CardTitle>Recent calls</CardTitle></CardHeader>
        {data.recent_calls.length === 0 ? (
          <EmptyState title="No calls yet" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>When</TableHead><TableHead>Caller</TableHead><TableHead>Length</TableHead><TableHead>Outcome</TableHead><TableHead>Summary</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {data.recent_calls.map((c) => (
                <TableRow key={c.call_id}>
                  <TableCell className="whitespace-nowrap">{dateTime(c.started_at)}</TableCell>
                  <TableCell className="font-mono text-xs">{c.caller_phone || '—'}{c.direction === 'outbound' && ' ↗'}</TableCell>
                  <TableCell className="tabular-nums">{duration(c.duration_s)}</TableCell>
                  <TableCell>{c.status === 'in_progress' ? <Badge variant="secondary">Live</Badge> : titleCase(c.outcome)}{c.after_hours && <span className="ml-1 text-xs text-muted-foreground">after hours</span>}</TableCell>
                  <TableCell className="max-w-md truncate text-muted-foreground" title={c.summary}>{c.summary || '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  )
}

/* ───────────── controls ───────────── */

type DraftValue = string | boolean
type Draft = Record<string, DraftValue>

/**
 * A card with its own draft and Save button that sends only what changed. Keyed on the saved values,
 * so a refetch after saving resets the draft to what the server now holds.
 */
function FormCard<T extends Draft>(props: {
  title: string
  description?: string
  initial: T
  toPatch: (changed: Partial<T>) => Record<string, unknown>
  children: (v: T, set: (patch: Partial<T>) => void) => ReactNode
}) {
  return <FormCardInner key={JSON.stringify(props.initial)} {...props} />
}

function FormCardInner<T extends Draft>({ title, description, initial, toPatch, children }: Parameters<typeof FormCard<T>>[0]) {
  const { id } = useParams<{ id: string }>()
  const m = usePlatformMutations()
  const [v, setV] = useState<T>(initial)
  const changed = Object.fromEntries(Object.entries(v).filter(([k, val]) => initial[k] !== val)) as Partial<T>
  const dirty = Object.keys(changed).length > 0
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-4">
        {children(v, (patch) => setV({ ...v, ...patch }))}
        <div className="flex justify-end gap-2">
          {dirty && <Button variant="ghost" onClick={() => setV(initial)}>Discard</Button>}
          <Button disabled={!dirty || m.update.isPending}
            onClick={() => m.update.mutate({ id: id!, ...toPatch(changed) }, { onSuccess: () => toast.success(`${title} saved`) })}>
            {m.update.isPending && <Loader2 className="animate-spin" />} Save
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

const int = (s: DraftValue | undefined) => Number.parseInt(String(s), 10)

function ControlsTab({ tenant: t }: { tenant: TenantDoc }) {
  const verticals = useVerticals()
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <FormCard title="Account"
        description="Suspended businesses can still sign in to pay or export, but calls aren't answered."
        initial={{ status: t.status, answering_mode: t.answering_mode, region: t.region }}
        toPatch={(c) => c}>
        {(v, set) => (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Status">
              <SimpleSelect value={v.status} onChange={(s) => set({ status: s })} options={STATUSES.map((s) => ({ value: s, label: titleCase(s) }))} />
            </Field>
            <Field label="Region">
              <SimpleSelect value={v.region} onChange={(s) => set({ region: s as TenantDoc['region'] })}
                options={[{ value: 'us', label: 'US' }, { value: 'eu', label: 'EU' }, { value: 'me', label: 'Middle East' }]} />
            </Field>
            <Field label="Assistant answers" className="sm:col-span-2">
              <SimpleSelect value={v.answering_mode} onChange={(s) => set({ answering_mode: s as TenantDoc['answering_mode'] })} options={ANSWERING} />
            </Field>
          </div>
        )}
      </FormCard>

      <FormCard title="Limits"
        description="0 means unlimited for users and numbers. Platform changes skip the plan rules."
        initial={{
          included_minutes: String(t.limits?.included_minutes ?? 500),
          max_concurrent_calls: String(t.limits?.max_concurrent_calls ?? 3),
          max_users: String(t.limits?.max_users ?? 0),
          max_numbers: String(t.limits?.max_numbers ?? 0),
          max_call_minutes: String(t.agent?.max_call_minutes ?? 15),
          hard_cap: Boolean(t.billing?.hard_cap),
        }}
        toPatch={(c) => {
          const out: Record<string, unknown> = {}
          const limits: Record<string, number> = {}
          for (const k of ['included_minutes', 'max_concurrent_calls', 'max_users', 'max_numbers'] as const) {
            if (k in c) limits[k] = int(c[k])
          }
          if (Object.keys(limits).length) out.limits = limits
          if ('max_call_minutes' in c) out.agent = { max_call_minutes: int(c.max_call_minutes) }
          if ('hard_cap' in c) out.billing = { hard_cap: c.hard_cap }
          return out
        }}>
        {(v, set) => (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Included minutes / period"><Input type="number" min={0} value={v.included_minutes} onChange={(e) => set({ included_minutes: e.target.value })} /></Field>
            <Field label="Max calls at once" hint="1–500"><Input type="number" min={1} max={500} value={v.max_concurrent_calls} onChange={(e) => set({ max_concurrent_calls: e.target.value })} /></Field>
            <Field label="Max dashboard users"><Input type="number" min={0} value={v.max_users} onChange={(e) => set({ max_users: e.target.value })} /></Field>
            <Field label="Max phone numbers"><Input type="number" min={0} value={v.max_numbers} onChange={(e) => set({ max_numbers: e.target.value })} /></Field>
            <Field label="Max call length (min)" hint="1–120"><Input type="number" min={1} max={120} value={v.max_call_minutes} onChange={(e) => set({ max_call_minutes: e.target.value })} /></Field>
            <label className="flex items-center gap-3 self-end pb-2 text-sm">
              <Switch checked={v.hard_cap} onCheckedChange={(c) => set({ hard_cap: c })} />
              Stop at 100 % of minutes
            </label>
          </div>
        )}
      </FormCard>

      <FormCard title="Business profile"
        description="Changing the type doesn't replace existing services or FAQs."
        initial={{ name: t.name, vertical: t.vertical, phone: t.phone ?? '', website: t.website ?? '', timezone: t.timezone, country: t.country, currency: t.currency }}
        toPatch={(c) => c}>
        {(v, set) => (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" className="sm:col-span-2"><Input value={v.name} onChange={(e) => set({ name: e.target.value })} /></Field>
            <Field label="Type">
              <SimpleSelect value={v.vertical} onChange={(s) => set({ vertical: s as TenantDoc['vertical'] })}
                options={(verticals.data?.verticals ?? [{ key: t.vertical, label: titleCase(t.vertical) }]).map((x) => ({ value: x.key, label: x.label }))} />
            </Field>
            <Field label="Timezone">
              <SimpleSelect value={v.timezone} onChange={(s) => set({ timezone: s })}
                options={[...new Set([t.timezone, ...TIMEZONES])].map((z) => ({ value: z, label: z }))} />
            </Field>
            <Field label="Public phone"><Input value={v.phone} placeholder="+61 2 5550 4120" onChange={(e) => set({ phone: e.target.value })} /></Field>
            <Field label="Website"><Input value={v.website} onChange={(e) => set({ website: e.target.value })} /></Field>
            <Field label="Country">
              <SimpleSelect value={v.country} onChange={(s) => set({ country: s })}
                options={[...new Set([t.country, ...COUNTRIES.map(([c]) => c as string)])].filter(Boolean).map((c) => ({
                  value: c, label: COUNTRIES.find(([k]) => k === c)?.[1] ?? c,
                }))} />
            </Field>
            <Field label="Currency">
              <SimpleSelect value={v.currency} onChange={(s) => set({ currency: s })}
                options={[...new Set([t.currency, ...CURRENCIES])].map((c) => ({ value: c, label: c }))} />
            </Field>
          </div>
        )}
      </FormCard>

      <FormCard title="Owner contact" description="Where booking, message and urgent-call alerts go."
        initial={{ owner_email: t.notifications?.owner_email ?? '', owner_phone: t.notifications?.owner_phone ?? '' }}
        toPatch={(c) => ({ notifications: c })}>
        {(v, set) => (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Alert e-mail"><Input type="email" value={v.owner_email} onChange={(e) => set({ owner_email: e.target.value })} /></Field>
            <Field label="Alert phone (SMS)"><Input value={v.owner_phone} placeholder="+61 412 345 678" onChange={(e) => set({ owner_phone: e.target.value })} /></Field>
          </div>
        )}
      </FormCard>

      <DangerZone tenant={t} />
    </div>
  )
}

function DangerZone({ tenant: t }: { tenant: TenantDoc }) {
  const m = usePlatformMutations()
  const [confirm, setConfirm] = useState<'suspend' | 'cancel' | null>(null)
  const [typed, setTyped] = useState('')
  const suspended = t.status === 'suspended'
  const cancelled = t.status === 'cancelled'
  const close = () => { setConfirm(null); setTyped('') }
  return (
    <Card className="border-destructive/40 lg:col-span-2">
      <CardHeader>
        <CardTitle className="text-destructive">Danger zone</CardTitle>
        <CardDescription>Both actions are audited on the business.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm">
            <div className="font-medium">{suspended ? 'Resume the business' : 'Suspend the business'}</div>
            <div className="text-muted-foreground">{suspended ? 'Calls are answered again and the team gets full access back.' : 'Stops answering calls and locks the dashboard except billing and export. Reversible.'}</div>
          </div>
          {suspended ? (
            <Button variant="outline" disabled={m.update.isPending}
              onClick={() => m.update.mutate({ id: t.id, status: 'active' }, { onSuccess: () => toast.success('Resumed') })}><Play /> Resume</Button>
          ) : (
            <Button variant="outline" disabled={cancelled} onClick={() => setConfirm('suspend')}><Pause /> Suspend</Button>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
          <div className="text-sm">
            <div className="font-medium">Cancel the business</div>
            <div className="text-muted-foreground">Ends the Stripe subscription now, e-mails the owners, and deletes data and releases numbers after the retention period.</div>
          </div>
          <Button variant="destructive" disabled={cancelled} onClick={() => setConfirm('cancel')}><XCircle /> {cancelled ? 'Cancelled' : 'Cancel business'}</Button>
        </div>
      </CardContent>
      <ConfirmDialog open={confirm === 'suspend'} onOpenChange={(o) => !o && close()} destructive confirmLabel="Suspend"
        title={`Suspend ${t.name}?`} description="Callers stop reaching the assistant until you resume it." loading={m.update.isPending}
        onConfirm={() => m.update.mutate({ id: t.id, status: 'suspended' }, { onSuccess: () => { toast.success('Suspended'); close() } })} />
      <ConfirmDialog open={confirm === 'cancel'} onOpenChange={(o) => !o && close()} destructive confirmLabel="Cancel business"
        title={`Cancel ${t.name}?`} loading={m.cancel.isPending}
        description="The subscription ends immediately and the owners are e-mailed. They can re-subscribe before the data is deleted."
        onConfirm={() => {
          if (typed.trim() !== t.name.trim()) return void toast.error('Type the business name exactly to confirm')
          m.cancel.mutate(t.id, { onSuccess: () => { toast.success('Cancelled'); close() } })
        }}>
        <Field label={`Type "${t.name}" to confirm`}><Input value={typed} onChange={(e) => setTyped(e.target.value)} /></Field>
      </ConfirmDialog>
    </Card>
  )
}

/* ───────────── team ───────────── */

function LinkDialog({ title, link, onClose }: { title: string; link: string; onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>One-time link, valid for 1 hour. It isn't e-mailed, so send it yourself.</DialogDescription>
        </DialogHeader>
        <div className="flex min-w-0 items-start gap-1 rounded border p-2">
          <code className="min-w-0 flex-1 break-all text-xs">{link}</code>
          <Button size="icon-xs" variant="ghost" aria-label="Copy" onClick={() => copy(link)}><Copy /></Button>
        </div>
        <DialogFooter><Button onClick={onClose}>Done</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TeamTab({ data }: { data: TenantDetails }) {
  const t = data.tenant
  const team = useTenantTeamMutations(t.id)
  const [adding, setAdding] = useState(false)
  const [link, setLink] = useState<{ title: string; link: string } | null>(null)
  const [removing, setRemoving] = useState<{ id: string; email: string } | null>(null)
  const max = t.limits?.max_users
  return (
    <div className="space-y-4">
      <Card className="gap-0 py-0">
        <CardHeader className="py-4">
          <CardTitle>Members</CardTitle>
          <CardDescription>{data.members.length}{max ? ` of ${max}` : ''} dashboard users. The plan's user limit doesn't apply to people you add here.</CardDescription>
          <CardAction><Button size="sm" onClick={() => setAdding(true)}><UserPlus /> Add member</Button></CardAction>
        </CardHeader>
        <Table>
          <TableHeader>
            <TableRow><TableHead>Person</TableHead><TableHead>Role</TableHead><TableHead>Last sign-in</TableHead><TableHead>Account</TableHead><TableHead /></TableRow>
          </TableHeader>
          <TableBody>
            {data.members.map((u) => (
              <TableRow key={u.id}>
                <TableCell><div className="font-medium">{u.name || u.email}</div>{u.name && <div className="text-xs text-muted-foreground">{u.email}</div>}</TableCell>
                <TableCell>
                  <SimpleSelect size="sm" value={u.role ?? 'staff'} disabled={team.setRole.isPending}
                    onChange={(role) => team.setRole.mutate({ userId: u.id, role: role as Role }, { onSuccess: () => toast.success(`${u.email} is now ${role}`) })}
                    options={ROLES.map((r) => ({ value: r, label: titleCase(r) }))} />
                </TableCell>
                <TableCell className="text-muted-foreground">{timeAgo(u.last_login_at)}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {u.disabled && <Badge variant="destructive">Disabled</Badge>}
                    {u.pending_password && <Badge variant="secondary">No password yet</Badge>}
                    {u.mfa && <Badge variant="outline">2FA</Badge>}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button size="xs" variant="outline" disabled={u.disabled || team.resetLink.isPending}
                      onClick={() => team.resetLink.mutate(u.id, { onSuccess: (r) => setLink({ title: `Password link for ${u.email}`, link: r.link }) })}>
                      <KeyRound /> Password link
                    </Button>
                    <Button size="icon-xs" variant="ghost" className="text-destructive" aria-label={`Remove ${u.email}`} onClick={() => setRemoving({ id: u.id, email: u.email })}>
                      <Trash2 />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      {data.invites.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Pending invites</CardTitle></CardHeader>
          <CardContent className="divide-y">
            {data.invites.map((i) => (
              <KeyValue key={i.email} label={i.email}>{titleCase(i.role)} · expires {dateOnly(i.expires_at)}</KeyValue>
            ))}
          </CardContent>
        </Card>
      )}
      {adding && <AddMemberDialog tenantId={t.id} tenantName={t.name} onClose={() => setAdding(false)}
        onLink={(l) => setLink({ title: 'Member added', link: l })} />}
      {link && <LinkDialog title={link.title} link={link.link} onClose={() => setLink(null)} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} destructive confirmLabel="Remove"
        title={`Remove ${removing?.email}?`} description={`They lose access to ${t.name} and are signed out of it. Their account stays.`}
        loading={team.remove.isPending}
        onConfirm={() => removing && team.remove.mutate(removing.id, { onSuccess: () => { toast.success('Removed'); setRemoving(null) } })} />
    </div>
  )
}

function AddMemberDialog({ tenantId, tenantName, onClose, onLink }: { tenantId: string; tenantName: string; onClose: () => void; onLink: (link: string) => void }) {
  const team = useTenantTeamMutations(tenantId)
  const [form, setForm] = useState<{ email: string; name: string; role: Role }>({ email: '', name: '', role: 'staff' })
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add a member to {tenantName}</DialogTitle>
          <DialogDescription>Someone who already has an account gets access straight away. New people get a link to set their password.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <Field label="E-mail"><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Name (optional)"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Role">
            <SimpleSelect value={form.role} onChange={(r) => setForm({ ...form, role: r as Role })} options={ROLES.map((r) => ({ value: r, label: titleCase(r) }))} />
          </Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email) || team.add.isPending}
            onClick={() => team.add.mutate(form, {
              onSuccess: (r) => {
                toast.success(`${r.member.email} added`)
                onClose()
                if (r.set_password_link) onLink(r.set_password_link)
              },
            })}>
            {team.add.isPending && <Loader2 className="animate-spin" />} Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ───────────── numbers ───────────── */

function NumbersTab({ data }: { data: TenantDetails }) {
  const t = data.tenant
  const m = usePlatformMutations()
  const orders = useNumberOrderMutations()
  const [adding, setAdding] = useState(false)
  const [releasing, setReleasing] = useState<string | null>(null)
  const rows = [...data.numbers].sort((a, b) => Number(a.status === 'released') - Number(b.status === 'released'))
  const recentOrders = (data.number_orders ?? []).slice(0, 3)
  return (
    <div className="space-y-4">
      {recentOrders.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Number setup</CardTitle>
            <CardDescription>Bought or connected numbers, step by step. Calls are answered once a number is routed.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {recentOrders.map((o) => (
              <OrderProgress key={o.id} order={o} action={(o.status === 'failed' || o.status === 'verify_failed') && (
                <Button size="xs" variant="outline" disabled={orders.retry.isPending}
                  onClick={() => orders.retry.mutate(o.id, { onSuccess: () => toast.success('Retried') })}>
                  {orders.retry.isPending && <Loader2 className="animate-spin" />} Retry
                </Button>
              )} />
            ))}
          </CardContent>
        </Card>
      )}
      <Card className="gap-0 py-0">
        <CardHeader className="py-4">
          <CardTitle>Phone numbers</CardTitle>
          <CardDescription>Calls to these numbers are answered as {t.name}.</CardDescription>
          <CardAction><Button size="sm" onClick={() => setAdding(true)}><Plus /> Add number</Button></CardAction>
        </CardHeader>
        {rows.length === 0 ? (
          <EmptyState title="No numbers" description="Add one so callers can reach the assistant." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>Number</TableHead><TableHead>Purpose</TableHead><TableHead>Provider</TableHead><TableHead>Status</TableHead><TableHead>Assigned</TableHead><TableHead /></TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((n) => (
                <TableRow key={n.e164} className={n.status === 'released' ? 'opacity-60' : undefined}>
                  <TableCell className="font-mono text-xs">{n.e164}</TableCell>
                  <TableCell>{titleCase(n.purpose ?? 'inbound')}</TableCell>
                  <TableCell>{titleCase(n.provider)}</TableCell>
                  <TableCell><Badge variant={n.status === 'released' ? 'outline' : 'secondary'}>{titleCase(n.status ?? 'active')}</Badge></TableCell>
                  <TableCell className="text-muted-foreground">{dateOnly(n.assigned_at)}</TableCell>
                  <TableCell className="text-right">
                    {n.status !== 'released' && <Button size="xs" variant="ghost" className="text-destructive" onClick={() => setReleasing(n.e164)}>Release</Button>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      {adding && <AddNumberDialog tenant={t} onClose={() => setAdding(false)} />}
      <ConfirmDialog open={!!releasing} onOpenChange={(o) => !o && setReleasing(null)} destructive confirmLabel="Release"
        title={`Release ${releasing}?`} description={`Calls to this number stop reaching ${t.name}'s assistant straight away.`}
        loading={m.releaseNumber.isPending}
        onConfirm={() => releasing && m.releaseNumber.mutate(releasing, { onSuccess: () => { toast.success('Released'); setReleasing(null) } })} />
    </div>
  )
}

/* ───────────── activity ───────────── */

function actor(entry: AuditEntry, data: TenantDetails): string {
  const id = entry.user_id ?? ''
  if (!id) return 'System'
  if (id.startsWith('platform:')) return 'Platform admin'
  return data.members.find((m) => m.id === id)?.email ?? id
}

function ActivityTab({ data }: { data: TenantDetails }) {
  if (data.audit.length === 0) return <Card><EmptyState title="No activity yet" /></Card>
  return (
    <Card className="gap-0 py-0">
      <CardHeader className="py-4">
        <CardTitle>Audit trail</CardTitle>
        <CardDescription>The latest 50 changes to this business, by its team and by platform admins.</CardDescription>
      </CardHeader>
      {(
        <Table>
          <TableHeader>
            <TableRow><TableHead>When</TableHead><TableHead>Who</TableHead><TableHead>Action</TableHead><TableHead>Details</TableHead></TableRow>
          </TableHeader>
          <TableBody>
            {data.audit.map((a, i) => {
              const keys = Object.keys(a.diff ?? {})
              return (
                <TableRow key={`${a.at}-${i}`} className="align-top">
                  <TableCell className="whitespace-nowrap text-muted-foreground" title={a.at}>{dateTime(a.at)}</TableCell>
                  <TableCell className="text-xs">{actor(a, data)}</TableCell>
                  <TableCell><code className="text-xs">{a.action}</code></TableCell>
                  <TableCell className="max-w-lg text-xs">
                    {keys.length === 0 ? <span className="text-muted-foreground">{a.target_type}{a.target_id && a.target_id !== data.tenant.id ? ` ${a.target_id}` : ''}</span> : (
                      <details>
                        <summary className="cursor-pointer text-muted-foreground">{keys.join(', ')}</summary>
                        <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted p-2 whitespace-pre-wrap break-all">{JSON.stringify(a.diff, null, 2)}</pre>
                      </details>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Card>
  )
}
