import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2, ShieldCheck } from 'lucide-react'
import { useAuth } from '../auth/auth'
import { Field } from './blocks'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { useMfa } from '../lib/auth-hooks'

/** Your own sign-in security: TOTP two-factor setup (required for platform admins). */
export function SecurityCard() {
  const { user, applyTokens } = useAuth()
  const mfa = useMfa()
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null)
  const [code, setCode] = useState('')
  const [disabling, setDisabling] = useState(false)
  const enabled = !!user?.mfa_enabled
  const legacy = user?.id === 'env-admin' || user?.id === 'api-key'

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" /> Two-factor sign-in
          {enabled ? <Badge className="ml-auto">On</Badge> : <Badge variant="outline" className="ml-auto">Off</Badge>}
        </CardTitle>
        <CardDescription>
          A 6-digit code from an authenticator app at every sign-in.
          {user?.is_platform_admin && ' Required for platform administrators.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {legacy && <p className="text-sm text-muted-foreground">The bootstrap admin can't use two-factor. Create a real admin user and sign in with it.</p>}
        {!legacy && !enabled && !setup && (
          <Button size="sm" disabled={mfa.setup.isPending} onClick={() => mfa.setup.mutate(undefined, { onSuccess: setSetup })}>
            {mfa.setup.isPending && <Loader2 className="animate-spin" />} Set up two-factor
          </Button>
        )}
        {setup && (
          <div className="space-y-3 text-sm">
            <p>1. In your authenticator app, add an account with this key (or open the link on your phone):</p>
            <code className="block rounded bg-muted p-2 text-center font-mono tracking-wider break-all">{setup.secret.match(/.{1,4}/g)?.join(' ')}</code>
            <a className="block text-xs text-muted-foreground underline" href={setup.otpauth_uri}>Open in authenticator app</a>
            <Field label="2. Enter the 6-digit code it shows">
              <div className="flex gap-2">
                <Input inputMode="numeric" maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} />
                <Button disabled={code.replace(/\s/g, '').length !== 6 || mfa.enable.isPending}
                  onClick={() => mfa.enable.mutate(code.replace(/\s/g, ''), {
                    onSuccess: (res) => { applyTokens(res); setSetup(null); setCode(''); toast.success('Two-factor is on') },
                  })}>
                  {mfa.enable.isPending && <Loader2 className="animate-spin" />} Turn on
                </Button>
              </div>
            </Field>
          </div>
        )}
        {enabled && !disabling && <Button size="sm" variant="outline" onClick={() => setDisabling(true)}>Turn off</Button>}
        {enabled && disabling && (
          <Field label="Current code to confirm">
            <div className="flex gap-2">
              <Input inputMode="numeric" maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} />
              <Button variant="destructive" disabled={code.replace(/\s/g, '').length !== 6 || mfa.disable.isPending}
                onClick={() => mfa.disable.mutate(code.replace(/\s/g, ''), {
                  onSuccess: () => { toast.success('Two-factor is off — sign in again'); window.location.assign('/login') },
                })}>
                Turn off
              </Button>
            </div>
          </Field>
        )}
      </CardContent>
    </Card>
  )
}
