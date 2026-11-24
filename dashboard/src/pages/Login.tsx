import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CheckCircle2 } from 'lucide-react'
import { AuthCard, LoginPage, NewPasswordForm } from '@receptionist/shared/auth/login-pages'
import { Button } from '@receptionist/shared/components/ui/button'
import { api } from '@receptionist/shared/lib/api'

export { ForgotPassword, ResetPassword } from '@receptionist/shared/auth/login-pages'

export default function Login() {
  return (
    <LoginPage
      title="Sign in"
      description="Sign in to manage your calls, bookings and assistant."
      footer={
        <p className="text-center text-sm text-muted-foreground">
          New here? <Link to="/signup" className="font-medium text-foreground hover:underline">Start a 14-day pilot</Link>
        </p>
      }
    />
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
