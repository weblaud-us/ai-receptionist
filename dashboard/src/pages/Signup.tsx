import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { Check, CheckCircle2, Loader2 } from 'lucide-react'
import { useAuth } from '@receptionist/shared/auth/auth'
import { ErrorBanner, Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Input } from '@receptionist/shared/components/ui/input'
import { api, type TokenResponse } from '@receptionist/shared/lib/api'
import { formatPrice, usePlans, type PlanInfo } from '@/lib/billing'
import { cn } from '@receptionist/shared/lib/utils'
import { AuthCard } from '@receptionist/shared/auth/login-pages'
import { LaudlineLogo } from '@receptionist/shared/components/laudline-logo'
import { DENTAL_COUNTRIES, defaultTimezone, practiceTypesFor, type DentalCountry } from '@receptionist/shared/lib/dental'

/** A first guess from the browser's time zone; the visitor can change it. */
function guessCountry(tz: string): DentalCountry {
  return tz.startsWith('Australia/') ? 'AU' : 'GB'
}

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || ''
  } catch {
    return ''
  }
}

/** The browser's zone when it is in the practice's country, else that country's main zone. */
function timezoneFor(country: string): string {
  const tz = browserTimezone()
  const fits = country === 'AU' ? tz.startsWith('Australia/') : tz === 'Europe/London' || tz === 'Europe/Belfast'
  return fits ? tz : defaultTimezone(country)
}

interface SignupResponse extends TokenResponse {
  next: 'checkout' | 'onboarding'
  verification_emailed: boolean
}

export default function Signup() {
  const { status, applyTokens } = useAuth()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  // Two steps (pick a plan, then create the account), kept in the URL: the browser's Back button returns to the
  // plans and a reload keeps the choice.
  const step = params.get('step') === 'account' ? 'account' : 'plan'
  const [plan, setPlan] = useState(params.get('plan') ?? '')
  const [annual, setAnnual] = useState(params.get('interval') === 'year')
  // LaudLine is for dental practices in Australia and the UK only, so the business type is always dental;
  // the visitor says what kind of practice it is.
  const [form, setForm] = useState(() => {
    const asked = (params.get('country') ?? '').toUpperCase()
    const country = DENTAL_COUNTRIES.some((c) => c.value === asked) ? asked : guessCountry(browserTimezone())
    const pt = params.get('practice_type') ?? ''
    const practice_type = practiceTypesFor(country).some((t) => t.value === pt) ? pt : 'private'
    return { name: '', email: '', password: '', business_name: '', vertical: 'dental', practice_type, country }
  })
  const plans = usePlans(form.country, form.vertical)
  const [accept, setAccept] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => {
    const next = { ...f, [k]: v }
    // "Mixed NHS and private" is UK only, "Mixed private and public" AU only.
    if (!practiceTypesFor(next.country).some((t) => t.value === next.practice_type)) next.practice_type = 'private'
    return next
  })

  useEffect(() => { window.scrollTo(0, 0) }, [step])

  if (status === 'authenticated' && !busy) return <Navigate to="/" replace />
  const cat = plans.data
  if (cat && cat.signup_enabled === false) {
    return (
      <AuthCard title="Sign-up isn't open yet" description="We're onboarding businesses one by one for now.">
        <Button className="w-full" variant="outline" nativeButton={false} render={<Link to="/login" />}>Sign in</Button>
      </AuthCard>
    )
  }
  const selfServe = cat?.plans.filter((p) => p.self_serve) ?? []
  // A plan picked for another business type isn't offered for this one: fall back to its recommended plan.
  const chosen = selfServe.some((p) => p.key === plan)
    ? plan
    : (selfServe.find((p) => p.recommended) ?? selfServe[0])?.key ?? plan
  const chosenPlan = selfServe.find((p) => p.key === chosen)
  const trialLine = cat
    ? `${cat.trial.days}-day pilot with your first ${cat.trial.minutes} minutes included.`
      + (chosenPlan?.setup_fee_required ? ' The onboarding fee is charged at sign-up.' : '')
      + (cat.trial.card_required ? ' Cancel during the pilot and the monthly fee is never charged.' : '')
    : ''

  function choosePlan(key: string) {
    setPlan(key)
    setParams((p) => {
      const next = new URLSearchParams(p)
      next.set('step', 'account')
      next.set('plan', key)
      next.set('interval', annual ? 'year' : 'month')
      next.set('vertical', form.vertical)
      next.set('practice_type', form.practice_type)
      next.set('country', form.country)
      return next
    })
  }

  function backToPlans() {
    setParams((p) => {
      const next = new URLSearchParams(p)
      next.delete('step')
      return next
    })
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await api<SignupResponse>('/signup', {
        method: 'POST', auth: false,
        body: { ...form, email: form.email.trim(), plan: chosen, interval: annual ? 'year' : 'month', timezone: timezoneFor(form.country), accept_terms: accept },
      })
      applyTokens(res)
      if (res.next === 'checkout') {
        // Straight to Stripe Checkout: card first (trial included), then the setup wizard.
        const checkout = await api<{ url: string }>('/t/billing/checkout', { method: 'POST', body: { plan: chosen, interval: annual ? 'year' : 'month' } })
        window.location.assign(checkout.url)
        return
      }
      navigate('/onboarding', { replace: true })
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  const plansFailed = plans.isError && (
    <div className="rounded-xl border border-sidebar-border p-5 text-sm">
      Plans didn't load. Check your connection, then{' '}
      <button type="button" onClick={() => plans.refetch()} className="font-medium underline">try again</button>.
    </div>
  )

  if (step === 'plan') {
    const darkSelect = 'w-full border-sidebar-border bg-sidebar-accent/60 text-sidebar-foreground hover:bg-sidebar-accent'
    const three = (cat ? selfServe.length : 2) >= 3
    return (
      <SignupShell>
        <main className="mx-auto max-w-4xl px-4 pt-6 pb-16 sm:px-6 lg:pt-12">
          <div className="text-center">
            <h1 className="mx-auto max-w-2xl text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
              The AI overflow receptionist for dental practices
            </h1>
            <p className="mx-auto mt-3 max-w-xl text-pretty text-sidebar-foreground/70">
              {trialLine} Most practices are answering calls within days of sign-up.
            </p>
          </div>

          <div className="mt-10 flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
            <div className="grid gap-4 sm:grid-cols-[21rem_12rem]">
              <Field label="Type of practice">
                <SimpleSelect className={darkSelect} value={form.practice_type} onChange={set('practice_type')}
                  options={practiceTypesFor(form.country)} />
              </Field>
              <Field label="Country">
                <SimpleSelect className={darkSelect} value={form.country} onChange={set('country')}
                  options={DENTAL_COUNTRIES} />
              </Field>
            </div>
            <div role="group" aria-label="Billing period" className="inline-flex self-start rounded-full bg-sidebar-accent p-1 text-sm sm:self-auto">
              {[false, true].map((a) => (
                <button key={String(a)} type="button" aria-pressed={annual === a} onClick={() => setAnnual(a)}
                  className={cn('rounded-full px-3.5 py-1 font-medium transition-colors',
                    annual === a ? 'bg-sidebar-foreground text-sidebar' : 'text-sidebar-foreground/70 hover:text-sidebar-foreground')}>
                  {a ? 'Yearly' : 'Monthly'}
                  {a && (
                    <span className={cn('ml-1.5 text-xs', annual ? 'text-primary' : 'text-sidebar-primary')}>
                      {cat?.annual_months_free ?? 2} months free
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          <div className={cn('mt-6 grid gap-4', three ? 'lg:grid-cols-3' : 'md:grid-cols-2')}>
            {plans.isLoading && Array.from({ length: three ? 3 : 2 }, (_, n) => (
              <div key={n} className="h-96 animate-pulse rounded-xl bg-sidebar-accent/60" />
            ))}
            {plansFailed}
            {selfServe.map((p) => (
              <PlanCard key={p.key} plan={p} annual={annual} highlighted={chosen === p.key} onChoose={() => choosePlan(p.key)} />
            ))}
          </div>
          {cat && (
            <p className="mt-6 text-center text-xs text-pretty text-sidebar-foreground/60">
              Prices exclude VAT/GST. Extra minutes cost {formatPrice(cat.overage_per_min, cat.currency, true)} each, or set a hard cap. Agencies and chains: contact us.
            </p>
          )}
        </main>
      </SignupShell>
    )
  }

  return (
    <SignupShell>
      <main className="mx-auto max-w-md px-4 pt-6 pb-16 sm:pt-12">
        <Card className="[--card-spacing:--spacing(6)]">
          <CardHeader>
            <CardTitle className="text-lg font-semibold">Create your account</CardTitle>
            {trialLine && <CardDescription className="text-pretty">{trialLine}</CardDescription>}
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-0.5 rounded-lg bg-muted p-4 text-sm">
              <div className="flex items-baseline justify-between gap-4">
                {chosenPlan ? <span className="font-medium">{chosenPlan.name}</span>
                  : plans.isError ? <span className="text-muted-foreground">Plans didn't load.</span>
                  : <span className="h-4 w-28 animate-pulse rounded bg-foreground/10" />}
                <button type="button" onClick={backToPlans} className="shrink-0 font-medium text-primary hover:underline">
                  Change plan
                </button>
              </div>
              {chosenPlan && (
                <div className="tabular-nums text-muted-foreground">
                  {formatPrice(annual ? chosenPlan.annual / 12 : chosenPlan.monthly, chosenPlan.currency)}/mo,{' '}
                  {annual ? `${formatPrice(chosenPlan.annual, chosenPlan.currency)} billed yearly` : 'billed monthly'}
                </div>
              )}
              {chosenPlan?.setup_fee_required && chosenPlan.setup_fee ? (
                <div className="tabular-nums text-muted-foreground">
                  + {formatPrice(chosenPlan.setup_fee, chosenPlan.currency)} one-time onboarding
                </div>
              ) : null}
            </div>

            <form onSubmit={onSubmit} className="space-y-4">
              <Field label="Your name">
                <Input autoFocus autoComplete="name" value={form.name} onChange={(e) => set('name')(e.target.value)} />
              </Field>
              <Field label="Work e-mail">
                <Input type="email" autoComplete="email" required value={form.email} onChange={(e) => set('email')(e.target.value)} />
              </Field>
              <Field label="Password" hint="At least 10 characters.">
                <Input type="password" autoComplete="new-password" required minLength={10} value={form.password} onChange={(e) => set('password')(e.target.value)} />
              </Field>
              <Field label="Business name">
                <Input required value={form.business_name} onChange={(e) => set('business_name')(e.target.value)} />
              </Field>
              <label className="flex items-start gap-2.5 text-sm">
                <Checkbox checked={accept} onCheckedChange={(v) => setAccept(v === true)} className="mt-0.5" />
                <span className="text-muted-foreground">
                  I agree to the{' '}
                  {cat?.terms_url ? <a href={cat.terms_url} target="_blank" rel="noreferrer" className="underline">terms of service</a> : 'terms of service'}
                  {' '}and{' '}
                  {cat?.privacy_url ? <a href={cat.privacy_url} target="_blank" rel="noreferrer" className="underline">privacy policy</a> : 'privacy policy'}.
                  Callers are told they're speaking with a virtual assistant.
                </span>
              </label>
              {error ? <ErrorBanner error={error} /> : null}
              <Button type="submit" size="lg" className="w-full" disabled={busy || !accept || !selfServe.length}>
                {busy && <Loader2 className="animate-spin" />}
                {cat?.billing_enabled === false ? `Start ${cat.trial.days}-day pilot` : 'Continue to payment'}
              </Button>
            </form>
          </CardContent>
        </Card>
      </main>
    </SignupShell>
  )
}

function SignupShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-sidebar text-sidebar-foreground">
      <header className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-5 sm:px-6">
        <LaudlineLogo className="h-6 text-sidebar-foreground" />
        <p className="text-sm text-sidebar-foreground/70">
          <span className="hidden sm:inline">Already have an account? </span>
          <Link to="/login" className="font-medium text-sidebar-foreground hover:underline">Sign in</Link>
        </p>
      </header>
      {children}
    </div>
  )
}

/** One plan on the first step. Choosing it moves straight on to the account form. */
function PlanCard({ plan: p, annual, highlighted, onChoose }: {
  plan: PlanInfo
  annual: boolean
  highlighted: boolean
  onChoose: () => void
}) {
  return (
    <div className={cn('flex flex-col rounded-xl border p-6',
      highlighted ? 'border-sidebar-primary bg-sidebar-accent' : 'border-sidebar-border bg-sidebar-accent/40')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">{p.name}</h2>
        {p.recommended && (
          <span className="rounded-full bg-sidebar-primary/15 px-2 py-0.5 text-xs font-medium text-sidebar-primary">Recommended</span>
        )}
      </div>
      <p className="mt-1 min-h-10 text-sm text-pretty text-sidebar-foreground/70">{p.tagline}</p>
      <div className="mt-4 text-3xl font-semibold tabular-nums">
        {formatPrice(annual ? p.annual / 12 : p.monthly, p.currency)}
        <span className="text-sm font-normal text-sidebar-foreground/60">/mo</span>
      </div>
      {annual && <div className="mt-1 text-xs text-sidebar-foreground/60">{formatPrice(p.annual, p.currency)} billed yearly</div>}
      {p.setup_fee_required && p.setup_fee ? (
        <div className="mt-1 text-xs text-sidebar-foreground/60">+ {formatPrice(p.setup_fee, p.currency)} one-time onboarding</div>
      ) : null}
      <ul className="mt-5 mb-6 space-y-2 text-sm text-sidebar-foreground/85">
        {p.features.map((f) => (
          <li key={f} className="flex gap-2"><Check className="mt-0.5 size-4 shrink-0 text-sidebar-primary" /> {f}</li>
        ))}
      </ul>
      <Button size="lg" onClick={onChoose}
        className={cn('mt-auto w-full', highlighted
          ? 'bg-sidebar-primary text-sidebar-primary-foreground hover:bg-sidebar-primary/85'
          : 'bg-sidebar-foreground/10 text-sidebar-foreground hover:bg-sidebar-foreground/15')}>
        Choose {p.name}
      </Button>
    </div>
  )
}

/** Landing page for the link in the verification e-mail. Works signed in or out. */
export function VerifyEmail() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [state, setState] = useState<'working' | 'done' | 'error'>('working')
  const [error, setError] = useState<unknown>(null)
  const sent = useRef(false)

  useEffect(() => {
    if (sent.current) return
    sent.current = true
    api('/signup/verify-email', { method: 'POST', auth: false, body: { token } })
      .then(() => setState('done'))
      .catch((err) => {
        setError(err)
        setState('error')
      })
  }, [token])

  return (
    <AuthCard title="Confirm your e-mail" description="One moment…">
      {state === 'working' && <Loader2 className="mx-auto size-6 animate-spin text-muted-foreground" />}
      {state === 'done' && (
        <div className="space-y-4 text-center text-sm">
          <CheckCircle2 className="mx-auto size-8 text-emerald-600" />
          <p>Thanks — your e-mail is confirmed.</p>
          <Button className="w-full" nativeButton={false} render={<Link to="/" />}>Open the dashboard</Button>
        </div>
      )}
      {state === 'error' && (
        <div className="space-y-4">
          <ErrorBanner error={error} />
          <Button variant="outline" className="w-full" nativeButton={false} render={<Link to="/login" />}>Sign in</Button>
        </div>
      )}
    </AuthCard>
  )
}
