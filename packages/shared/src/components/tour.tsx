/**
 * The welcome-tour engine: dims the page, cuts a spotlight around one element at a time and shows a
 * small card beside it with Back / Next / Skip. Look-only: the page underneath is `inert` while the
 * tour runs, so nothing can be clicked by accident. Progress is saved per person (useTutorials).
 *
 * Steps come from lib/tour.ts. Mark elements with data-tour="<id>". Esc skips; ← → move.
 */
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { BookOpen, Menu, X } from 'lucide-react'
import { toast } from 'sonner'
import { useTutorials, type TutorialStatus } from '../hooks/use-tutorials'
import { defaultIsVisible, placeCard, resolveSteps, resolveTarget, type Box, type ResolvedStep, type TourDef } from '../lib/tour'
import { cn } from '../lib/utils'
import { Button } from './ui/button'

interface TourApi {
  start: <C>(def: TourDef<C>, c: C) => void
  stop: () => void
  active: { key: string; index: number; total: number } | null
}

const TourContext = createContext<TourApi | null>(null)

// eslint-disable-next-line react-refresh/only-export-components
export function useTour(): TourApi {
  const v = useContext(TourContext)
  if (!v) throw new Error('useTour must be used inside <TourProvider>')
  return v
}

interface Running { key: string; steps: ResolvedStep[] }

const reducedMotion = () =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

export function TourProvider({ children, appRootId = 'root', isVisible = defaultIsVisible, onStatus }: {
  children: ReactNode
  /** The app's root element: made inert while a tour runs (the card is portalled outside it). */
  appRootId?: string
  /** Injectable for tests (jsdom has no layout). */
  isVisible?: (el: Element) => boolean
  /** Where a finished or skipped tour is recorded. Defaults to the person's saved progress. */
  onStatus?: (key: string, status: TutorialStatus) => void
}) {
  const tutorials = useTutorials()
  const save = useRef(onStatus ?? tutorials.mark)
  useLayoutEffect(() => { save.current = onStatus ?? tutorials.mark })
  const [running, setRunning] = useState<Running | null>(null)
  const [index, setIndex] = useState(0)
  const opener = useRef<Element | null>(null)
  const current = useRef<Running | null>(null)

  const start = useCallback(<C,>(def: TourDef<C>, c: C) => {
    const steps = resolveSteps(def.steps, c)
    if (!steps.length) return
    if (!current.current) opener.current = document.activeElement
    current.current = { key: def.key, steps }
    setIndex(0)
    setRunning(current.current)
  }, [])

  const finish = useCallback((status: TutorialStatus | null) => {
    const r = current.current
    current.current = null
    setRunning(null)
    if (r && status) {
      save.current(r.key, status)
      if (status === 'skipped') toast('You can take the tour again from Help.')
    }
  }, [])

  // The page underneath can't be used while the tour runs; focus goes back afterwards.
  useEffect(() => {
    if (!running) return
    const root = document.getElementById(appRootId)
    root?.setAttribute('inert', '')
    return () => {
      root?.removeAttribute('inert')
      const back = opener.current as HTMLElement | null
      if (back && typeof back.focus === 'function' && document.contains(back)) back.focus()
    }
  }, [running, appRootId])

  const api = useMemo<TourApi>(() => ({
    start,
    stop: () => finish(null),
    active: running ? { key: running.key, index, total: running.steps.length } : null,
  }), [start, finish, running, index])

  return (
    <TourContext.Provider value={api}>
      {children}
      {running && createPortal(
        <TourOverlay key={running.key} steps={running.steps} index={index} setIndex={setIndex} finish={finish} isVisible={isVisible} />,
        document.body,
      )}
    </TourContext.Provider>
  )
}

/** What the current step points at; `index` ties it to its step, so a stale one is never shown. */
type View = { index: number; el: Element | null; hint: boolean }

function useViewport() {
  const [vp, setVp] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }))
  useEffect(() => {
    const on = () => setVp({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return vp
}

function TourOverlay({ steps, index, setIndex, finish, isVisible }: {
  steps: ResolvedStep[]
  index: number
  setIndex: (i: number) => void
  finish: (status: TutorialStatus | null) => void
  isVisible: (el: Element) => boolean
}) {
  const step = steps[index]
  const navigate = useNavigate()
  const location = useLocation()
  const path = useRef(location.pathname)
  useLayoutEffect(() => { path.current = location.pathname })
  const dir = useRef<1 | -1>(1)
  const [found, setView] = useState<View | null>(null)
  const view = found?.index === index ? found : null
  const [measured, setBox] = useState<(Box & { el: Element }) | null>(null)
  const box = view?.el && measured?.el === view.el ? measured : null
  const [card, setCard] = useState({ w: 352, h: 190 })
  const cardRef = useRef<HTMLDivElement>(null)
  const titleRef = useRef<HTMLHeadingElement>(null)
  const vp = useViewport()
  const docked = vp.w < 640

  const go = useCallback((d: 1 | -1) => {
    dir.current = d
    const next = index + d
    if (next >= steps.length) finish('done')
    else if (next >= 0) setIndex(next)
  }, [index, steps.length, finish, setIndex])

  // Find this step's target: open its page if it has one, wait for the element, skip it if the
  // person's role or business type doesn't have it.
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (step.route && path.current !== step.route) navigate(step.route)
      const until = Date.now() + (step.route ? step.waitMs : 300)
      let found = resolveTarget(step.targets, document, isVisible)
      while (found === 'absent' && Date.now() < until && !cancelled) {
        await new Promise((r) => setTimeout(r, 100))
        found = resolveTarget(step.targets, document, isVisible)
      }
      if (cancelled) return
      if (found === 'absent') {
        const next = index + dir.current
        if (next >= steps.length) finish('done')
        else if (next < 0) { dir.current = 1; setIndex(index + 1 < steps.length ? index + 1 : index) }
        else setIndex(next)
        return
      }
      if (found === 'hidden') {
        setView({ index, el: null, hint: step.targets.length > 0 })
        return
      }
      const el = found.el as HTMLElement
      if (typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' })
      setView({ index, el, hint: found.fallback })
    }
    void run()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, steps])

  // Follow the target as the page scrolls or resizes.
  useLayoutEffect(() => {
    const el = view?.el
    if (!el) return
    let raf = 0
    const read = () => {
      const r = el.getBoundingClientRect()
      setBox({ el, top: r.top, left: r.left, width: r.width, height: r.height })
    }
    const measure = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(read) }
    read()
    window.addEventListener('scroll', measure, true)
    window.addEventListener('resize', measure)
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
    ro?.observe(el)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('scroll', measure, true)
      window.removeEventListener('resize', measure)
      ro?.disconnect()
    }
  }, [view?.el])

  // The card's real size, so it can be placed beside the target without running off the screen.
  useEffect(() => {
    const c = cardRef.current
    if (!c || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      if (c.offsetWidth > 0) setCard((p) => (p.w === c.offsetWidth && p.h === c.offsetHeight ? p : { w: c.offsetWidth, h: c.offsetHeight }))
    })
    ro.observe(c)
    return () => ro.disconnect()
  }, [view])

  useEffect(() => {
    if (view) titleRef.current?.focus({ preventScroll: true })
  }, [view, index])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); finish('skipped') }
      else if (e.key === 'ArrowRight') { e.preventDefault(); go(1) }
      else if (e.key === 'ArrowLeft' && index > 0) { e.preventDefault(); go(-1) }
      else if (e.key === 'Enter' && e.target === titleRef.current) { e.preventDefault(); go(1) }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [go, finish, index])

  const pad = 6
  const pos = placeCard(box, card, vp, step.placement ?? (step.targets[0]?.startsWith('nav-') ? 'right' : 'bottom'))
  const last = index === steps.length - 1
  const titleId = `tour-title-${step.id}`
  const bodyId = `tour-body-${step.id}`

  return (
    <>
      <svg className="fixed inset-0 z-[60] size-full" aria-hidden="true" onClick={(e) => e.preventDefault()}>
        <defs>
          <mask id="tour-spotlight">
            <rect width="100%" height="100%" fill="white" />
            {box && <rect x={box.left - pad} y={box.top - pad} width={box.width + pad * 2} height={box.height + pad * 2} rx={8} fill="black"
              className="transition-all duration-200 motion-reduce:transition-none" />}
          </mask>
        </defs>
        <rect width="100%" height="100%" fill="rgb(0 0 0 / 0.5)" mask="url(#tour-spotlight)" />
        {box && <rect x={box.left - pad} y={box.top - pad} width={box.width + pad * 2} height={box.height + pad * 2} rx={8}
          fill="none" stroke="var(--color-primary, #6366f1)" strokeWidth={2} className="transition-all duration-200 motion-reduce:transition-none" />}
      </svg>
      {view && (
        <div ref={cardRef} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={bodyId}
          className={cn('fixed z-[61] rounded-xl border bg-popover p-4 text-popover-foreground shadow-xl',
            docked ? 'inset-x-3 bottom-3' : 'w-[22rem] transition-[top,left] duration-200 motion-reduce:transition-none')}
          style={docked ? undefined : { top: pos.top, left: pos.left }}>
          <div className="flex items-start justify-between gap-3">
            <h2 id={titleId} ref={titleRef} tabIndex={-1} className="font-semibold outline-none">{step.title}</h2>
            <button type="button" aria-label="Skip tour" onClick={() => finish('skipped')}
              className="-m-1 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
              <X className="size-4" />
            </button>
          </div>
          <div id={bodyId} className="mt-1.5 text-sm text-muted-foreground">{step.body}</div>
          {view.hint && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground"><Menu className="size-3.5" /> Find it in the menu.</p>
          )}
          <div className="mt-4 flex items-center gap-2">
            <span className="text-xs text-muted-foreground" aria-live="polite">{index + 1} of {steps.length}</span>
            {step.learnMore && (
              <Link to={`/help/${step.learnMore}`} onClick={() => finish('done')}
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                <BookOpen className="size-3.5" /> Learn more
              </Link>
            )}
            <div className="ml-auto flex gap-2">
              {index > 0 && <Button size="sm" variant="ghost" onClick={() => go(-1)}>Back</Button>}
              <Button size="sm" onClick={() => go(1)}>{last ? 'Done' : 'Next'}</Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
