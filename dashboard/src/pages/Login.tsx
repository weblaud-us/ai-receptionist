import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { CheckCircle2, Headset, Loader2 } from 'lucide-react'
import { useAuth } from '@/auth/auth'
import { ErrorBanner } from '@/components/app/blocks'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api, API_BASE } from '@/lib/api'

function AuthCard({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-sidebar p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <div className="mb-2 flex size-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Headset className="size-6" />
          </div>
          <CardTitle className="text-xl">{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>
    </div>
  )
}

export default function Login() {
  const { status, login, verifyMfa } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  const [code, setCode] = useState('')

  if (status === 'authenticated') return <Navigate to={from} replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mfaToken) {
        await verifyMfa(mfaToken, code.replace(/s/g, ''))
        navigate(from, { replace: true })
      } else {
        const challenge = await login(email.trim(), password)
        if (challenge) setMfaToken(challenge)
        else navigate(from, { replace: true })
      }
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  if (mfaToken) {
    return (
      <AuthCard title="Two-factor code" description="Enter the 6-digit code from your authenticator app.">
        <form onSubmit={onSubmit} className="space-y-4">
          <Input autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code}
            onChange={(e) => setCode(e.target.value)} className="text-center text-lg tracking-widest" />
          <ErrorBanner error={error} />
          <Button type="submit" className="w-full" size="lg" disabled={busy || code.replace(/s/g, '').length !== 6}>
            {busy && <Loader2 className="animate-spin" />} Verify
          </Button>
          <Button type="button" variant="ghost" className="w-full" onClick={() => { setMfaToken(null); setCode('') }}>Back</Button>
        </form>
      </AuthCard>
    )
  }

  return (
    <AuthCard title="AI Receptionist" description="Sign in to manage your calls, bookings and assistant.">
      <form onSubmit={onSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="email">E-mail</Label>
          <Input id="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="password">Password</Label>
            <Link to="/forgot-password" className="text-xs text-muted-foreground hover:underline">
              Forgot password?
            </Link>
          </div>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <ErrorBanner error={error} />
        <Button type="submit" className="w-full" size="lg" disabled={busy}>
          {busy && <Loader2 className="animate-spin" />}
          Sign in
        </Button>
        <p className="text-center text-xs text-muted-foreground">API: {API_BASE}</p>
      </form>
    </AuthCard>
  )
}

export function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api('/auth/password/forgot', { method: 'POST', body: { email: email.trim() }, auth: false })
      setSent(true)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthCard title="Reset your password" description="We'll e-mail you a link to choose a new one.">
      {sent ? (
        <div className="space-y-4 text-center text-sm">
          <CheckCircle2 className="mx-auto size-8 text-emerald-600" />
          <p>If an account exists for {email}, a reset link is on its way. It works for one hour.</p>
          <Button variant="outline" className="w-full" nativeButton={false} render={<Link to="/login" />}>
            Back to sign in
          </Button>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="email">E-mail</Label>
            <Input id="email" type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <ErrorBanner error={error} />
          <Button type="submit" className="w-full" disabled={busy}>
            {busy && <Loader2 className="animate-spin" />} Send reset link
          </Button>
        </form>
      )}
    </AuthCard>
  )
}

function NewPasswordForm({
  submitLabel,
  withName,
  onSubmit,
}: {
  submitLabel: string
  withName?: boolean
  onSubmit: (password: string, name: string) => Promise<void>
}) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const mismatch = confirm.length > 0 && confirm !== password

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (mismatch || password.length < 10) return
    setBusy(true)
    setError(null)
    try {
      await onSubmit(password, name.trim())
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      {withName && (
        <div className="space-y-1.5">
          <Label htmlFor="name">Your name</Label>
          <Input id="name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="pw">New password</Label>
        <Input id="pw" type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        <p className="text-xs text-muted-foreground">At least 10 characters.</p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="pw2">Repeat it</Label>
        <Input id="pw2" type="password" autoComplete="new-password" required value={confirm} aria-invalid={mismatch}
          onChange={(e) => setConfirm(e.target.value)} />
        {mismatch && <p className="text-xs text-destructive">The passwords don't match.</p>}
      </div>
      <ErrorBanner error={error} />
      <Button type="submit" className="w-full" disabled={busy || mismatch || password.length < 10}>
        {busy && <Loader2 className="animate-spin" />} {submitLabel}
      </Button>
    </form>
  )
}

export function ResetPassword() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [done, setDone] = useState(false)
  return (
    <AuthCard title="Choose a password" description="Set the password you'll use to sign in.">
      {done ? (
        <div className="space-y-4 text-center text-sm">
          <CheckCircle2 className="mx-auto size-8 text-emerald-600" />
          <p>Your password is set.</p>
          <Button className="w-full" nativeButton={false} render={<Link to="/login" />}>Sign in</Button>
        </div>
      ) : (
        <NewPasswordForm
          submitLabel="Save password"
          onSubmit={async (password) => {
            await api('/auth/password/reset', { method: 'POST', body: { token, password }, auth: false })
            setDone(true)
          }}
        />
      )}
    </AuthCard>
  )
}

export function AcceptInvite() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [email, setEmail] = useState<string | null>(null)
  return (
    <AuthCard title="Join your team" description="Set up your account to manage the business's AI receptionist.">
      {email ? (
        <div className="space-y-4 text-center text-sm">
          <CheckCircle2 className="mx-auto size-8 text-emerald-600" />
          <p>You're in. Sign in as {email}.</p>
          <Button className="w-full" nativeButton={false} render={<Link to="/login" />}>Sign in</Button>
        </div>
      ) : (
        <NewPasswordForm
          withName
          submitLabel="Accept invitation"
          onSubmit={async (password, name) => {
            const r = await api<{ email: string }>('/auth/invite/accept', { method: 'POST', body: { token, password, name }, auth: false })
            setEmail(r.email)
          }}
        />
      )}
    </AuthCard>
  )
}
