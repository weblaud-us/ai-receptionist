import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CallPrep, SingleCallReview } from '@/lib/single-call'

// The single-call sheet (SALES_SINGLE_CALL_PLAN.md) with its data replaced. What matters here: it shows
// what the server worked out, "Approve & call" sends exactly the review that was shown, and a refusal
// or a changed review is shown in place of a call.
type Opts<T> = { onSuccess?: (v: T) => void; onError?: (e: unknown) => void }
// jsdom has no PointerEvent, and Base UI's checkbox dispatches one when clicked.
if (!('PointerEvent' in window)) Object.assign(window, { PointerEvent: MouseEvent })
const prepare = vi.fn()
const review = vi.fn()
const place = vi.fn()
const patchLead = vi.fn()
// The attempt the live step polls; a test can make it one the AI hasn't started yet.
let liveAttempt: Record<string, unknown> = {}

vi.mock('@/lib/single-call', () => ({
  useSingleCall: () => ({
    prepare: { mutate: prepare, isPending: false },
    review: { mutate: review, isPending: false },
    place: { mutate: place, isPending: false },
  }),
  refusal: (e: { message?: string; detail?: { message?: string; review?: unknown } }) =>
    ({ message: e.detail?.message ?? e.message ?? '', review: e.detail?.review ?? null }),
  FIELD_LABEL: { contact_name: 'contact', vertical: 'kind of business' },
}))
vi.mock('@/lib/sales', () => ({
  MODE_LABEL: { ai_cold_b2b_us: 'AI cold calls — business landlines', ai_consented: 'AI calls — people who asked for one' },
  WRAP_OUTCOMES: [{ value: 'interested', label: 'Interested' }],
  endedText: (r?: { cancelled?: boolean }) => (r?.cancelled ? 'Cancelled before it rang' : 'Call ended'),
  useAttempt: () => ({ data: { attempt: { status: 'in_progress', outcome: null, summary: null, next_step: null, ...liveAttempt } } }),
  useFollowUpMutations: () => ({ patchLead: { mutate: patchLead, isPending: false }, addNote: { mutate: vi.fn(), isPending: false } }),
  useSalesLeads: () => ({ data: { leads: [] } }),
  useSalesMutations: () => ({ join: { mutate: vi.fn(), isPending: false }, hangUp: { mutate: vi.fn(), isPending: false },
    wrapUp: { mutate: vi.fn(), isPending: false } }),
}))
vi.mock('@/components/sales/call-audio', () => ({ CallAudio: ({ title }: { title: string }) => <div>audio: {title}</div> }))
vi.mock('@/components/sales/call-panel', () => ({ CallTimer: () => <div>timer</div>, Transcript: () => <div>transcript</div> }))

const { SingleCallSheet } = await import('@/components/sales/single-call')

function reviewOf(over: Partial<SingleCallReview> = {}): SingleCallReview {
  return {
    lead_id: 'lead_1', mode: 'ai_cold_b2b_us', mode_reason: 'An AI cold call to a business landline in the US.',
    offer: { id: 'off_weblaud_studio', name: 'WebLaud studio', version: 2, company_name: 'WebLaud' },
    variant_id: 'custom_software', callback: false,
    offers: [{ id: 'off_weblaud_studio', name: 'WebLaud studio', version: 2, mode: 'ai_cold_b2b_us', reason: '',
      variants: [{ id: 'ai_automation', label: 'AI automation first' }, { id: 'custom_software', label: 'Website first' }] }],
    from_number: '+15125550100', caller_ids: ['+15125550100'],
    opener: "Hi, this is Sam, an AI assistant calling from WebLaud about Joe's Plumbing.",
    checks: [{ id: 'call_type', label: 'The AI may call this number', status: 'pass', detail: '' },
      { id: 'calling_window', label: 'Inside calling hours where they are', status: 'pass', detail: '' }],
    failures: [], retry_at: null, local_times: [{ tz: 'America/Chicago', text: 'Tue 17 Nov, 10:00' }],
    cost: { direction: 'outbound', country: 'US', country_name: 'United States', line_kind: 'landline_or_mobile',
      line_text: 'landline or mobile', carrier_usd_per_min: 0.01, per_minute: { low: 0.044, mid: 0.048, high: 0.052 },
      typical_minutes: 3, typical_call_usd: 0.14, source: 'twilio', basis: 'Trunking Outbound Minute - United States',
      fetched_at: null, warn: false },
    can_call: true, overrides: [], overridable: [], may_override: false, hash: 'hash-1', ...over,
  }
}

function prepOf(over: Partial<CallPrep> = {}): CallPrep {
  return {
    callable: true, created: false, ai_filled: ['contact_name'],
    lead: { id: 'lead_1', business_name: "Joe's Plumbing", phone: '+15125550199', contact_name: 'Joe', email: '',
      website: '', vertical: 'plumber', agent_notes: '', status: 'queued' } as CallPrep['lead'],
    suggestion: { offer_id: 'off_weblaud_studio', offer_version: 2, variant_id: 'custom_software', why: 'Their site has no online booking.', vertical: 'plumber', source: 'ai' },
    review: reviewOf(), ...over,
  }
}

beforeEach(() => { prepare.mockReset(); review.mockReset(); place.mockReset(); patchLead.mockReset(); localStorage.clear(); liveAttempt = {} })
afterEach(cleanup)

describe('Call one business', () => {
  it('gets a lead ready, shows what the server worked out, and approves exactly that review', () => {
    prepare.mockImplementation((_body: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf()))
    render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
    expect(prepare).toHaveBeenCalledTimes(1)
    expect(prepare.mock.calls[0][0]).toEqual({ lead_id: 'lead_1' })            // straight to Preparing: no business step
    expect(screen.getByText(/an AI assistant calling from WebLaud/)).toBeTruthy()
    expect(screen.getByText('AI cold call to a business')).toBeTruthy()
    expect(screen.getByText('About $0.048/min · United States, landline or mobile · a 3-min call ≈ $0.14 (Twilio rate)')).toBeTruthy()
    expect(screen.getByText(/Their site has no online booking/)).toBeTruthy()
    expect(screen.getByText('From their website: check it.')).toBeTruthy()      // the contact the AI filled in
    place.mockImplementation((_r: unknown, o: Opts<unknown>) => o.onSuccess?.({ attempt_id: 'att_1', room: 'out-att_1',
      attempt: { id: 'att_1' }, join: { token: 't', url: 'wss://x', room: 'out-att_1', mode: 'listen' } }))
    fireEvent.click(screen.getByRole('button', { name: /Approve & call/ }))
    expect(place.mock.calls[0][0]).toMatchObject({ hash: 'hash-1', from_number: '+15125550100', variant_id: 'custom_software' })
    expect(screen.getByText("audio: Calling Joe's Plumbing")).toBeTruthy()
    expect(screen.getByRole('button', { name: /Take over/ })).toBeTruthy()
    expect(screen.queryByText(/hasn't started this call yet/)).toBeNull()        // on the call: no waiting line
  })

  it("a call the AI hasn't started after a while says it will be cancelled, not dialled", () => {
    const approve = (createdAgoMs: number) => {
      liveAttempt = { status: 'dispatched', created_at: new Date(Date.now() - createdAgoMs).toISOString() }
      prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf()))
      place.mockImplementation((_r: unknown, o: Opts<unknown>) => o.onSuccess?.({ attempt_id: 'att_1', room: 'out-att_1',
        attempt: { id: 'att_1' }, join: { token: 't', url: 'wss://x', room: 'out-att_1', mode: 'listen' } }))
      render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: /Approve & call/ }))
    }
    approve(5_000)                                                              // a few seconds in: normal
    expect(screen.queryByText(/hasn't started this call yet/)).toBeNull()
    cleanup()
    approve(30_000)
    expect(screen.getByRole('status').textContent).toMatch(/cancelled and nothing is dialled/)
    expect(screen.getByRole('button', { name: /End call/ })).toBeTruthy()       // and it can be ended (cancelled) now
  })

  it("won't approve a call while a check fails, and says when it can go", () => {
    const blocked = reviewOf({ can_call: false, retry_at: '2026-11-18T14:00:00Z',
      checks: [{ id: 'calling_window', label: 'Inside calling hours where they are', status: 'fail', detail: 'Next: Wed 08:00.' }] })
    prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf({ review: blocked })))
    render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
    expect((screen.getByRole('button', { name: /Approve & call/ }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Next: Wed 08:00/)).toBeTruthy()
    expect(screen.getByText(/You can call them from/)).toBeTruthy()
  })

  it('a business the AI may not call shows why, with nothing to approve', () => {
    prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf({ callable: false, review: null,
      reason: 'The AI may not call numbers in the UK. Call them yourself in a human-assisted campaign.' })))
    render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
    expect(screen.getByText(/The AI may not call numbers in the UK/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Approve & call/ })).toBeNull()
  })

  it('when something changed since the review, the fresh review replaces it and nothing is dialled', () => {
    prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf()))
    const fresh = reviewOf({ hash: 'hash-2', opener: 'Hi, this is Sam, an AI assistant calling from WebLaud about the new clinic.' })
    place.mockImplementation((_r: unknown, o: Opts<unknown>) => o.onError?.({ message: 'x', detail: {
      code: 'review_changed', message: 'Something changed since you reviewed this call. Check it again.', review: fresh } }))
    render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Approve & call/ }))
    expect(screen.getByRole('alert').textContent).toContain('Something changed since you reviewed this call')
    expect(screen.getByText(/about the new clinic/)).toBeTruthy()
    expect(screen.queryByText(/^audio:/)).toBeNull()
  })

  it('a new business needs its website or phone number, then is got ready', () => {
    prepare.mockImplementation(() => {})
    render(<SingleCallSheet call="new" onClose={vi.fn()} />)
    expect(prepare).not.toHaveBeenCalled()
    const go = screen.getByRole('button', { name: /Get the call ready/ }) as HTMLButtonElement
    expect(go.disabled).toBe(true)
    fireEvent.change(screen.getByPlaceholderText('harbourdental.com.au'), { target: { value: 'harbour.example' } })
    fireEvent.click(screen.getByRole('button', { name: /Get the call ready/ }))
    expect(prepare.mock.calls[0][0]).toEqual({ business: { website: 'harbour.example', no_website: false, business_name: '', phone: '',
      country: 'AU', contact_name: '' } })
    expect(screen.getByText('Getting the call ready…')).toBeTruthy()
  })

  it('a website without a phone number asks for one', () => {
    prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) =>
      o.onSuccess?.({ needs_phone: true, found: { business_name: 'Harbour Dental', website: 'https://harbour.example' } }))
    render(<SingleCallSheet call="new" onClose={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText('harbourdental.com.au'), { target: { value: 'harbour.example' } })
    fireEvent.click(screen.getByRole('button', { name: /Get the call ready/ }))
    expect(screen.getByText(/couldn't find a phone number/)).toBeTruthy()
    expect((screen.getByRole('button', { name: /Get the call ready/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it("a business with no website needs its phone number, and the country you used last is kept", () => {
    localStorage.setItem('single-call.country', 'BD')
    prepare.mockImplementation(() => {})
    render(<SingleCallSheet call="new" onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Website'), { target: { value: 'facebook.com/dhakasweets' } })
    expect(screen.getByText(/counts as no website of their own/)).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: "They don't have a website" }))
    expect((screen.getByLabelText('Website') as HTMLInputElement).disabled).toBe(true)
    const go = screen.getByRole('button', { name: /Get the call ready/ }) as HTMLButtonElement
    expect(go.disabled).toBe(true)                                         // nothing to read: the number is needed
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '+8801767677517' } })
    fireEvent.click(screen.getByRole('button', { name: /Get the call ready/ }))
    expect(prepare.mock.calls[0][0]).toEqual({ business: { website: '', no_website: true, business_name: '',
      phone: '+8801767677517', country: 'BD', contact_name: '' } })
  })

  it('a warning (a mobile, the local rules) is shown but does not stop the call', () => {
    const warned = reviewOf({ checks: [
      { id: 'local_rules', label: 'Stricter rules for AI calls in the UK', status: 'warn', detail: 'UK law (PECR regulation 19) needs prior consent.' },
      { id: 'line_type', label: 'A business number', status: 'warn', detail: "A mobile. Make sure it's the business's number." }] })
    prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf({ review: warned })))
    render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
    expect(screen.getByText('Stricter rules for AI calls in the UK')).toBeTruthy()
    expect(screen.getByText(/PECR regulation 19/)).toBeTruthy()
    expect((screen.getByRole('button', { name: /Approve & call/ }) as HTMLButtonElement).disabled).toBe(false)
  })

  describe('overrides', () => {
    const tests = { id: 'simulation', label: 'This script passed its automated test calls', detail: 'Run the test calls.', override: 'simulation' } as const
    const hours = { id: 'calling_window', label: 'Inside calling hours where they are', detail: 'Outside business hours where they are.', override: 'business_hours' } as const
    const blocked = (over: Partial<SingleCallReview> = {}) => reviewOf({ can_call: false, retry_at: '2026-11-29T03:00:00Z',
      checks: [{ ...tests, status: 'fail' }, { ...hours, status: 'fail' }], overridable: ['simulation', 'business_hours'], ...over })

    it('a sales manager ticks Override, the review is worked out again, and the call needs a reason', () => {
      const overridden = reviewOf({ checks: [{ ...tests, status: 'overridden' }, { ...hours, status: 'overridden' }],
        overrides: ['business_hours', 'simulation'], may_override: true, hash: 'hash-over' })
      prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf({ review: blocked({ may_override: true }) })))
      review.mockImplementation((_b: unknown, o: Opts<SingleCallReview>) => o.onSuccess?.(overridden))
      render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
      expect((screen.getByRole('button', { name: /Approve & call/ }) as HTMLButtonElement).disabled).toBe(true)
      expect(screen.getByText(/Tick Override on the checks you want to skip/)).toBeTruthy()

      fireEvent.click(screen.getByRole('checkbox', { name: 'Override: Inside calling hours where they are' }))
      expect(review.mock.calls[0][0]).toMatchObject({ lead_id: 'lead_1', overrides: ['business_hours'] })
      expect(screen.getAllByText(/Overridden/).length).toBe(2)
      const call = screen.getByRole('button', { name: /Override & call/ }) as HTMLButtonElement
      expect(call.disabled).toBe(true)                                           // not without a reason
      expect(screen.getByText(/Say why you're overriding/)).toBeTruthy()
      fireEvent.change(screen.getByLabelText("Why you're overriding"), { target: { value: 'They asked us to call on Saturday' } })
      expect(call.disabled).toBe(false)
      fireEvent.click(call)
      expect(place.mock.calls[0][0]).toMatchObject({ hash: 'hash-over', overrides: ['business_hours', 'simulation'],
        override_reason: 'They asked us to call on Saturday' })
    })

    it('unticking Override asks for the review without it', () => {
      const overridden = reviewOf({ checks: [{ ...tests, status: 'overridden' }], overrides: ['simulation'], may_override: true })
      prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf({ review: overridden })))
      render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('checkbox', { name: 'Override: This script passed its automated test calls' }))
      expect(review.mock.calls[0][0]).toMatchObject({ overrides: [] })
    })

    it('a caller sees what a sales manager could override, with nothing to tick', () => {
      prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf({ review: blocked() })))
      render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
      expect(screen.queryByRole('checkbox', { name: /^Override:/ })).toBeNull()
      expect(screen.getAllByText('A sales manager can override this').length).toBe(2)
      expect(screen.getByText(/A sales manager can override these checks/)).toBeTruthy()
    })

    it("says which checks can't be overridden, instead of only greying the button out", () => {
      prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf({ review: blocked({ may_override: true,
        checks: [{ ...tests, status: 'fail' }, { id: 'internal_dnc', label: 'Not on our do-not-call list', status: 'fail', detail: '' }],
        overridable: ['simulation'] }) })))
      render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
      expect(screen.getByRole('checkbox', { name: 'Override: This script passed its automated test calls' })).toBeTruthy()
      expect(screen.getByText(/Not on our do-not-call list: this check can't be overridden/)).toBeTruthy()
    })
  })

  it('ticking "no website" on the review saves it and asks the AI again what to sell', () => {
    prepare.mockImplementation((_b: unknown, o: Opts<CallPrep>) => o.onSuccess?.(prepOf()))
    patchLead.mockImplementation((_b: unknown, o: Opts<unknown>) => o.onSuccess?.({}))
    render(<SingleCallSheet call="lead_1" onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('checkbox', { name: "They don't have a website of their own" }))
    fireEvent.click(screen.getByRole('button', { name: /Save details/ }))
    expect(patchLead.mock.calls[0][0]).toMatchObject({ id: 'lead_1', no_website: true })
    expect(prepare).toHaveBeenCalledTimes(2)                               // prepared again for the new situation
    expect(prepare.mock.calls[1][0]).toEqual({ lead_id: 'lead_1' })
    expect(review).not.toHaveBeenCalled()
  })
})
