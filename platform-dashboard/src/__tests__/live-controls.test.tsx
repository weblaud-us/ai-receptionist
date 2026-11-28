import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The Live tab with its data and heavy children replaced: what matters here is who sees which
// control, and that ending or taking over a call with a real person on it is confirmed first.
const join = vi.fn()
const hangUp = vi.fn()
let level = 'viewer'

vi.mock('@/lib/sales', () => ({
  MODE_LABEL: { human_assisted: 'Human-assisted' },
  endedText: (r?: { cancelled?: boolean }) => (r?.cancelled ? 'Cancelled before it rang' : 'Call ended'),
  useLiveSalesCalls: () => ({
    data: { total: 1, calls: [{ id: 'att_1', business_name: 'Harbour Dental', to: '+61290000000', status: 'in_progress', mode: 'human_assisted', amd: null, created_at: new Date().toISOString() }] },
    error: null,
  }),
  useSalesMutations: () => ({ join: { mutate: join, isPending: false }, hangUp: { mutate: hangUp, isPending: false } }),
}))
vi.mock('@/lib/roles', () => {
  const rank: Record<string, number> = { viewer: 1, caller: 2, manager: 3, admin: 4 }
  return { useCan: () => (want: string) => rank[level] >= rank[want] }
})
vi.mock('@/components/sales/power-dialer', () => ({ PowerDialer: () => <div>power dialer</div> }))
vi.mock('@/components/sales/call-audio', () => ({ CallAudio: () => <div>on the call</div> }))

const { Live } = await import('@/components/sales/live')

beforeEach(() => { join.mockReset(); hangUp.mockReset() })
afterEach(cleanup)

describe('Live calls', () => {
  it('shows a viewer the calls but none of the controls', () => {
    level = 'viewer'
    render(<Live />)
    expect(screen.getByText('Harbour Dental')).toBeTruthy()
    expect(screen.queryByText('power dialer')).toBeNull()
    for (const name of [/Listen/, /Take over/, /End/]) expect(screen.queryByRole('button', { name })).toBeNull()
  })

  it('ends a call only after the caller confirms', () => {
    level = 'caller'
    render(<Live />)
    expect(screen.getByText('power dialer')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /End/ }))
    expect(hangUp).not.toHaveBeenCalled()
    expect(screen.getByText('End the call with Harbour Dental?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'End the call' }))
    expect(hangUp).toHaveBeenCalledWith('att_1', expect.anything())
  })

  it('takes over only after the caller confirms', () => {
    level = 'caller'
    render(<Live />)
    fireEvent.click(screen.getByRole('button', { name: /Take over/ }))
    expect(join).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Take over' }))
    expect(join).toHaveBeenCalledWith({ id: 'att_1', mode: 'takeover' }, expect.anything())
  })
})
