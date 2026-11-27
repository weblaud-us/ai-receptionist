/**
 * "Launch this business": what's left before a new practice is live, from what the details page
 * already loads plus the getting-started signals. Not dismissable: it shrinks to one line when done.
 */
import { CheckCircle2, Rocket } from 'lucide-react'
import { ChecklistCard, type ChecklistItem } from '@receptionist/shared/components/checklist-card'
import { Card, CardContent } from '@receptionist/shared/components/ui/card'
import { impersonateUrl } from '@receptionist/shared/lib/urls'
import type { TenantDetails } from '@/lib/platform'

type Tab = 'team' | 'numbers' | 'controls'

// eslint-disable-next-line react-refresh/only-export-components
export function launchItems(data: TenantDetails, onTab: (tab: Tab) => void): ChecklistItem[] {
  const t = data.tenant
  const s = data.signals
  const owner = data.members.find((m) => m.role === 'owner' && !m.disabled)
  const invitedOwner = data.invites.some((i) => i.role === 'owner')
  const openAsOwner = { label: 'Open as owner', onClick: () => window.open(impersonateUrl(t.id), '_blank', 'noopener') }
  const subscription = t.billing?.subscription_status ?? ''
  return [
    { id: 'owner', label: 'Owner added', done: !!owner || invitedOwner,
      body: 'They get a link to set their password.', action: { label: 'Team', onClick: () => onTab('team') } },
    { id: 'owner_signed_in', label: 'Owner has signed in', done: !!owner && !owner.pending_password && !!owner.last_login_at,
      body: 'Still waiting? Send the set-password link again from Team.', action: { label: 'Team', onClick: () => onTab('team') } },
    { id: 'number', label: 'Number live', done: data.numbers.some((n) => (n.status ?? 'active') === 'active'),
      body: 'Buy or assign the number calls are forwarded to.', action: { label: 'Numbers', onClick: () => onTab('numbers') } },
    { id: 'setup', label: 'Setup wizard finished', done: !!t.onboarding?.completed_at,
      body: 'Hours, treatments, transfers and a voice.', action: openAsOwner, learnMore: 'launch-a-business' },
    { id: 'test_call', label: 'Test call made', done: !!s?.test_call,
      body: 'A browser test call from the Assistant page.', action: openAsOwner },
    { id: 'first_call', label: 'First real call answered', done: !!s?.first_call,
      body: 'Forwarding is on and a patient got through. Recent calls are below.' },
    { id: 'dpa', label: 'DPA accepted', done: !!t.legal?.dpa_accepted_at,
      body: 'The owner accepts it under Legal & privacy.' },
    { id: 'plan', label: 'Pilot or plan set', done: !!t.pilot?.active || t.status === 'active' || ['trialing', 'active'].includes(subscription),
      body: 'A manual pilot, or a paid plan through Stripe.', action: { label: 'Controls', onClick: () => onTab('controls') } },
  ]
}

export function LaunchChecklist({ data, onTab }: { data: TenantDetails; onTab: (tab: Tab) => void }) {
  const items = launchItems(data, onTab)
  if (items.every((i) => i.done)) {
    return (
      <Card size="sm">
        <CardContent className="flex items-center gap-2 text-sm">
          <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" /> Launched. Every step is done.
        </CardContent>
      </Card>
    )
  }
  return (
    <ChecklistCard
      title="Launch this business"
      description={<span className="inline-flex items-center gap-1.5"><Rocket className="size-3.5" /> What's left before it's answering real calls. Each step ticks itself off.</span>}
      items={items}
    />
  )
}
