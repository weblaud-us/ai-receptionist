import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { Check, CheckCircle2, Headset, Loader2 } from 'lucide-react'
import { useAuth } from '@receptionist/shared/auth/auth'
import { ErrorBanner, Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent } from '@receptionist/shared/components/ui/card'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Input } from '@receptionist/shared/components/ui/input'
import { api, type TokenResponse } from '@receptionist/shared/lib/api'
import { formatPrice, usePlans } from '@/lib/billing'
import { cn } from '@receptionist/shared/lib/utils'
import { AuthCard } from '@receptionist/shared/auth/login-pages'

const BUSINESS_TYPES = [
  { value: 'home_services', label: 'Home services (plumbing, HVAC, electrical…)' },
  { value: 'salon', label: 'Salon, barber or spa' },
  { value: 'clinic', label: 'Clinic, dental or physio' },
  { value: 'restaurant', label: 'Restaurant or café' },
  { value: 'professional', label: 'Professional services (law, accounting…)' },
  { value: 'hotel', label: 'Hotel or guesthouse' },
  { value: 'generic', label: 'Something else' },
]

// Launch markets first (English-speaking: Australia, UK, Ireland, Malta).
const COUNTRIES = [
  { value: 'AU', label: 'Australia' },
  { value: 'GB', label: 'United Kingdom' },
  { value: 'IE', label: 'Ireland' },
  { value: 'MT', label: 'Malta' },
  { value: 'US', label: 'United States' },
  { value: 'CA', label: 'Canada' },
  { value: 'DE', label: 'Germany' },
  { value: 'FR', label: 'France' },
  { value: 'ES', label: 'Spain' },
  { value: 'NL', label: 'Netherlands' },
  { value: 'AE', label: 'United Arab Emirates' },
  { value: 'SA', label: 'Saudi Arabia' },
]

/** A first guess from the browser's time zone; the visitor can change it. */
function guessCountry(tz: string): string {
  if (tz.startsWith('Australia/')) return 'AU'
  const byZone: Record<string, string> = {
    'Europe/London': 'GB', 'Europe/Belfast': 'GB', 'Europe/Dublin': 'IE', 'Europe/Malta': 'MT',
    'Europe/Berlin': 'DE', 'Europe/Paris': 'FR', 'Europe/Madrid': 'ES', 'Europe/Amsterdam': 'NL',
    'Asia/Dubai': 'AE', 'Asia/Riyadh': 'SA',
  }
  if (byZone[tz]) return byZone[tz]
  if (tz.startsWith('America/')) return /Toronto|Vancouver|Edmonton|Winnipeg|Halifax|Regina|St_Johns/.test(tz) ? 'CA' : 'US'
  return 'GB'
}

function guessTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York'
  } catch {
    return 'America/New_York'
  }
}

interface SignupResponse extends TokenResponse {
  next: 'checkout' | 'onboarding'
  verification_emailed: boolean
}

export default function Signup() {
  const { status, applyTokens } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [plan, setPlan] = useState(params.get('plan') ?? 'growth')
  const [annual, setAnnual] = useState(params.get('interval') === 'year')
  const [form, setForm] = useState(() => ({
    name: '', email: '', password: '', business_name: '', vertical: 'home_services',
    country: (params.get('country') ?? '').toUpperCase() || guessCountry(guessTimezone()),
  }))
  const plans = usePlans(form.country)
  const [accept, setAccept] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }))

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

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await api<SignupResponse>('/signup', {
        method: 'POST', auth: false,
        body: { ...form, email: form.email.trim(), plan, interval: annual ? 'year' : 'month', timezone: guessTimezone(), accept_terms: accept },
      })
      applyTokens(res)
      if (res.next === 'checkout') {
        // Straight to Stripe Checkout: card first (trial included), then the setup wizard.
        const checkout = await api<{ url: string }>('/t/billing/checkout', { method: 'POST', body: { plan, interval: annual ? 'year' : 'month' } })
        window.location.assign(checkout.url)
        return
      }
      navigate('/onboarding', { replace: true })
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-sidebar px-4 py-10">
      <div className="mx-auto max-w-5xl">
        <div className="mb-8 flex flex-col items-center text-center text-sidebar-foreground">
          <div className="mb-3 flex size-12 items-center justify-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground">
            <Headset className="size-6" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">An AI receptionist that answers every call</h1>
          <p className="mt-2 max-w-xl text-sm text-sidebar-foreground/70">
            {cat ? `${cat.trial.days}-day free trial (${cat.trial.minutes} minutes). Card required, cancel any time. ` : ''}
            Live in about 15 minutes.
          </p>
        </div>

        <div className="mb-4 flex justify-center">
          <div className="inline-flex rounded-full bg-sidebar-accent p-1 text-sm">
            {[false, true].map((a) => (
              <button key={String(a)} type="button" onClick={() => setAnnual(a)}
                className={cn('rounded-full px-4 py-1.5 font-medium text-sidebar-foreground/70 transition-colors',
                  annual === a && 'bg-background text-foreground shadow-sm')}>
                {a ? `Yearly — ${cat?.annual_months_free ?? 2} months free` : 'Monthly'}
              </button>
            ))}
          </div>
        </div>

        <div className="mb-8 grid gap-3 md:grid-cols-3">
          {plans.isLoading && [0, 1, 2].map((n) => <div key={n} className="h-56 animate-pulse rounded-xl bg-sidebar-accent" />)}
          {selfServe.map((p) => (
            <button key={p.key} type="button" onClick={() => setPlan(p.key)}
              className={cn('rounded-xl border bg-card p-5 text-left text-card-foreground transition-shadow',
                plan === p.key ? 'border-primary ring-2 ring-primary/40' : 'hover:shadow-md')}>
              <div className="flex items-center justify-between">
                <span className="font-semibold">{p.name}</span>
                {p.recommended && <Badge>Most popular</Badge>}
              </div>
              <div className="mt-2 text-3xl font-semibold tabular-nums">
                {formatPrice(annual ? p.annual / 12 : p.monthly, p.currency)}
                <span className="text-sm font-normal text-muted-foreground">/mo</span>
              </div>
              {annual && <div className="text-xs text-muted-foreground">{formatPrice(p.annual, p.currency)} billed yearly</div>}
              <p className="mt-2 text-sm text-muted-foreground">{p.tagline}</p>
              <ul className="mt-3 space-y-1 text-sm">
                {p.features.map((f) => (
                  <li key={f} className="flex gap-2"><Check className="mt-0.5 size-4 shrink-0 text-primary" /> {f}</li>
                ))}
              </ul>
            </button>
          ))}
        </div>
        {cat && (
          <p className="-mt-5 mb-8 text-center text-xs text-sidebar-foreground/60">
            Prices exclude VAT/GST. Extra minutes {formatPrice(cat.overage_per_min, cat.currency, true)} each — or set a hard cap. Agencies and chains: contact us.
          </p>
        )}

        <Card className="mx-auto max-w-xl">
          <CardContent className="pt-6">
            <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-2">
              <Field label="Your name"><Input autoComplete="name" value={form.name} onChange={(e) => set('name')(e.target.value)} /></Field>
              <Field label="Work e-mail"><Input type="email" autoComplete="email" required value={form.email} onChange={(e) => set('email')(e.target.value)} /></Field>
              <Field label="Password" className="sm:col-span-2" hint="At least 10 characters.">
                <Input type="password" autoComplete="new-password" required minLength={10} value={form.password} onChange={(e) => set('password')(e.target.value)} />
              </Field>
              <Field label="Business name" className="sm:col-span-2">
                <Input required value={form.business_name} onChange={(e) => set('business_name')(e.target.value)} />
              </Field>
              <Field label="Type of business"><SimpleSelect value={form.vertical} onChange={set('vertical')} options={BUSINESS_TYPES} /></Field>
              <Field label="Country"><SimpleSelect value={form.country} onChange={set('country')} options={COUNTRIES} /></Field>
              <label className="flex items-start gap-2 text-sm sm:col-span-2">
                <Checkbox checked={accept} onCheckedChange={(v) => setAccept(v === true)} className="mt-0.5" />
                <span className="text-muted-foreground">
                  I agree to the{' '}
                  {cat?.terms_url ? <a href={cat.terms_url} target="_blank" rel="noreferrer" className="underline">terms of service</a> : 'terms of service'}
                  {' '}and{' '}
                  {cat?.privacy_url ? <a href={cat.privacy_url} target="_blank" rel="noreferrer" className="underline">privacy policy</a> : 'privacy policy'}.
                  Callers are told they're speaking with a virtual assistant.
                </span>
              </label>
              <div className="sm:col-span-2"><ErrorBanner error={error} /></div>
              <Button type="submit" size="lg" className="sm:col-span-2" disabled={busy || !accept || !selfServe.length}>
                {busy && <Loader2 className="animate-spin" />}
                {cat?.billing_enabled === false ? 'Start free trial' : 'Continue to payment'}
              </Button>
              <p className="text-center text-sm text-muted-foreground sm:col-span-2">
                Already have an account? <Link to="/login" className="font-medium text-foreground hover:underline">Sign in</Link>
              </p>
            </form>
          </CardContent>
        </Card>
      </div>
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
