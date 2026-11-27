/**
 * Welcome tours for the practice dashboard, one per role. A step whose item this person's role or
 * business type doesn't have is skipped by the engine (its data-tour target isn't on the page).
 * Nav items are marked data-tour="nav-<path>" in components/app/layout.tsx.
 */
import type { TourDef, TourStep } from '@receptionist/shared/lib/tour'
import type { Role, TenantContext } from '@/lib/tenant-types'

type Step = TourStep<TenantContext>

/** Where a sidebar item is: itself on a big screen, the ☰ button on a phone. */
const nav = (path: string) => [`nav-${path}`, 'menu-button']

const help: Step = {
  id: 'help', target: 'help-menu', placement: 'bottom',
  title: 'Help is always here',
  body: 'Take this tour again, read the guides, or get in touch with us.',
}

const inbox: Step = {
  id: 'inbox', target: nav('inbox'),
  title: 'Inbox',
  body: 'Callbacks, booking requests and urgent messages that need a person. Overdue ones show a red count.',
  learnMore: 'inbox',
}

const calls: Step = {
  id: 'calls', target: nav('calls'),
  title: 'Calls & messages',
  body: 'Every call your assistant took, with a short summary and the full transcript.',
}

const knowledge: Step = {
  id: 'knowledge', target: nav('knowledge'),
  title: 'Knowledge',
  body: "What your assistant knows. Questions it couldn't answer show up here, so you only answer each one once.",
  learnMore: 'knowledge',
}

const assistant: Step = {
  id: 'assistant', target: nav('assistant'),
  title: 'Assistant',
  body: 'Its voice, greeting and hand-offs. Make a test call after any change.',
}

export const OWNER_TOUR: TourDef<TenantContext> = {
  key: 'tour:dashboard:owner',
  steps: [
    { id: 'welcome', title: 'Welcome to LaudLine', placement: 'center',
      body: (c) => `Your assistant answers calls for ${c.tenant.name}. Here's where everything is. It takes about a minute.` },
    { id: 'checklist', target: 'getting-started', route: '/', placement: 'bottom',
      title: 'Your getting-started list',
      body: 'Each step ticks itself off as you go. Do them in any order.' },
    inbox,
    calls,
    knowledge,
    assistant,
    { id: 'team', target: nav('team'), title: 'Team',
      body: 'Invite your front desk and dentists. Each person sees only what their role needs.', learnMore: 'team-and-roles' },
    { id: 'results', target: nav('results'), title: 'Results',
      body: 'Calls it caught and appointments it booked. A summary comes by e-mail each month.', learnMore: 'results' },
    help,
  ],
}

export const ADMIN_TOUR: TourDef<TenantContext> = {
  key: 'tour:dashboard:admin',
  steps: [
    { id: 'welcome', title: 'Welcome to LaudLine', placement: 'center',
      body: (c) => `You've joined ${c.tenant.name} as an admin. Here's what you'll look after.` },
    inbox,
    calls,
    knowledge,
    assistant,
    { id: 'settings', target: nav('settings'), title: 'Settings',
      body: 'Opening hours, transfers, and which calls reach your assistant.' },
    { id: 'services', target: nav('services'), title: (c) => (c.vertical.key === 'dental' ? 'Treatments & fees' : 'Services'),
      body: 'Prices live here, so the assistant always quotes them the same way.' },
    help,
  ],
}

export const STAFF_TOUR: TourDef<TenantContext> = {
  key: 'tour:dashboard:staff',
  steps: [
    { id: 'welcome', title: 'Welcome to LaudLine', placement: 'center',
      body: (c) => `You've joined ${c.tenant.name}. Here's what you'll use day to day.` },
    { ...inbox, body: 'Start here each morning. Assign an item to yourself, then mark it done once it\'s handled.' },
    { ...calls, body: 'Read the transcript before you ring someone back, so they don\'t have to repeat themselves.' },
    { id: 'appointments', target: nav('appointments'),
      title: (c) => (c.vertical.key === 'dental' ? 'Consultations & appointments' : c.vertical.labels.appointments),
      body: 'What the assistant booked, plus your own bookings. Move or cancel them here.' },
    { id: 'my-day', target: nav('my-day'), when: (c) => c.vertical.key === 'dental',
      title: "Dentists' day", body: "Each dentist's list for today and tomorrow." },
    { id: 'contacts', target: nav('contacts'), title: (c) => c.vertical.labels.contacts,
      body: "Everyone who's called or booked, with their history." },
    { ...knowledge, body: "Check what it tells callers. If something's wrong, tell your practice manager." },
    help,
  ],
}

export const DENTIST_TOUR: TourDef<TenantContext> = {
  key: 'tour:dashboard:dentist',
  steps: [
    { id: 'welcome', title: 'Welcome to LaudLine', placement: 'center',
      body: (c) => `This is your day at ${c.tenant.name}. There's nothing else to learn.` },
    { id: 'my-day', target: nav('my-day'), title: 'My day', body: 'Your patients for today and tomorrow.', learnMore: 'first-day-dentist' },
    { id: 'account', target: 'account-menu', placement: 'top', title: 'Your account',
      body: 'Change your password and turn on two-factor sign-in.' },
    help,
  ],
}

export const DASHBOARD_TOURS: Record<Role, TourDef<TenantContext>> = {
  owner: OWNER_TOUR,
  admin: ADMIN_TOUR,
  staff: STAFF_TOUR,
  dentist: DENTIST_TOUR,
}

export const tourFor = (ctx: TenantContext): TourDef<TenantContext> => DASHBOARD_TOURS[ctx.role] ?? STAFF_TOUR
