import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The way into a single call (SALES_SINGLE_CALL_PLAN.md): a "Call a business" button on the Dialer page
// for callers and up, and the sheet opened from the page's URL (?call=new or ?call=<lead id>).
let role: string | null = 'viewer'

vi.mock('@receptionist/shared/auth/auth', () => ({
  useAuth: () => ({ user: { id: 'usr_1', email: 'sam@weblaud.test', name: 'Sam', is_platform_admin: false, sales_role: role, mfa_enabled: true } }),
}))
vi.mock('@/lib/sales', () => ({
  MODE_LABEL: {},
  downloadSalesCsv: vi.fn(),
  useSalesStatus: () => ({ data: { ready: true, problems: [], single_calls: { enabled: true, problems: [], per_day: 30, max_concurrent: 2 } }, error: null }),
  useLiveSalesCalls: () => ({ data: { total: 0, calls: [] } }),
  useSalesMutations: () => ({}),
  useDnc: () => ({ data: { rows: [], total: 0, page: 1, pages: 1 } }),
}))
vi.mock('@receptionist/shared/components/security-card', () => ({ SecurityCard: () => null }))
for (const [path, name] of [
  ['@/components/sales/campaigns', 'Campaigns'], ['@/components/sales/leads', 'Leads'], ['@/components/sales/offers', 'Offers'],
  ['@/components/sales/pipeline', 'Pipeline'], ['@/components/sales/live', 'Live'], ['@/components/sales/results', 'Results'],
  ['@/components/sales/team', 'Team'], ['@/components/sales/numbers', 'NumbersCard'], ['@/components/sales/reports', 'Reports'],
  ['@/components/sales/activity', 'ActivityLog'], ['@/components/sales/search', 'SalesSearch'],
] as const) vi.doMock(path, () => ({ [name]: () => null }))
vi.mock('@/components/sales/single-call', () => ({
  SingleCallSheet: ({ call }: { call: string | null }) => <div>sheet: {call ?? 'closed'}</div>,
}))

const { default: Dialer } = await import('@/pages/Dialer')

const at = (url: string) => render(<MemoryRouter initialEntries={[url]}><Dialer /></MemoryRouter>)

afterEach(cleanup)

describe('Calling one business from the Dialer page', () => {
  it('viewers get no button, and a ?call= link opens nothing for them', () => {
    role = 'viewer'
    at('/dialer?call=lead_9')
    expect(screen.queryByRole('button', { name: /Call a business/ })).toBeNull()
    expect(screen.getByText('sheet: closed')).toBeTruthy()
  })

  it('callers open it from the button, or straight at a lead from its link', () => {
    role = 'caller'
    at('/dialer')
    expect(screen.getByText('sheet: closed')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Call a business/ }))
    expect(screen.getByText('sheet: new')).toBeTruthy()
    cleanup()
    at('/dialer?tab=leads&call=lead_9')
    expect(screen.getByText('sheet: lead_9')).toBeTruthy()
  })
})
