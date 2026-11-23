// Smoke tests for whole pages (audit QA-02): Calls and Assistant render with real hooks and
// components against a mocked API, and show no call-recording UI (calls are not recorded).
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TenantProvider, useTenant } from '@/lib/tenant'
import Assistant from '@/pages/Assistant'
import Calls from '@/pages/Calls'

const CONTEXT = {
  role: 'owner',
  platform_admin: false,
  legacy: false,
  sms_enabled: false,
  greeting: "Thanks for calling Harbour Smiles, you're speaking with the clinic's AI assistant.",
  vertical: {
    key: 'dental', label: 'Dental practice', uses_scheduling: true, tools: ['check_availability'],
    labels: { appointment: 'Appointment', appointments: 'Appointments', contact: 'Patient', contacts: 'Patients',
              resource: 'Dentist', resources: 'Dentists', service: 'Treatment', services: 'Treatments' },
  },
  tenant: {
    id: 'ten_1', name: 'Harbour Smiles', slug: 'harbour-smiles', vertical: 'dental', status: 'active',
    country: 'AU', timezone: 'Australia/Sydney', currency: 'AUD', phone: '+61290000000',
    emergency_instructions: '',
    links: { booking: '', menu: '', directions: '', payment: '' },
    transfer: { number: '', only_during_hours: true, hours: '', warm: false },
    agent: {
      name: 'Aria', voice_provider: 'deepgram', voice_id: 'aura-2-theia-en', language: 'en-AU', stt_language: 'en-AU',
      greeting: '', tone: 'warm', custom_instructions: '', llm_model: 'gpt-4.1-mini',
      endpointing: { min_delay: 0.3, max_delay: 2.5 }, ambience: false, call_notice: '',
      max_call_minutes: 15, transcript_retention_days: 90, stt_keyterms: [],
    },
  },
}

const CALLS = {
  calls: [{
    call_id: 'call_1', channel: 'phone', direction: 'inbound', caller_phone: '+61412345678',
    started_at: '2026-11-23T01:00:00Z', ended_at: '2026-11-23T01:03:00Z', duration_s: 180, status: 'completed',
    outcome: 'booked', summary: 'Booked a check-up with Dr Lee on Tuesday.',
  }],
  total: 1, page: 1, pages: 1,
}

const ROUTES: Record<string, unknown> = {
  '/t/context': CONTEXT,
  '/t/calls': CALLS,
  '/admin/calls/live': { calls: [], total: 0 },
  '/admin/messages': { messages: [], total: 0, new: 0 },
  '/t/voices': { provider: 'deepgram', available: true, voices: [{ id: 'aura-2-theia-en', name: 'Theia', accent: 'Australian' }] },
}

vi.mock('@receptionist/shared/auth/auth', () => ({
  useAuth: () => ({ status: 'authenticated', tenantId: 'ten_1', user: { is_platform_admin: false } }),
}))

vi.mock('@receptionist/shared/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@receptionist/shared/lib/api')>()
  return {
    ...actual,
    api: vi.fn(async (path: string) => {
      const key = Object.keys(ROUTES).find((k) => path === k || path.startsWith(`${k}?`) || path.startsWith(`${k}/`))
      if (!key) throw new actual.ApiError(404, `no fixture for ${path}`)
      return ROUTES[key]
    }),
  }
})

afterEach(cleanup)

/** Pages call useCtx(), which needs the context loaded; the layout normally guarantees that. */
function Loaded({ children }: { children: ReactNode }) {
  const { ctx } = useTenant()
  return ctx ? <>{children}</> : <p>loading</p>
}

function renderPage(page: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TenantProvider><Loaded>{page}</Loaded></TenantProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Calls page', () => {
  it('lists past calls and has no recording player', async () => {
    renderPage(<Calls />)
    expect(await screen.findByText('+61412345678')).toBeTruthy()
    expect(await screen.findByText('No calls in progress')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/recording/i)
  })
})

describe('Assistant page', () => {
  it('renders the voice settings for a dental practice, with no recording option', async () => {
    renderPage(<Assistant />)
    expect(await screen.findByText('Voice & greeting')).toBeTruthy()
    expect((screen.getByDisplayValue('Aria') as HTMLInputElement).value).toBe('Aria')
    expect(document.body.textContent).not.toMatch(/record(ed|ing) (calls|this call)/i)
  })
})
