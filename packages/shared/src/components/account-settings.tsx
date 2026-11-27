/** Your own account: name, e-mail, password, two-factor, signed-in devices and appearance.
 * Shared by the client dashboard and the platform admin app (route: /account). */
import { useState, type FormEvent, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Info, KeyRound, Laptop, Loader2, LogOut, Mail, Moon, Palette, ShieldCheck, Smartphone, Sun, UserRound } from 'lucide-react'
import { useAuth } from '../auth/auth'
import { refreshSession } from '../lib/api'
import { useAccount, useAccountActions, useSessions, type AccountSession } from '../lib/auth-hooks'
import { timeAgo } from '../lib/format'
import { cn } from '../lib/utils'
import { useTheme } from '../providers/theme'
import { ConfirmDialog, ErrorBanner, Field, PageHeader } from './blocks'
import { SecurityCard } from './security-card'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Card, CardContent } from './ui/card'
import { Input } from './ui/input'
import { Separator } from './ui/separator'
import { Skeleton } from './ui/skeleton'

const MIN_PASSWORD = 10 // matches core/auth/passwords.py

function Section({ id, icon: Icon, title, description, children }: {
  id: string
  icon: typeof UserRound
  title: string
  description: ReactNode
  children: ReactNode
}) {
  return (
    <section id={id} className="grid gap-4 py-8 first:pt-0 lg:grid-cols-[16rem_1fr] lg:gap-10">
      <div>
        <h2 className="flex items-center gap-2 text-base font-semibold"><Icon className="size-4 text-muted-foreground" /> {title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  )
}

/** Keep the sidebar's name in step: a refresh re-reads the account and re-issues the token. */
function useReloadSession() {
  const { applyTokens } = useAuth()
  return async () => {
    const res = await refreshSession()
    if (res?.access_token) applyTokens(res)
  }
}

function ProfileCard({ name: saved }: { name: string }) {
  const { updateName } = useAccountActions()
  const reload = useReloadSession()
  const [name, setName] = useState(saved)
  const dirty = name.trim() !== saved && name.trim().length > 0

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    updateName.mutate(name.trim(), {
      onSuccess: async () => {
        await reload()
        toast.success('Name updated')
      },
    })
  }

  return (
    <Card>
      <CardContent>
        <form className="space-y-4" onSubmit={onSubmit}>
          <Field label="Full name" hint="Shown in the sidebar and to your team.">
            <Input value={name} maxLength={120} autoComplete="name" onChange={(e) => setName(e.target.value)} />
          </Field>
          <ErrorBanner error={updateName.error} />
          <Button type="submit" size="sm" disabled={!dirty || updateName.isPending}>
            {updateName.isPending && <Loader2 className="animate-spin" />} Save name
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}

function EmailCard({ email, pending, verified }: { email: string; pending: string | null; verified: boolean }) {
  const { changeEmail, cancelEmailChange } = useAccountActions()
  const [open, setOpen] = useState(false)
  const [newEmail, setNewEmail] = useState('')
  const [password, setPassword] = useState('')

  function close() {
    setOpen(false)
    setNewEmail('')
    setPassword('')
    changeEmail.reset()
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    changeEmail.mutate({ new_email: newEmail.trim(), password }, {
      onSuccess: (res) => {
        close()
        toast.success(res.emailed ? `Check ${res.pending_email} for a confirmation link` : 'Confirmation link written to the server log (e-mail isn\'t set up here)')
      },
    })
  }

  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-xs text-muted-foreground">You sign in with</div>
            <div className="flex items-center gap-2 truncate font-medium">
              {email}
              {verified ? <Badge variant="secondary">Verified</Badge> : <Badge variant="outline">Not verified</Badge>}
            </div>
          </div>
          {!open && <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Change e-mail</Button>}
        </div>

        {pending && !open && (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
            <Mail className="size-4 shrink-0 text-amber-600" />
            <div className="min-w-0 flex-1">
              Waiting for you to confirm <span className="font-medium">{pending}</span>. Open the link we sent there. Until then you keep signing in with {email}.
            </div>
            <Button size="xs" variant="ghost" disabled={cancelEmailChange.isPending}
              onClick={() => cancelEmailChange.mutate(undefined, { onSuccess: () => toast.success('E-mail change cancelled') })}>
              Cancel change
            </Button>
          </div>
        )}

        {open && (
          <form className="space-y-4" onSubmit={onSubmit}>
            <Separator />
            <Field label="New e-mail" hint="We'll send a link there. Your e-mail changes when you open it.">
              <Input type="email" required autoComplete="email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
            </Field>
            <Field label="Current password">
              <Input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <ErrorBanner error={changeEmail.error} />
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={!newEmail.trim() || !password || changeEmail.isPending}>
                {changeEmail.isPending && <Loader2 className="animate-spin" />} Send confirmation link
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={close}>Cancel</Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  )
}

function PasswordCard({ changedAt }: { changedAt: string | null }) {
  const { changePassword } = useAccountActions()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const tooShort = next.length > 0 && next.length < MIN_PASSWORD
  const mismatch = confirm.length > 0 && confirm !== next
  const ready = current && next.length >= MIN_PASSWORD && next === confirm

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    changePassword.mutate({ current_password: current, new_password: next }, {
      onSuccess: (res) => {
        setCurrent('')
        setNext('')
        setConfirm('')
        toast.success(res.signed_out
          ? `Password changed. ${res.signed_out} other ${res.signed_out === 1 ? 'device was' : 'devices were'} signed out.`
          : 'Password changed')
      },
    })
  }

  return (
    <Card>
      <CardContent>
        <form className="space-y-4" onSubmit={onSubmit}>
          {/* Lets password managers file the new password under the right account. */}
          <input type="text" name="username" autoComplete="username" hidden readOnly />
          <Field label="Current password">
            <Input type="password" required autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="New password" hint={tooShort ? `At least ${MIN_PASSWORD} characters` : `${MIN_PASSWORD}+ characters. A short phrase works well.`}>
              <Input type="password" required autoComplete="new-password" value={next} aria-invalid={tooShort || undefined}
                onChange={(e) => setNext(e.target.value)} />
            </Field>
            <Field label="Confirm new password" hint={mismatch ? "Doesn't match" : undefined}>
              <Input type="password" required autoComplete="new-password" value={confirm} aria-invalid={mismatch || undefined}
                onChange={(e) => setConfirm(e.target.value)} />
            </Field>
          </div>
          <ErrorBanner error={changePassword.error} />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button type="submit" size="sm" disabled={!ready || changePassword.isPending}>
              {changePassword.isPending && <Loader2 className="animate-spin" />} Change password
            </Button>
            <span className="text-xs text-muted-foreground">{changedAt ? `Last changed ${timeAgo(changedAt)}` : 'Other devices are signed out when you change it.'}</span>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

/** "Chrome on Windows" from a user-agent string; good enough to recognise your own devices. */
function describeDevice(ua: string): { label: string; mobile: boolean } {
  if (!ua) return { label: 'Unknown device', mobile: false }
  const browser = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : ''
  const os = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows'
    : /Mac OS X|Macintosh/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : ''
  const mobile = /Mobi|iPhone|Android/.test(ua)
  if (browser && os) return { label: `${browser} on ${os}`, mobile }
  return { label: browser || os || ua.slice(0, 60), mobile }
}

function SessionRow({ s, onRevoke, busy }: { s: AccountSession; onRevoke: () => void; busy: boolean }) {
  const device = describeDevice(s.user_agent)
  const Icon = device.mobile ? Smartphone : Laptop
  return (
    <li className="flex items-center gap-3 py-3">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted"><Icon className="size-4 text-muted-foreground" /></div>
      <div className="min-w-0 flex-1 text-sm">
        <div className="flex flex-wrap items-center gap-2 font-medium">
          {device.label}
          {s.current && <Badge>This device</Badge>}
          {s.mfa && <Badge variant="outline">Two-factor</Badge>}
        </div>
        <div className="text-xs text-muted-foreground">
          Signed in {timeAgo(s.signed_in_at)} · {s.current ? 'active now' : `last active ${timeAgo(s.last_active_at)}`}
        </div>
      </div>
      {!s.current && (
        <Button size="sm" variant="ghost" disabled={busy} onClick={onRevoke}>Sign out</Button>
      )}
    </li>
  )
}

function SessionsCard() {
  const sessions = useSessions()
  const { revokeSession, revokeOthers } = useAccountActions()
  const [confirmAll, setConfirmAll] = useState(false)
  const others = (sessions.data ?? []).filter((s) => !s.current).length

  return (
    <Card>
      <CardContent className="space-y-2">
        {sessions.isLoading && <Skeleton className="h-24 w-full" />}
        <ErrorBanner error={sessions.error} onRetry={() => void sessions.refetch()} />
        {sessions.data && (
          <ul className="divide-y">
            {sessions.data.map((s) => (
              <SessionRow key={s.id} s={s} busy={revokeSession.isPending}
                onRevoke={() => revokeSession.mutate(s.id, { onSuccess: () => toast.success('Signed out of that device') })} />
            ))}
            {sessions.data.length === 0 && <li className="py-3 text-sm text-muted-foreground">No saved sessions.</li>}
          </ul>
        )}
        <ErrorBanner error={revokeSession.error ?? revokeOthers.error} />
        <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
          <span className="text-xs text-muted-foreground">A device you sign out may stay open for up to 15 minutes.</span>
          <Button size="sm" variant="outline" disabled={others === 0} onClick={() => setConfirmAll(true)}>
            <LogOut /> Sign out other devices
          </Button>
        </div>
      </CardContent>
      <ConfirmDialog
        open={confirmAll}
        onOpenChange={setConfirmAll}
        title="Sign out every other device?"
        description={`${others} other ${others === 1 ? 'session' : 'sessions'} will need to sign in again. You stay signed in here.`}
        confirmLabel="Sign them out"
        destructive
        loading={revokeOthers.isPending}
        onConfirm={() => revokeOthers.mutate(undefined, {
          onSuccess: (res) => { setConfirmAll(false); toast.success(`Signed out ${res.signed_out} ${res.signed_out === 1 ? 'device' : 'devices'}`) },
        })}
      />
    </Card>
  )
}

function AppearanceCard() {
  const { theme, setTheme } = useTheme()
  const options = [
    { value: 'light' as const, label: 'Light', icon: Sun },
    { value: 'dark' as const, label: 'Dark', icon: Moon },
  ]
  return (
    <Card>
      <CardContent>
        <div className="grid max-w-sm grid-cols-2 gap-3" role="radiogroup" aria-label="Theme">
          {options.map(({ value, label, icon: Icon }) => (
            <button key={value} type="button" role="radio" aria-checked={theme === value} onClick={() => setTheme(value)}
              className={cn('flex items-center justify-center gap-2 rounded-lg border px-3 py-3 text-sm font-medium transition-colors',
                theme === value ? 'border-primary bg-primary/5 text-foreground ring-1 ring-primary' : 'text-muted-foreground hover:bg-muted')}>
              <Icon className="size-4" /> {label}
            </button>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted-foreground">Saved in this browser.</p>
      </CardContent>
    </Card>
  )
}

export function AccountSettingsPage() {
  const { user } = useAuth()
  const account = useAccount()
  const data = account.data

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Account settings" description="Your own sign-in details. They follow you across every business you can open." />
      <ErrorBanner error={account.error} onRetry={() => void account.refetch()} />
      {account.isLoading && <div className="space-y-4"><Skeleton className="h-32 w-full" /><Skeleton className="h-32 w-full" /></div>}

      {data?.legacy && (
        <div className="mb-6 flex gap-3 rounded-lg border bg-muted/40 p-4 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div>
            <div className="font-medium">You're signed in as the bootstrap admin from the server's <code>.env</code> file</div>
            <p className="mt-1 text-muted-foreground">
              Its name, e-mail and password live in <code>ADMIN_USERNAME</code> / <code>ADMIN_PASSWORD</code>, so they can't be changed here.
              Create a real admin account on the server with <code>uv run python -m scripts.create_admin --email you@company.com --name "Your Name"</code>,
              sign in with it, and you'll get the full settings, including two-factor.
            </p>
          </div>
        </div>
      )}

      {data && (
        <div className="divide-y">
          {!data.legacy && (
            <>
              <Section id="profile" icon={UserRound} title="Profile" description="How you appear in LaudLine.">
                <ProfileCard key={data.name} name={data.name} />
              </Section>
              <Section id="email" icon={Mail} title="E-mail" description="The address you sign in with and where we send security alerts.">
                <EmailCard email={data.email} pending={data.pending_email} verified={!!user?.email_verified} />
              </Section>
              <Section id="password" icon={KeyRound} title="Password" description="You'll need your current password. Changing it signs out your other devices.">
                <PasswordCard changedAt={data.password_changed_at} />
              </Section>
            </>
          )}
          <Section id="two-factor" icon={ShieldCheck} title="Two-factor sign-in" description="A code from your phone at every sign-in, so a stolen password isn't enough.">
            <SecurityCard />
          </Section>
          {!data.legacy && (
            <Section id="sessions" icon={Laptop} title="Where you're signed in" description="Browsers and devices with an active session. Sign out any you don't recognise.">
              <SessionsCard />
            </Section>
          )}
          <Section id="appearance" icon={Palette} title="Appearance" description="Light or dark.">
            <AppearanceCard />
          </Section>
          {!data.legacy && data.created_at && (
            <p className="py-6 text-xs text-muted-foreground">
              Account created {timeAgo(data.created_at)}{data.last_login_at && ` · last password sign-in ${timeAgo(data.last_login_at)}`}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
