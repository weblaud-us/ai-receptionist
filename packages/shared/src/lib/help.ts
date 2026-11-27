/**
 * Help articles shown in the dashboards (Help → Guides, and "Learn more" links on pages).
 *
 * The article files are in src/help/*.md, which is also what the external help centre publishes.
 * This manifest holds the titles and who each article is for, so the list and the menus never load
 * article bodies; each body is its own small chunk, loaded when opened. Add a row when you add a file.
 */

/** Who an article is written for. Practice roles, plus the platform's own team. */
export type HelpAudience = 'owner' | 'admin' | 'staff' | 'dentist' | 'platform' | 'sales'

export interface HelpEntry {
  slug: string
  title: string
  summary: string
  audience: HelpAudience[]
  group: string
  load: () => Promise<string>
}

export interface HelpRegistry {
  entries: HelpEntry[]
  get: (slug: string) => HelpEntry | undefined
}

type Row = Omit<HelpEntry, 'load'>

/** Builds a registry from manifest rows and an import.meta.glob of the article files. */
export function helpRegistry(rows: Row[], files: Record<string, () => Promise<string>>): HelpRegistry {
  const bySlug = new Map<string, () => Promise<string>>()
  for (const [path, load] of Object.entries(files)) {
    const slug = /([^/]+)\.md$/.exec(path)?.[1]
    if (slug && slug.toLowerCase() !== 'readme') bySlug.set(slug, load)
  }
  const entries = rows.filter((r) => bySlug.has(r.slug)).map((r) => ({ ...r, load: bySlug.get(r.slug)! }))
  const index = new Map(entries.map((e) => [e.slug, e]))
  return { entries, get: (slug) => index.get(slug) }
}

const PRACTICE: HelpAudience[] = ['owner', 'admin', 'staff']
const MANAGERS: HelpAudience[] = ['owner', 'admin']

/** The practice-facing articles (public: the help centre publishes the same files). */
export const PUBLIC_HELP_ROWS: Row[] = [
  { slug: 'getting-started', title: 'Getting started', group: 'Getting started', audience: MANAGERS,
    summary: 'The first 15 minutes, from sign-up to the first real call.' },
  { slug: 'first-day-practice-manager', title: 'Your first day: practice manager', group: 'Getting started', audience: MANAGERS,
    summary: 'Settings, answers, prices and test calls: what you look after.' },
  { slug: 'first-day-front-desk', title: 'Your first day: front desk', group: 'Getting started', audience: ['staff'],
    summary: 'The inbox, calls and appointments you use every day.' },
  { slug: 'first-day-dentist', title: 'Your first day: dentists', group: 'Getting started', audience: ['dentist'],
    summary: 'Your own day, and how to keep your account safe.' },
  { slug: 'inbox', title: 'The inbox', group: 'Day to day', audience: PRACTICE,
    summary: 'Callbacks and requests that need a person, and how to clear them.' },
  { slug: 'results', title: 'Results and reports', group: 'Day to day', audience: PRACTICE,
    summary: 'What the numbers mean, and the monthly e-mail.' },
  { slug: 'knowledge', title: 'Teach your assistant (Knowledge)', group: 'Your assistant', audience: PRACTICE,
    summary: 'Answers, unanswered questions and the website import.' },
  { slug: 'call-forwarding', title: 'Forward your calls to your assistant', group: 'Your assistant', audience: MANAGERS,
    summary: 'Codes for mobiles, landlines and office phone systems.' },
  { slug: 'calendar', title: 'Connect your calendar', group: 'Your assistant', audience: MANAGERS,
    summary: 'Google Calendar two-way sync.' },
  { slug: 'team-and-roles', title: 'Your team and their roles', group: 'Your practice', audience: MANAGERS,
    summary: 'Owner, admin, front desk and dentist, and inviting people.' },
  { slug: 'caller-privacy', title: 'When a caller asks about their data', group: 'Your practice', audience: MANAGERS,
    summary: 'Copy or deletion requests, opt-outs and recording.' },
  { slug: 'troubleshooting', title: 'Troubleshooting', group: 'Your practice', audience: PRACTICE,
    summary: 'Calls not answered, wrong answers, transfers.' },
]

export const publicHelp: HelpRegistry = helpRegistry(
  PUBLIC_HELP_ROWS,
  import.meta.glob<string>(['../help/*.md', '!../help/README.md'], { query: '?raw', import: 'default' }),
)

/** The articles for one person first, then the rest, both in manifest order. */
export function forAudience(entries: HelpEntry[], audience: HelpAudience[]): { mine: HelpEntry[]; rest: HelpEntry[] } {
  const want = new Set(audience)
  const mine = entries.filter((e) => e.audience.some((a) => want.has(a)))
  return { mine, rest: entries.filter((e) => !mine.includes(e)) }
}
