import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, ClipboardList, FileText, Phone, PhoneCall, Sparkles, Star, Wallet } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card } from '@receptionist/shared/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { timeAgo } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useTenant } from '@/lib/tenant'
import { formatMinor, URGENCY_STYLE, zoneDateTime } from '@/lib/tenant-format'
import { useLeads, useUpdateLead } from '@/lib/tenant-hooks'
import type { Lead, Tenant } from '@/lib/tenant-types'

type Status = Lead['status']
type View = Status | 'all'
type KindFilter = 'all' | 'dental_emergency' | 'cosmetic_consult'

const STATUSES: Status[] = ['new', 'contacted', 'won', 'lost']

/** Wording follows the business: a dental practice has patients and enquiries, a trade has customers and jobs. */
function copyFor(vertical: string) {
  if (vertical === 'dental') {
    return {
      title: 'Captured leads',
      description: 'Dental emergencies and treatment enquiries your assistant captured, ready for a callback.',
      emptyAll: 'When a patient calls with an urgent problem or asks about implants, aligners or cosmetic work, the lead lands here and you get a text.',
      person: 'patient',
      status: { new: 'New', contacted: 'Called back', won: 'Booked', lost: 'Not going ahead' } as Record<Status, string>,
    }
  }
  if (vertical === 'home_services') {
    return {
      title: 'Job requests',
      description: "Every job your assistant captured: who, where, what's wrong and how urgent.",
      emptyAll: 'When callers describe a job, it lands here and you get a text.',
      person: 'customer',
      status: { new: 'New', contacted: 'Contacted', won: 'Won', lost: 'Lost' } as Record<Status, string>,
    }
  }
  return {
    title: 'Leads',
    description: 'Enquiries your assistant captured, ready for a callback.',
    emptyAll: 'When a caller leaves an enquiry, it lands here and you get a text.',
    person: 'caller',
    status: { new: 'New', contacted: 'Contacted', won: 'Won', lost: 'Lost' } as Record<Status, string>,
  }
}

const URGENCY_LABEL: Record<Lead['urgency'], string> = {
  emergency: 'Emergency', today: 'Today', this_week: 'This week', flexible: 'Flexible',
}
const URGENCY_RANK: Record<Lead['urgency'], number> = { emergency: 0, today: 1, this_week: 2, flexible: 3 }

// Mirrors core/tools/dental.py TREATMENT_LABELS.
const TREATMENT_LABEL: Record<string, string> = {
  implants: 'Dental implants', all_on_4: 'All-on-4 / full-arch implants', clear_aligners: 'Clear aligners',
  veneers: 'Veneers', composite_bonding: 'Composite bonding', whitening: 'Teeth whitening',
  other_cosmetic: 'Cosmetic treatment',
}
const SEVERITY_LABEL: Record<string, string> = { life_threatening: 'Life-threatening', urgent: 'Urgent', routine: 'Routine' }
const TIMEFRAME_LABEL: Record<string, string> = {
  asap: 'Wants to start ASAP', within_1_month: 'Within a month', '1_3_months': 'In 1–3 months', researching: 'Just researching',
}
const PATIENT_LABEL: Record<string, string> = { new: 'New patient', existing: 'Existing patient' }

function fundingLabel(v: Lead['funding_interest'], country: string): string | null {
  const fund = country === 'GB' ? 'insurance / dental plan' : 'health fund'
  if (v === 'health_fund') return `Asking about ${fund}`
  if (v === 'finance') return 'Asking about payment plans'
  if (v === 'both') return `Asking about ${fund} & payment plans`
  return null
}

const isUrgent = (l: Lead) => l.urgency === 'emergency' || l.urgency === 'today'
const isOpen = (l: Lead) => l.status === 'new' || l.status === 'contacted'

export default function Leads() {
  const { ctx } = useTenant()
  const tenant = ctx!.tenant
  const dental = ctx!.vertical.key === 'dental'
  const copy = copyFor(ctx!.vertical.key)

  const [view, setView] = useState<View>('new')
  const [kind, setKind] = useState<KindFilter>('all')
  // One query for every status, so the tabs can show counts and switching is instant.
  const q = useLeads()
  const all = useMemo(() => q.data?.leads ?? [], [q.data])

  const ofKind = useMemo(() => (kind === 'all' ? all : all.filter((l) => l.kind === kind)), [all, kind])
  const counts = useMemo(() => {
    const c: Record<View, number> = { new: 0, contacted: 0, won: 0, lost: 0, all: ofKind.length }
    for (const l of ofKind) c[l.status] += 1
    return c
  }, [ofKind])

  const rows = useMemo(() => {
    const list = view === 'all' ? ofKind : ofKind.filter((l) => l.status === view)
    // The New tab is a callback queue: most urgent first, then newest.
    if (view !== 'new') return list
    return [...list].sort((a, b) => URGENCY_RANK[a.urgency] - URGENCY_RANK[b.urgency] || b.created_at.localeCompare(a.created_at))
  }, [ofKind, view])

  const urgentWaiting = all.filter((l) => l.status === 'new' && isUrgent(l)).length
  const pipeline = all
    .filter((l) => l.kind === 'cosmetic_consult' && isOpen(l) && typeof l.estimated_value_minor === 'number')
    .reduce((sum, l) => sum + (l.estimated_value_minor ?? 0), 0)
  const won = all.filter((l) => l.status === 'won').length

  const emptyText = all.length === 0
    ? copy.emptyAll
    : view === 'new' ? 'Nothing waiting for a callback. Nice work.' : 'No leads with this status yet.'

  return (
    <>
      <PageHeader
        title={copy.title}
        description={copy.description}
        actions={dental ? (
          <SimpleSelect size="sm" value={kind} onChange={(v) => setKind(v as KindFilter)}
            options={[
              { value: 'all', label: 'All leads' },
              { value: 'dental_emergency', label: 'Emergencies' },
              { value: 'cosmetic_consult', label: 'Treatment enquiries' },
            ]} />
        ) : undefined}
      />
      {q.error && <ErrorBanner error={q.error} />}

      {all.length > 0 && (
        <div className="mb-6 grid gap-3 sm:grid-cols-3">
          <SummaryTile icon={<PhoneCall className="size-4" />} label="Waiting for a callback" value={q.data?.new ?? counts.new}
            onClick={() => setView('new')} />
          <SummaryTile icon={<AlertTriangle className="size-4" />} label="Urgent, not called yet" value={urgentWaiting}
            tone={urgentWaiting > 0 ? 'alert' : undefined} onClick={() => setView('new')} />
          {dental ? (
            <SummaryTile icon={<Wallet className="size-4" />} label="Open treatment pipeline"
              value={pipeline > 0 ? formatMinor(pipeline, tenant.currency, tenant.locale) : '—'}
              hint="Estimated from your treatment fees" />
          ) : (
            <SummaryTile icon={<Star className="size-4" />} label="Won" value={won} onClick={() => setView('won')} />
          )}
        </div>
      )}

      <Tabs value={view} onValueChange={(v) => setView(String(v) as View)} className="mb-3">
        <TabsList className="h-auto max-w-full flex-wrap justify-start">
          {[...STATUSES, 'all' as const].map((s) => (
            <TabsTrigger key={s} value={s}>
              {s === 'all' ? 'All' : copy.status[s]}
              {counts[s] > 0 && (
                <Badge variant={s === 'new' ? 'default' : 'secondary'} className="ml-1">{counts[s]}</Badge>
              )}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <Card className="gap-0 py-0">
        {q.isLoading ? <TableSkeleton rows={5} cols={4} /> : rows.length === 0 ? (
          <EmptyState title={all.length === 0 ? `No ${copy.title.toLowerCase()} yet` : 'Nothing here'}
            icon={<ClipboardList className="size-8" />} description={emptyText} />
        ) : (
          <ul className="divide-y">
            {rows.map((l) => <LeadRow key={l.id} lead={l} tenant={tenant} statusLabel={copy.status} />)}
          </ul>
        )}
      </Card>
      {all.length >= 200 && (
        <p className="mt-2 text-xs text-muted-foreground">Showing your 200 most recent leads.</p>
      )}
    </>
  )
}

function SummaryTile({ icon, label, value, hint, tone, onClick }: {
  icon: ReactNode; label: string; value: ReactNode; hint?: string; tone?: 'alert'; onClick?: () => void
}) {
  const body = (
    <>
      <div className={cn('flex items-center gap-2 text-xs font-medium text-muted-foreground', tone === 'alert' && 'text-red-700 dark:text-red-300')}>
        {icon} {label}
      </div>
      <div className={cn('mt-1 text-2xl font-semibold tabular-nums', tone === 'alert' && 'text-red-700 dark:text-red-300')}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </>
  )
  const cls = cn('rounded-xl border bg-card p-4 text-left', tone === 'alert' && 'border-red-200 bg-red-50 dark:border-red-500/30 dark:bg-red-500/10')
  return onClick
    ? <button type="button" onClick={onClick} className={cn(cls, 'transition-colors hover:bg-muted/50')}>{body}</button>
    : <div className={cls}>{body}</div>
}

function Fact({ children }: { children: ReactNode }) {
  return <span className="inline-flex items-center rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">{children}</span>
}

function LeadRow({ lead: l, tenant, statusLabel }: { lead: Lead; tenant: Tenant; statusLabel: Record<Status, string> }) {
  const update = useUpdateLead()
  const emergency = l.kind === 'dental_emergency'
  const cosmetic = l.kind === 'cosmetic_consult'
  const attention = l.status === 'new' && isUrgent(l)

  // Cosmetic leads store "<Treatment> enquiry: <notes>" as the problem; show the parts separately.
  const headline = cosmetic
    ? TREATMENT_LABEL[l.treatment_category ?? ''] ?? 'Treatment enquiry'
    : l.problem
  const notes = cosmetic ? l.notes : null

  const facts: ReactNode[] = []
  if (cosmetic) {
    if (l.service_name) facts.push(l.service_name)
    if (l.patient_status && PATIENT_LABEL[l.patient_status]) facts.push(PATIENT_LABEL[l.patient_status])
    if (l.timeframe && TIMEFRAME_LABEL[l.timeframe]) facts.push(TIMEFRAME_LABEL[l.timeframe])
    const funding = fundingLabel(l.funding_interest, tenant.country)
    if (funding) facts.push(funding)
    if (typeof l.estimated_value_minor === 'number') {
      facts.push(<>Est. {formatMinor(l.estimated_value_minor, l.currency || tenant.currency, tenant.locale)}</>)
    }
  }
  if (l.preferred_time) facts.push(<>Prefers {l.preferred_time}</>)
  if (l.address) facts.push(l.address)

  return (
    <li className={cn('flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:gap-6', attention && 'border-l-4 border-l-red-500 bg-red-50/40 dark:bg-red-500/5')}>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{l.name || 'Unknown caller'}</span>
          {emergency && <Badge variant="outline" className="gap-1"><AlertTriangle className="size-3" /> Emergency</Badge>}
          {cosmetic && <Badge variant="outline" className="gap-1"><Sparkles className="size-3" /> Treatment enquiry</Badge>}
          <Badge variant="secondary" className={cn('border-0', URGENCY_STYLE[l.urgency])}>
            {emergency && l.severity ? SEVERITY_LABEL[l.severity] : URGENCY_LABEL[l.urgency] ?? l.urgency}
          </Badge>
          {l.priority === 'high' && (
            <Badge variant="secondary" className="gap-1 border-0 bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300">
              <Star className="size-3" /> Hot lead
            </Badge>
          )}
        </div>

        {l.red_flag && (
          <div className="flex items-start gap-2 rounded-md bg-red-100 px-3 py-2 text-sm text-red-800 dark:bg-red-500/15 dark:text-red-300">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            Red-flag symptoms. The caller was told to contact emergency services.
          </div>
        )}

        <p className="text-sm">{headline}</p>
        {notes && <p className="text-sm text-muted-foreground">“{notes}”</p>}
        {facts.length > 0 && <div className="flex flex-wrap gap-1.5">{facts.map((f, i) => <Fact key={i}>{f}</Fact>)}</div>}
      </div>

      <div className="flex shrink-0 flex-col gap-2 sm:w-52 sm:items-end">
        <span className="text-xs text-muted-foreground" title={zoneDateTime(l.created_at, tenant.timezone)}>
          {timeAgo(l.created_at)}
        </span>
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          {l.phone ? (
            <Button size="sm" variant={l.status === 'new' ? 'default' : 'outline'} nativeButton={false} render={<a href={`tel:${l.phone}`} />}>
              <Phone /> {l.phone}
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">No callback number</span>
          )}
          {l.call_id && (
            <Button size="sm" variant="ghost" nativeButton={false} render={<Link to={`/calls?call=${encodeURIComponent(l.call_id)}`} />}>
              <FileText /> Call
            </Button>
          )}
        </div>
        <SimpleSelect size="sm" className="w-full sm:w-44" value={l.status}
          onChange={(v) => update.mutate({ id: l.id, status: v }, {
            onSuccess: () => toast.success(`Marked as ${statusLabel[v as Status].toLowerCase()}`),
            onError: (e) => toast.error(e instanceof Error ? e.message : 'Could not update the lead'),
          })}
          options={STATUSES.map((s) => ({ value: s, label: statusLabel[s] }))} />
      </div>
    </li>
  )
}
