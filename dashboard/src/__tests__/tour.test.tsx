// The welcome-tour engine (packages/shared/src/components/tour.tsx): moving through steps, skipping
// what a person's role doesn't have, saving done/skipped, and leaving the page usable afterwards.
import { useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TourProvider, useTour } from '@receptionist/shared/components/tour'
import { placeCard, type TourDef } from '@receptionist/shared/lib/tour'

vi.mock('@receptionist/shared/auth/auth', () => ({
  useAuth: () => ({ status: 'authenticated', tenantId: 'ten_1', user: { id: 'usr_1', is_platform_admin: false } }),
}))
vi.mock('@receptionist/shared/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@receptionist/shared/lib/api')>()
  return { ...actual, api: vi.fn(async () => ({ tutorials: {}, persist: true })) }
})

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})

/** jsdom has no layout: an element is "visible" unless it (or a parent) is marked data-hidden. */
const isVisible = (el: Element) => !el.closest('[data-hidden]')

type C = { name: string; dental: boolean }
const CTX: C = { name: 'Harbour Smiles', dental: false }

const TOUR: TourDef<C> = {
  key: 'tour:dashboard:staff',
  steps: [
    { id: 'welcome', title: (c) => `Welcome to ${c.name}`, body: 'Hello', placement: 'center' },
    { id: 'inbox', target: ['nav-inbox', 'menu-button'], title: 'Inbox', body: 'Callbacks' },
    { id: 'my-day', target: 'nav-my-day', title: "Dentists' day", body: 'Lists', when: (c) => c.dental },
    { id: 'missing', target: 'nav-not-here', title: 'Not for you', body: 'Skipped' },
    { id: 'calls', target: 'nav-calls', title: 'Calls', body: 'Transcripts', learnMore: 'inbox' },
  ],
}

function Starter({ def = TOUR }: { def?: TourDef<C> }) {
  const tour = useTour()
  return <button onClick={() => tour.start(def, CTX)}>Take the tour</button>
}

function setup({ hiddenInbox = false, def }: { hiddenInbox?: boolean; def?: TourDef<C> } = {}) {
  const onStatus = vi.fn()
  const root = document.createElement('div')
  root.id = 'root'
  document.body.appendChild(root)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <TourProvider onStatus={onStatus} isVisible={isVisible}>
          <nav>
            <a data-tour="nav-inbox" href="/inbox" {...(hiddenInbox ? { 'data-hidden': '' } : {})}>Inbox</a>
            <a data-tour="nav-calls" href="/calls">Calls</a>
          </nav>
          <button data-tour="menu-button">Menu</button>
          <Routes>
            <Route path="/" element={<Starter def={def} />} />
            <Route path="/help/:slug" element={<p>Help article</p>} />
            <Route path="/knowledge" element={<KnowledgePage />} />
          </Routes>
        </TourProvider>
      </MemoryRouter>
    </QueryClientProvider>,
    { container: root },
  )
  return { onStatus, root }
}

function KnowledgePage() {
  useEffect(() => {}, [])
  return <h1 data-tour="knowledge-page">Knowledge page</h1>
}

const step = async (title: string) => expect(await screen.findByRole('dialog', { name: title }, { timeout: 4000 })).toBeTruthy()
const next = () => fireEvent.click(screen.getByRole('button', { name: /^(Next|Done)$/ }))

describe('tour engine', () => {
  it('walks every step it can show, skips what this person lacks, and saves done', async () => {
    const { onStatus, root } = setup()
    const opener = screen.getByText('Take the tour')
    opener.focus()                       // a real click focuses the button; fireEvent doesn't
    fireEvent.click(opener)
    await step('Welcome to Harbour Smiles')
    expect(root.hasAttribute('inert')).toBe(true)
    expect(screen.getByText('1 of 4')).toBeTruthy()   // "my-day" filtered out by `when`
    next()
    await step('Inbox')
    next()
    // "missing" has no target on the page: skipped straight to Calls.
    await step('Calls')
    expect(screen.getByText('4 of 4')).toBeTruthy()
    next()
    await vi.waitFor(() => expect(onStatus).toHaveBeenCalledWith('tour:dashboard:staff', 'done'), { timeout: 4000 })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(root.hasAttribute('inert')).toBe(false)
    expect(document.activeElement).toBe(opener)   // focus goes back
  })

  it('goes back over a skipped step, and Escape skips the tour', async () => {
    const { onStatus } = setup()
    fireEvent.click(screen.getByText('Take the tour'))
    await step('Welcome to Harbour Smiles')
    next()
    await step('Inbox')
    next()
    await step('Calls')
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    await step('Inbox')
    fireEvent.keyDown(document, { key: 'Escape' })
    await vi.waitFor(() => expect(onStatus).toHaveBeenCalledWith('tour:dashboard:staff', 'skipped'), { timeout: 4000 })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('points at the menu button when the item is there but hidden (a phone)', async () => {
    setup({ hiddenInbox: true })
    fireEvent.click(screen.getByText('Take the tour'))
    await step('Welcome to Harbour Smiles')
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    await step('Inbox')
    expect(screen.getByText('Find it in the menu.')).toBeTruthy()
  })

  it('labels the card for screen readers and offers Learn more, which finishes the tour', async () => {
    const { onStatus } = setup()
    fireEvent.click(screen.getByText('Take the tour'))
    await step('Welcome to Harbour Smiles')
    const dialog = screen.getByRole('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(document.getElementById(dialog.getAttribute('aria-describedby')!)?.textContent).toBe('Hello')
    next(); await step('Inbox'); next(); await step('Calls')
    fireEvent.click(screen.getByText('Learn more'))
    await vi.waitFor(() => expect(onStatus).toHaveBeenCalledWith('tour:dashboard:staff', 'done'), { timeout: 4000 })
    expect(await screen.findByText('Help article')).toBeTruthy()
  })

  it('opens a step\'s page before pointing at something on it', async () => {
    setup({ def: { key: 'tour:dashboard:admin', steps: [
      { id: 'k', target: 'knowledge-page', route: '/knowledge', title: 'Knowledge', body: 'Answers' },
    ] } })
    fireEvent.click(screen.getByText('Take the tour'))
    await step('Knowledge')
    expect(screen.getByText('Knowledge page')).toBeTruthy()
  })

  it('ends as done when nothing in the tour exists for this person', async () => {
    const { onStatus } = setup({ def: { key: 'tour:dashboard:dentist', steps: [
      { id: 'x', target: 'nav-nothing', title: 'Nope', body: '' },
    ] } })
    await act(async () => { fireEvent.click(screen.getByText('Take the tour')) })
    await vi.waitFor(() => expect(onStatus).toHaveBeenCalledWith('tour:dashboard:dentist', 'done'), { timeout: 4000 })
  })
})

describe('placeCard', () => {
  const vp = { w: 1200, h: 800 }
  const card = { w: 300, h: 150 }

  it('sits beside the target on the side asked for', () => {
    const p = placeCard({ top: 100, left: 20, width: 200, height: 36 }, card, vp, 'right')
    expect(p.side).toBe('right')
    expect(p.left).toBe(20 + 200 + 12)
  })

  it('flips when there is no room, and stays inside the window', () => {
    const p = placeCard({ top: 760, left: 1100, width: 80, height: 30 }, card, vp, 'bottom')
    expect(p.side).toBe('top')
    expect(p.left + card.w).toBeLessThanOrEqual(vp.w - 12)
    expect(p.top).toBeGreaterThanOrEqual(12)
  })

  it('centres when there is no target', () => {
    expect(placeCard(null, card, vp, 'bottom')).toEqual({ top: 325, left: 450, side: 'center' })
  })
})
