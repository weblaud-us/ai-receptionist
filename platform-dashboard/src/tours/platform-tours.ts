/**
 * Welcome tours for the platform app: one for platform admins, one for the sales team. Steps a
 * person's sales role can't use are left out (`when`); missing targets are skipped by the engine.
 */
import type { AuthUser } from '@receptionist/shared/auth/auth'
import type { TourDef, TourStep } from '@receptionist/shared/lib/tour'
import { can, salesOnly } from '@/lib/roles'

type Step = TourStep<AuthUser>

const nav = (path: string) => [`nav-${path}`, 'menu-button']

const help: Step = {
  id: 'help', target: 'help-menu', placement: 'bottom',
  title: 'Guides for the team',
  body: 'Help has a guide for each part of this app, and the guides practice owners see.',
}

export const ADMIN_TOUR: TourDef<AuthUser> = {
  key: 'tour:platform:admin',
  steps: [
    { id: 'welcome', placement: 'center', title: 'Platform admin',
      body: 'This app is for the LaudLine team. You can see every practice, so every change you make is logged.' },
    { id: 'businesses', target: nav('businesses'), route: '/', title: 'Businesses',
      body: 'Every practice on the platform. Open one to see its setup, team, numbers and calls.' },
    { id: 'new', target: 'new-business', title: 'New business', learnMore: 'launch-a-business',
      body: 'Set a practice up from its website in a few minutes, then send the owner their link. A launch checklist on its page shows what\'s left.' },
    { id: 'numbers', target: nav('numbers'), title: 'Numbers', body: 'Buy, assign and release phone numbers.', learnMore: 'numbers' },
    { id: 'pilots', target: nav('pilots'), title: 'Pilots', body: 'Free pilots we run by hand, with a feedback call at the end.', learnMore: 'pilots' },
    { id: 'dialer', target: nav('dialer'), title: 'Sales dialer', body: 'Our own outbound calls and CRM.', learnMore: 'sales-first-day' },
    { id: 'legal', target: nav('legal'), title: 'Legal documents', learnMore: 'legal-documents',
      body: 'Terms, Privacy and the DPA, as the website shows them. Publishing a material change asks owners to accept again.' },
    { id: 'account', target: 'account-menu', placement: 'top', title: 'Your account',
      body: 'Two-factor sign-in is required for platform admins. Set it up under Account.' },
    help,
  ],
}

export const SALES_TOUR: TourDef<AuthUser> = {
  key: 'tour:platform:sales',
  steps: [
    { id: 'mfa', target: 'mfa-card', when: (u) => !u.mfa_enabled, title: 'Turn on two-factor first',
      body: "The dialer won't open without it. It takes a minute with an authenticator app." },
    { id: 'welcome', placement: 'center', title: 'Welcome to the sales team',
      body: "You'll work here, in the Sales dialer. Here's what each tab is for." },
    { id: 'campaigns', target: 'dialer-tab-campaigns', route: '/dialer', title: 'Campaigns', body: "Who we're calling, and when." },
    { id: 'leads', target: 'dialer-tab-leads', title: 'Leads',
      body: 'Everyone we might call. Open a lead for its notes, calls and next step.' },
    { id: 'live', target: 'dialer-tab-live', title: 'Live calls',
      body: (u) => (can(u, 'caller') ? 'Listen in on a call, and take over if someone asks for a person.' : 'Calls happening right now.'),
      learnMore: 'live-calls' },
    { id: 'offers', target: 'dialer-tab-offers', title: 'Offers',
      body: (u) => (can(u, 'manager') ? 'What the AI says and sells. You can edit these.' : 'What the AI says and sells. Managers edit these.') },
    { id: 'compliance', target: 'dialer-tab-compliance', title: 'Compliance', learnMore: 'dialer-compliance',
      body: 'Do-not-call lists and consent. Every number is checked before every call.' },
    { id: 'search', target: 'dialer-search', title: 'Find anyone', body: 'Search every lead by name, number or e-mail.' },
    help,
  ],
}

export const tourFor = (user: AuthUser): TourDef<AuthUser> | null =>
  user.is_platform_admin ? ADMIN_TOUR : salesOnly(user) ? SALES_TOUR : null

/** Where each tour starts by itself. */
export const landingFor = (user: AuthUser): string => (user.is_platform_admin ? '/' : '/dialer')
