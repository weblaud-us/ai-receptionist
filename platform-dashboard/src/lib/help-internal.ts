/**
 * The LaudLine team's own guides (src/help/*.md), bundled only into this app and never published.
 * They must hold nothing secret: see src/help/README.md.
 */
import { helpRegistry, type HelpAudience, type HelpRegistry } from '@receptionist/shared/lib/help'

const ADMINS: HelpAudience[] = ['platform']
const EVERYONE: HelpAudience[] = ['platform', 'sales']

export const INTERNAL_HELP_ROWS = [
  { slug: 'platform-first-day', title: 'Your first day: platform admin', group: 'Getting started', audience: ADMINS,
    summary: 'Where things are, two-factor sign-in, and why everything is logged.' },
  { slug: 'sales-first-day', title: 'Your first day: sales team', group: 'Getting started', audience: EVERYONE,
    summary: 'The dialer tabs, and what each sales role can do.' },
  { slug: 'launch-a-business', title: 'Launching a business', group: 'Practices', audience: ADMINS,
    summary: 'New business, the set-password link, and the launch checklist.' },
  { slug: 'open-as-owner', title: 'Open a practice as its owner', group: 'Practices', audience: ADMINS,
    summary: 'Helping a practice from inside their dashboard, and what not to change.' },
  { slug: 'numbers', title: 'Phone numbers', group: 'Practices', audience: ADMINS,
    summary: 'Buying, regulatory bundles, assigning and releasing.' },
  { slug: 'pilots', title: 'Pilots', group: 'Practices', audience: ADMINS,
    summary: 'Starting, running and ending a free pilot.' },
  { slug: 'legal-documents', title: 'Legal documents', group: 'Practices', audience: ADMINS,
    summary: 'Editing, placeholders, and material changes.' },
  { slug: 'dialer-compliance', title: 'Calling within the rules', group: 'Sales', audience: EVERYONE,
    summary: 'Do-not-call registers, our own list, and what the AI says.' },
  { slug: 'live-calls', title: 'Live calls', group: 'Sales', audience: EVERYONE,
    summary: 'Listening in, taking over and ending a call.' },
]

export const internalHelp: HelpRegistry = helpRegistry(
  INTERNAL_HELP_ROWS,
  import.meta.glob<string>(['../help/*.md', '!../help/README.md'], { query: '?raw', import: 'default' }),
)
