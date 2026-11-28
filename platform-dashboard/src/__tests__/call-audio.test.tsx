import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Joining a sales call from the browser, with the LiveKit client replaced. What matters here: in dev,
// StrictMode mounts the component, cleans it up and mounts it again, and the token must still join once. Two rooms with one
// token are one identity, so LiveKit would drop one of them mid-connect ("room connection has been cancelled").
const lk = vi.hoisted(() => {
  const state = { rooms: [] as { connects: number; disconnects: number }[], failWith: null as Error | null }
  class FakeRoom {
    connects = 0
    disconnects = 0
    localParticipant = { setMicrophoneEnabled: async () => {} }
    constructor() { state.rooms.push(this) }
    on() { return this }
    async connect() {
      this.connects += 1
      if (state.failWith) throw state.failWith
    }
    async disconnect() { this.disconnects += 1 }
  }
  return { state, FakeRoom }
})

vi.mock('livekit-client', () => ({
  Room: lk.FakeRoom,
  RoomEvent: { TrackSubscribed: 'ts', TrackUnsubscribed: 'tu', Disconnected: 'd' },
  Track: { Kind: { Audio: 'audio' } },
}))

const { CallAudio } = await import('@/components/sales/call-audio')
const join = { token: 'tok', url: 'wss://lk.example', room: 'out-att_1', identity: 'listen_1', mode: 'listen' as const }

beforeEach(() => { lk.state.rooms.length = 0; lk.state.failWith = null })
afterEach(cleanup)

describe('Joining a call', () => {
  it('a mount cleaned up before the client loaded never joins, so its token joins once', async () => {
    // What StrictMode does in dev: mount, clean up straight away, mount again with the same token.
    const first = render(<CallAudio join={join} title="Calling LLC" onLeave={vi.fn()} />)
    first.unmount()
    const view = render(<CallAudio join={join} title="Calling LLC" onLeave={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Listening. Nobody on the call can hear you.')).toBeTruthy())
    expect(lk.state.rooms.map((r) => r.connects)).toEqual([1])
    view.unmount()
    expect(lk.state.rooms[0].disconnects).toBe(1)                            // closing leaves the call
  })

  it('a join cut off partway says so plainly', async () => {
    lk.state.failWith = Object.assign(new Error('room connection has been cancelled'), { reasonName: 'Cancelled' })
    render(<CallAudio join={join} title="Calling LLC" onLeave={vi.fn()} />)
    await waitFor(() => expect(screen.getByText("Couldn't join the call. It may have just ended.")).toBeTruthy())
    expect(screen.queryByText(/room connection has been cancelled/)).toBeNull()
  })
})
