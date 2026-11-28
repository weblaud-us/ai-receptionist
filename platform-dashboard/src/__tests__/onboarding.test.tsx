// The platform's tutorials: the launch checklist on a business, the team's welcome tours (by role),
// and the internal guides (bundled here only, never mixed with the public help centre).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AuthUser } from '@receptionist/shared/auth/auth'
import { Markdown } from '@receptionist/shared/components/markdown'
import { publicHelp } from '@receptionist/shared/lib/help'
import { articleTitle, linkedSlugs, parseMarkdown } from '@receptionist/shared/lib/markdown'
import { resolveSteps } from '@receptionist/shared/lib/tour'
import { PlatformLayout } from '@/components/layout'
import { launchItems } from '@/components/launch-checklist'
import { INTERNAL_HELP_ROWS, internalHelp } from '@/lib/help-internal'
import type { TenantDetails } from '@/lib/platform'
import { ADMIN_TOUR, SALES_TOUR, tourFor } from '@/tours/platform-tours'
import { shouldAutoTour } from '@/tours/use-auto-tour'

let user: AuthUser = { id: 'usr_1', email: 'root@laudline.io', name: 'Root', is_platform_admin: true, mfa_enabled: true }

vi.mock('@receptionist/shared/auth/auth', () => ({
  useAuth: () => ({ status: 'authenticated', user, memberships: [], logout: vi.fn() }),
}))
vi.mock('@receptionist/shared/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@receptionist/shared/lib/api')>()
  return {
    ...actual,
    api: vi.fn(async (path: string) => {
      if (path === '/me/tutorials') return { tutorials: { 'tour:platform:admin': { status: 'done', at: null }, 'tour:platform:sales': { status: 'done', at: null } }, persist: true }
      if (path === '/public/site') return { demo_phone_number: '', status_page_url: '', support_email: '', help_center_url: '' }
      throw new actual.ApiError(404, `no fixture for ${path}`)
    }),
  }
})

afterEach(cleanup)

const viewer: AuthUser = { id: 'u2', email: 'v@x.io', name: 'V', is_platform_admin: false, sales_role: 'viewer', mfa_enabled: false }
const manager: AuthUser = { ...viewer, sales_role: 'manager', mfa_enabled: true }

function details(over: Partial<TenantDetails> = {}): TenantDetails {
  return {
    tenant: { id: 'ten_1', name: 'Harbour Smiles', status: 'trial', onboarding: {}, legal: { dpa_version: '', dpa_accepted_at: null },
              pilot: { active: false, ends_at: null }, billing: { subscription_status: '' } } as unknown as TenantDetails['tenant'],
    stats: {} as TenantDetails['stats'], numbers: [], members: [], invites: [], recent_calls: [], audit: [], number_orders: [],
    signals: { setup: false, test_call: false, number: false, first_call: false, knowledge: false, calendar: false, team: false, dpa: false },
    ...over,
  }
}

describe('launch checklist', () => {
  it('starts with nothing done', () => {
    expect(launchItems(details(), vi.fn()).filter((i) => i.done)).toEqual([])
  })

  it('ticks off from the members, numbers, tenant and signals the page already has', () => {
    const d = details({
      members: [{ id: 'u', email: 'o@hs.au', name: 'O', role: 'owner', last_login_at: '2026-11-25T00:00:00Z', disabled: false, pending_password: false, mfa: false }],
      numbers: [{ e164: '+61290000000', status: 'active' }],
      signals: { setup: true, test_call: true, number: true, first_call: true, knowledge: false, calendar: false, team: true, dpa: true },
    })
    d.tenant.onboarding = { completed_at: '2026-11-25T00:00:00Z' }
    d.tenant.legal = { dpa_version: '1', dpa_accepted_at: '2026-11-25T00:00:00Z' }
    d.tenant.pilot = { active: true, ends_at: null }
    expect(launchItems(d, vi.fn()).every((i) => i.done)).toBe(true)
  })

  it('an invited owner who has not set a password yet is added, not signed in', () => {
    const d = details({ invites: [{ email: 'o@hs.au', role: 'owner', expires_at: null }] })
    const items = Object.fromEntries(launchItems(d, vi.fn()).map((i) => [i.id, i.done]))
    expect(items.owner).toBe(true)
    expect(items.owner_signed_in).toBe(false)
  })

  it('opens the right tab', () => {
    const onTab = vi.fn()
    launchItems(details(), onTab).find((i) => i.id === 'number')!.action!.onClick!()
    expect(onTab).toHaveBeenCalledWith('numbers')
  })
})

describe('welcome tours', () => {
  it('platform admins get the admin tour, the sales team theirs, anyone else none', () => {
    expect(tourFor(user)?.key).toBe('tour:platform:admin')
    expect(tourFor(viewer)?.key).toBe('tour:platform:sales')
    expect(tourFor({ ...viewer, sales_role: null })).toBeNull()
  })

  it('the sales tour asks for two-factor first only when it is off, and words steps by role', () => {
    expect(resolveSteps(SALES_TOUR.steps, viewer)[0].id).toBe('mfa')
    const forManager = resolveSteps(SALES_TOUR.steps, manager)
    expect(forManager[0].id).toBe('welcome')
    expect(forManager.find((s) => s.id === 'offers')!.body).toContain('You can edit these')
    expect(resolveSteps(SALES_TOUR.steps, viewer).find((s) => s.id === 'offers')!.body).toContain('Managers edit these')
    // Single calls are for callers and up: a viewer's tour never points at a button they can't see.
    expect(forManager.some((s) => s.id === 'single-call')).toBe(true)
    expect(resolveSteps(SALES_TOUR.steps, viewer).some((s) => s.id === 'single-call')).toBe(false)
  })

  it('starts by itself only on the landing page, once', () => {
    expect(shouldAutoTour(user, '/', () => false)).toBe(true)
    expect(shouldAutoTour(user, '/numbers', () => false)).toBe(false)
    expect(shouldAutoTour(viewer, '/dialer', () => false)).toBe(true)
    expect(shouldAutoTour(user, '/', (k) => k === 'tour:platform:admin')).toBe(false)
  })

  it('every admin-tour sidebar step points at a real nav item', async () => {
    user = { id: 'usr_1', email: 'root@laudline.io', name: 'Root', is_platform_admin: true, mfa_enabled: true }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/']}>
          <Routes><Route element={<PlatformLayout />}><Route index element={<button data-tour="new-business">New business</button>} /></Route></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await screen.findByText('New business', {}, { timeout: 5000 })
    const missing = resolveSteps(ADMIN_TOUR.steps, user)
      .filter((s) => s.targets.length && !document.querySelector(`[data-tour="${s.targets[0]}"]`))
      .map((s) => s.id)
    expect(missing).toEqual([])
  })
})

describe('internal guides', () => {
  const files = import.meta.glob<string>('../help/*.md', { query: '?raw', import: 'default', eager: true })
  const articles = Object.entries(files)
    .map(([path, body]) => ({ slug: /([^/]+)\.md$/.exec(path)![1], body }))
    .filter((a) => a.slug !== 'README')

  it('every file is in the manifest, and none shares a slug with a public article', () => {
    expect(articles.map((a) => a.slug).sort()).toEqual(INTERNAL_HELP_ROWS.map((r) => r.slug).sort())
    for (const e of internalHelp.entries) expect(publicHelp.get(e.slug), e.slug).toBeUndefined()
  })

  it.each(articles)('$slug: title matches, links resolve, renders cleanly', ({ slug, body }) => {
    const blocks = parseMarkdown(body)
    expect(articleTitle(blocks)).toBe(internalHelp.get(slug)!.title)
    for (const target of linkedSlugs(blocks)) {
      expect(internalHelp.get(target) ?? publicHelp.get(target), `${slug} links to ${target}`).toBeTruthy()
    }
    const { container } = render(<MemoryRouter><Markdown source={body} /></MemoryRouter>)
    container.querySelectorAll('code').forEach((c) => c.remove())
    expect(container.textContent).not.toMatch(/\*\*|\]\(|\|---/)
  })
})
