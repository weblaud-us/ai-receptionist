// New businesses and new people: the getting-started list, when a welcome tour starts by itself,
// and that every tour step points at something the layout really shows for that role.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@receptionist/shared/lib/api'
import { resolveSteps } from '@receptionist/shared/lib/tour'
import { AppLayout } from '@/components/app/layout'
import { GettingStartedCard } from '@/components/app/getting-started'
import { TenantProvider, useTenant } from '@/lib/tenant'
import type { Role, TenantContext } from '@/lib/tenant-types'
import { DASHBOARD_TOURS } from '@/tours/dashboard-tours'
import { shouldAutoTour } from '@/tours/use-auto-tour'

const ALL_SEEN = Object.fromEntries(Object.values(DASHBOARD_TOURS).map((t) => [t.key, { status: 'done', at: null }]))

function context(role: Role, over: Partial<TenantContext> = {}): TenantContext {
  return {
    role, platform_admin: false, legacy: false, greeting: '',
    billing: { status: 'active' } as TenantContext['billing'],
    vertical: {
      key: 'dental', label: 'Dental practice', uses_scheduling: true, tools: [],
      labels: { appointment: 'Appointment', appointments: 'Appointments', contact: 'Patient', contacts: 'Patients',
                resource: 'Dentist', resources: 'Dentists', service: 'Treatment', services: 'Treatments' },
    },
    tenant: { id: 'ten_1', name: 'Harbour Smiles', onboarding: { completed_at: '2026-11-20T00:00:00Z' } } as TenantContext['tenant'],
    ...over,
  } as TenantContext
}

let routes: Record<string, unknown> = {}

vi.mock('@receptionist/shared/auth/auth', () => ({
  useAuth: () => ({
    status: 'authenticated', tenantId: 'ten_1', memberships: [], switchTenant: vi.fn(), logout: vi.fn(),
    user: { id: 'usr_1', name: 'Sam Lee', email: 'sam@hs.au', is_platform_admin: false },
  }),
}))
vi.mock('@receptionist/shared/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@receptionist/shared/lib/api')>()
  return {
    ...actual,
    api: vi.fn(async (path: string, opts?: { method?: string }) => {
      if (opts?.method === 'PUT' || opts?.method === 'DELETE') return { tutorials: {}, persist: true }
      const key = Object.keys(routes).find((k) => path === k || path.startsWith(`${k}?`))
      if (!key) throw new actual.ApiError(404, `no fixture for ${path}`)
      return routes[key]
    }),
  }
})

beforeEach(() => { vi.clearAllMocks() })
afterEach(cleanup)

function renderWith(node: React.ReactNode, path = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TenantProvider>
        <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

describe('when a welcome tour starts by itself', () => {
  const never = () => false
  it('starts for a new staff member on their first visit, even before setup is finished', () => {
    const ctx = context('staff', { tenant: { id: 'ten_1', name: 'H', onboarding: {} } as TenantContext['tenant'] })
    expect(shouldAutoTour(ctx, '/', never)).toBe(true)
  })

  it('waits for the owner to finish the setup wizard', () => {
    const ctx = context('owner', { tenant: { id: 'ten_1', name: 'H', onboarding: {} } as TenantContext['tenant'] })
    expect(shouldAutoTour(ctx, '/', never)).toBe(false)
    expect(shouldAutoTour(context('owner'), '/', never)).toBe(true)
  })

  it('never for a platform admin looking at a business, a paused business, off the landing page, or once seen', () => {
    expect(shouldAutoTour(context('owner', { platform_admin: true }), '/', never)).toBe(false)
    expect(shouldAutoTour(context('owner', { billing: { status: 'suspended' } as TenantContext['billing'] }), '/', never)).toBe(false)
    expect(shouldAutoTour(context('owner'), '/onboarding', never)).toBe(false)
    expect(shouldAutoTour(context('owner'), '/', (k) => k === 'tour:dashboard:owner')).toBe(false)
    expect(shouldAutoTour(context('dentist'), '/my-day', never)).toBe(true)
  })
})

describe('getting-started list', () => {
  it('shows each step, ticks the done ones, and hiding it saves that for this business', async () => {
    routes = {
      '/t/context': context('owner'),
      '/me/tutorials': { tutorials: {}, persist: true },
      '/t/getting-started': { done: 2, total: 8, complete: false, items: [
        { id: 'setup', done: true }, { id: 'test_call', done: true }, { id: 'number', done: false },
        { id: 'first_call', done: false }, { id: 'knowledge', done: false }, { id: 'calendar', done: false },
        { id: 'team', done: false }, { id: 'dpa', done: false },
      ] },
    }
    renderWith(<GettingStartedCard ctx={context('owner')} />)
    expect(await screen.findByText('2 of 8 done', {}, { timeout: 4000 })).toBeTruthy()
    expect(screen.getByText('Invite your team')).toBeTruthy()
    expect(screen.getAllByLabelText('Done')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Hide for now' }))
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith(
      '/me/tutorials/checklist%3Adashboard%3Agetting_started%3Aten_1',
      expect.objectContaining({ method: 'PUT', body: { status: 'skipped' } })))
  })
})

describe('every tour step points at something on the page', () => {
  function Shown({ role }: { role: Role }) {
    const { ctx } = useTenant()
    return ctx ? <span data-testid="role">{role}</span> : null
  }

  it.each(['owner', 'admin', 'staff', 'dentist'] as Role[])('%s', async (role) => {
    const ctx = context(role)
    routes = {
      '/t/context': ctx,
      '/me/tutorials': { tutorials: ALL_SEEN, persist: true },
      '/t/inbox': { items: [], overdue: 0, unassigned: 0 },
      '/admin/calls/live': { calls: [], total: 0 },
      '/public/site': { demo_phone_number: '', status_page_url: '', support_email: '', help_center_url: '' },
    }
    renderWith(
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<div data-tour="getting-started"><Shown role={role} /></div>} />
          <Route path="my-day" element={<Shown role={role} />} />
        </Route>
      </Routes>,
      role === 'dentist' ? '/my-day' : '/',
    )
    await screen.findByTestId('role', {}, { timeout: 5000 })
    const steps = resolveSteps(DASHBOARD_TOURS[role].steps, ctx)
    const missing = steps.filter((s) => s.targets.length && !document.querySelector(`[data-tour="${s.targets[0]}"]`)).map((s) => s.id)
    expect(missing).toEqual([])
  })
})
