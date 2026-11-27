/** The overview's getting-started list for owners and admins: each step ticks itself off. */
import { ChecklistCard, type ChecklistItem } from '@receptionist/shared/components/checklist-card'
import { useTutorials } from '@receptionist/shared/hooks/use-tutorials'
import { useGettingStarted } from '@/lib/tenant-hooks'
import type { GettingStartedId, TenantContext } from '@/lib/tenant-types'
import { checklistKey } from '@/tours/use-auto-tour'

type Copy = Omit<ChecklistItem, 'id' | 'done'>

function copy(ctx: TenantContext): Record<GettingStartedId, Copy> {
  const dental = ctx.vertical.key === 'dental'
  const services = dental ? 'treatments' : ctx.vertical.labels.services.toLowerCase()
  return {
    setup: { label: 'Set up your assistant', body: `Hours, ${services}, transfers and a voice. About ten minutes.`,
      action: { label: 'Continue setup', to: '/onboarding' }, learnMore: 'getting-started' },
    test_call: { label: 'Talk to it', body: dental ? 'Make a test call in your browser. Ask a price, book a check-up, ask for a person.' : 'Make a test call in your browser. Ask a price, book something, ask for a person.',
      action: { label: 'Test call', to: '/assistant' } },
    number: { label: "Get your assistant's number", body: 'A local number your calls are forwarded to.',
      action: { label: 'Phone numbers', to: '/phone' } },
    first_call: { label: 'Send it a real call', body: 'Forward your line, then ring your own number from another phone.',
      action: { label: 'How to forward', to: '/help/call-forwarding' } },
    knowledge: { label: 'Add an answer of your own', body: `Something only ${dental ? 'your practice' : 'you'} would know, like parking or payment plans.`,
      action: { label: 'Knowledge', to: '/knowledge' }, learnMore: 'knowledge' },
    calendar: { label: 'Connect your calendar', body: 'So it never books over a busy time.',
      action: { label: 'Connect', to: '/settings?tab=integrations' }, learnMore: 'calendar' },
    team: { label: 'Invite your team', body: dental ? 'Front desk and dentists each get their own sign-in.' : 'Everyone gets their own sign-in.',
      action: { label: 'Team', to: '/team' }, learnMore: 'team-and-roles' },
    dpa: { label: 'Accept the data processing agreement', body: "How we look after your callers' data for you.",
      action: { label: 'Legal & privacy', to: '/privacy' }, learnMore: 'caller-privacy' },
  }
}

/** True while the list shows (the overview then leaves out its older "finish setup" banner). */
// eslint-disable-next-line react-refresh/only-export-components
export function useGettingStartedVisible(ctx: TenantContext, can: (r: 'admin') => boolean): boolean {
  const tutorials = useTutorials()
  return can('admin') && !ctx.legacy && tutorials.ready && tutorials.status(checklistKey(ctx)) === undefined
}

export function GettingStartedCard({ ctx }: { ctx: TenantContext }) {
  const tutorials = useTutorials()
  const q = useGettingStarted()
  const data = q.data
  if (!data || !data.items.length) return null
  const text = copy(ctx)
  const items: ChecklistItem[] = data.items.map((i) => ({ id: i.id, done: i.done, ...text[i.id] }))
  return (
    <ChecklistCard
      data-tour="getting-started"
      className="mb-4"
      title="Getting started"
      description="A few steps to have your assistant answering real calls. Each one ticks itself off."
      doneBody="Your assistant is set up and answering. Help → Guides has more whenever you need it."
      items={items}
      onHide={() => tutorials.mark(checklistKey(ctx), data.complete ? 'done' : 'skipped')}
    />
  )
}
