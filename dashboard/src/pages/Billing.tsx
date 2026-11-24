import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, Check, CreditCard, Download, Loader2, ShieldCheck } from 'lucide-react'
import { ErrorBanner, Field, PageHeader } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Switch } from '@receptionist/shared/components/ui/switch'
import {
  downloadFile,
  STATUS_LABEL,
  formatPrice,
  useBilling,
  useCheckout,
  useConfirmPhone,
  usePatchBilling,
  usePortal,
  useVerifyPhone,
  type Billing as BillingData,
} from '@/lib/billing'
import { dateOnly } from '@receptionist/shared/lib/format'
import { useCtx } from '@/lib/tenant'
import { cn } from '@receptionist/shared/lib/utils'

function statusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'active') return 'default'
  if (status === 'trial') return 'secondary'
  if (status === 'past_due' || status === 'suspended' || status === 'cancelled') return 'destructive'
  return 'outline'
}

function UsageCard({ b }: { b: BillingData }) {
  const u = b.usage
  const pct = Math.min(100, u.percent ?? 0)
  const bar = pct >= 100 ? 'bg-destructive' : pct >= 80 ? 'bg-amber-500' : 'bg-primary'
  return (
    <Card>
      <CardHeader>
        <CardTitle>{u.trial ? 'Pilot minutes' : 'Minutes this period'}</CardTitle>
        <CardDescription>{dateOnly(u.period_start)} – {dateOnly(u.period_end)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-baseline justify-between">
          <span className="text-3xl font-semibold tabular-nums">{u.used_minutes}</span>
          <span className="text-sm text-muted-foreground">of {u.included_minutes || '∞'} included</span>
        </div>
        {u.included_minutes > 0 && (
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className={cn('h-full rounded-full transition-all', bar)} style={{ width: `${pct}%` }} />
          </div>
        )}
        {u.overage_minutes > 0 && (
          <p className="text-sm">
            {u.overage_minutes} extra minutes · about {formatPrice(u.overage_minutes * b.overage_per_min, b.currency, true)} on your next invoice (before tax)
          </p>
        )}
        {u.capped && (
          <p className="flex items-start gap-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            {u.trial ? 'Pilot minutes used up — calls are being forwarded. Subscribe to keep the assistant answering.'
              : 'Hard cap reached — calls are being forwarded until the next period.'}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function PlanPicker({ b }: { b: BillingData }) {
  const checkout = useCheckout()
  const [annual, setAnnual] = useState(false)
  const plans = b.catalogue.plans.filter((p) => p.self_serve)
  return (
    <Card>
      <CardHeader>
        <CardTitle>{b.status === 'cancelled' ? 'Re-subscribe' : 'Choose a plan'}</CardTitle>
        <CardDescription>
          {!b.trial_used && b.status === 'incomplete'
            ? `${b.catalogue.trial.days}-day pilot, card required.${plans.some((p) => p.setup_fee_required) ? ' The onboarding fee is charged at sign-up.' : ''} Cancel during the pilot and the monthly fee is never charged.`
            : 'Billed through Stripe. Change or cancel any time.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={annual} onCheckedChange={setAnnual} /> Yearly billing ({b.catalogue.annual_months_free} months free)
        </label>
        <div className="grid gap-3 md:grid-cols-3">
          {plans.map((p) => (
            <div key={p.key} className={cn('flex flex-col rounded-xl border p-4', p.recommended && 'border-primary')}>
              <div className="flex items-center justify-between">
                <span className="font-semibold">{p.name}</span>
                {p.recommended && <Badge>Recommended</Badge>}
              </div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">
                {formatPrice(annual ? p.annual / 12 : p.monthly, p.currency)}<span className="text-sm font-normal text-muted-foreground">/mo</span>
              </div>
              <ul className="my-3 flex-1 space-y-1 text-sm">
                {p.features.map((f) => <li key={f} className="flex gap-2"><Check className="mt-0.5 size-4 shrink-0 text-primary" />{f}</li>)}
              </ul>
              <Button disabled={!b.can_manage || checkout.isPending}
                onClick={() => checkout.mutate({ plan: p.key, interval: annual ? 'year' : 'month' })}>
                {checkout.isPending && checkout.variables?.plan === p.key && <Loader2 className="animate-spin" />}
                {b.status === 'incomplete' && !b.trial_used ? `Start pilot on ${p.name}` : `Choose ${p.name}`}
              </Button>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Extra minutes {formatPrice(b.overage_per_min, b.currency, true)} each. Prices exclude tax; VAT, GST or sales tax is added by Stripe where it applies.
          {!b.can_manage && ' Only the business owner can change the plan.'}
        </p>
      </CardContent>
    </Card>
  )
}

function VerifyPhoneCard({ b }: { b: BillingData }) {
  const start = useVerifyPhone()
  const confirm = useConfirmPhone()
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [asked, setAsked] = useState(false)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><ShieldCheck className="size-4" /> Your phone number</CardTitle>
        <CardDescription>
          {b.status === 'trial'
            ? 'During the pilot your assistant can only transfer callers to your own, verified number.'
            : 'Verified numbers can receive transfers and urgent alerts.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {b.verified_phone && <p className="text-sm">Verified: <span className="font-mono">{b.verified_phone}</span></p>}
        {!asked ? (
          <div className="flex flex-wrap items-end gap-2">
            <Field label={b.verified_phone ? 'Verify a different number' : 'Mobile number'}>
              <Input value={phone} placeholder="+61 412 345 678" onChange={(e) => setPhone(e.target.value)} />
            </Field>
            <Button variant="outline" disabled={!b.can_manage || phone.replace(/\D/g, '').length < 8 || start.isPending}
              onClick={() => start.mutate(phone, {
                onSuccess: (r) => {
                  setAsked(true)
                  toast.success(r.dev_code ? `Development code: ${r.dev_code}` : 'Calling you now. Answer to hear your code.')
                },
              })}>
              {start.isPending && <Loader2 className="animate-spin" />} Call me with a code
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-2">
            <Field label="6-digit code">
              <Input inputMode="numeric" maxLength={6} className="w-32 tracking-widest" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
            </Field>
            <Button disabled={code.length !== 6 || confirm.isPending}
              onClick={() => confirm.mutate(code, { onSuccess: () => { toast.success('Number verified'); setAsked(false); setCode('') } })}>
              Confirm
            </Button>
            <Button variant="ghost" onClick={() => setAsked(false)}>Back</Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ExportCard({ b }: { b: BillingData }) {
  const ctx = useCtx()
  const [busy, setBusy] = useState(false)
  const run = async (path: string, name: string) => {
    setBusy(true)
    try {
      await downloadFile(path, name)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your data</CardTitle>
        <CardDescription>
          Calls, transcripts, contacts, appointments and messages — one zip, any time.
          {b.delete_after && ` Everything is deleted on ${dateOnly(b.delete_after)}.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-2">
        <Button disabled={!b.can_manage || busy} onClick={() => void run('/t/export', `${ctx.tenant.slug}-export.zip`)}>
          {busy ? <Loader2 className="animate-spin" /> : <Download />} Export all data
        </Button>
        <Button variant="outline" disabled={!b.can_manage || busy} onClick={() => void run('/t/export/appointments.ics', `${ctx.tenant.slug}-appointments.ics`)}>
          Calendar (.ics)
        </Button>
      </CardContent>
    </Card>
  )
}

function StatusNotice({ b }: { b: BillingData }) {
  const portal = usePortal()
  const fix = b.has_customer && (
    <Button size="sm" variant="outline" onClick={() => portal.mutate()} disabled={portal.isPending}>Update card</Button>
  )
  let text: string | null = null
  if (b.status === 'past_due') text = 'Your last payment failed. Stripe will retry automatically — update your card to avoid any interruption.'
  if (b.status === 'suspended') text = "Your assistant is paused because we couldn't charge your card. Calls are being forwarded to your transfer number."
  if (b.status === 'cancelled') text = `Your subscription has ended. Calls are being forwarded. Your data is kept until ${dateOnly(b.delete_after)} — export it or re-subscribe before then.`
  if (b.cancel_at_period_end && b.status !== 'cancelled') text = `Your plan ends on ${dateOnly(b.usage.period_end)}. Resume it from "Manage billing" to keep your assistant.`
  if (!text) return null
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
      <AlertTriangle className="size-4 shrink-0 text-destructive" />
      <span className="flex-1">{text}</span>
      {fix}
    </div>
  )
}

export default function Billing() {
  const q = useBilling()
  const portal = usePortal()
  const patch = usePatchBilling()
  const [params, setParams] = useSearchParams()

  useEffect(() => {
    if (params.get('checkout') === 'cancelled') {
      toast.info('Checkout cancelled — nothing was charged.')
      setParams({}, { replace: true })
    }
  }, [params, setParams])

  if (q.error) return <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data) return <Skeleton className="h-96 w-full" />
  const b = q.data
  const needsPlan = b.self_serve && (['incomplete', 'cancelled'].includes(b.status) || (!b.has_customer && b.billing_enabled))
  const subscribed = b.has_customer && !['incomplete'].includes(b.status)

  return (
    <>
      <PageHeader
        title="Plan & billing"
        description={b.self_serve ? 'Your plan, minutes and invoices.' : 'Your plan is managed by us — contact us to change it.'}
        actions={subscribed && b.can_manage && (
          <Button variant="outline" onClick={() => portal.mutate()} disabled={portal.isPending}>
            {portal.isPending ? <Loader2 className="animate-spin" /> : <CreditCard />} Manage billing
          </Button>
        )}
      />
      <StatusNotice b={b} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {b.plan_name} <Badge variant={statusVariant(b.status)}>{STATUS_LABEL[b.status] ?? b.status}</Badge>
            </CardTitle>
            <CardDescription>
              {b.status === 'trial' && b.trial_ends_at ? `Pilot ends ${dateOnly(b.trial_ends_at)}.` : null}
              {b.self_serve && b.status !== 'trial' && b.status !== 'incomplete' ? ` Billed ${b.interval === 'year' ? 'yearly' : 'monthly'}.` : null}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-sm text-muted-foreground">
            <div>{b.limits.included_minutes ? `${b.limits.included_minutes} minutes / period` : 'Custom minutes'}</div>
            <div>{b.limits.max_users ? `${b.limits.max_users} dashboard user(s)` : 'Unlimited users'}</div>
            <div>{b.limits.max_concurrent_calls} calls at once</div>
          </CardContent>
        </Card>
        <UsageCard b={b} />
        <Card>
          <CardHeader>
            <CardTitle>At 100% of your minutes</CardTitle>
            <CardDescription>By default your assistant keeps answering and extra minutes are billed. Never miss a call.</CardDescription>
          </CardHeader>
          <CardContent>
            <label className="flex items-start gap-3 text-sm">
              <Switch checked={b.hard_cap} disabled={!b.can_manage || patch.isPending}
                onCheckedChange={(v) => patch.mutate({ hard_cap: v }, { onSuccess: () => toast.success(v ? 'Hard cap on' : 'Hard cap off') })} />
              <span>Stop at my included minutes and forward calls instead (no extra charges)</span>
            </label>
          </CardContent>
        </Card>
      </div>
      <div className="mt-4 space-y-4">
        {needsPlan && b.billing_enabled && <PlanPicker b={b} />}
        {!b.billing_enabled && b.self_serve && (
          <p className="text-sm text-muted-foreground">Billing isn't configured on this server (development) — your pilot runs without a card.</p>
        )}
        <div className="grid gap-4 lg:grid-cols-2">
          {b.self_serve && <VerifyPhoneCard b={b} />}
          <ExportCard b={b} />
        </div>
      </div>
    </>
  )
}
