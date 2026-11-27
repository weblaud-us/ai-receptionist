/**
 * Welcome tours: the step model and the pure parts of the engine (components/tour.tsx draws them).
 *
 * A step points at an element marked `data-tour="<id>"`. `target` lists ids in order: the first is
 * the real thing (e.g. "nav-inbox"); the rest are fallbacks used when it exists but isn't visible
 * (on a phone the sidebar is closed, so "menu-button"). A step whose first target isn't on the page
 * at all is skipped: that person's role or business type doesn't have it.
 */
import type { ReactNode } from 'react'

export type TourPlacement = 'top' | 'bottom' | 'left' | 'right' | 'center'

export interface TourStep<C> {
  id: string
  target?: string | string[]
  title: string | ((c: C) => string)
  body: ReactNode | ((c: C) => ReactNode)
  placement?: TourPlacement
  /** Open this page first (the target may be on it). */
  route?: string
  /** Only for some people: checked once, when the tour starts. */
  when?: (c: C) => boolean
  /** A help article's slug, offered as "Learn more". */
  learnMore?: string
  /** How long to wait for the target after opening `route` (pages load in chunks). */
  waitMs?: number
}

export interface TourDef<C> {
  /** The tutorial key progress is saved under, e.g. "tour:dashboard:owner". */
  key: string
  steps: TourStep<C>[]
}

export interface ResolvedStep {
  id: string
  targets: string[]
  title: string
  body: ReactNode
  placement?: TourPlacement
  route?: string
  learnMore?: string
  waitMs: number
}

export function resolveSteps<C>(steps: TourStep<C>[], c: C): ResolvedStep[] {
  return steps
    .filter((s) => !s.when || s.when(c))
    .map((s) => ({
      id: s.id,
      targets: s.target ? (Array.isArray(s.target) ? s.target : [s.target]) : [],
      title: typeof s.title === 'function' ? s.title(c) : s.title,
      body: typeof s.body === 'function' ? (s.body as (c: C) => ReactNode)(c) : s.body,
      placement: s.placement,
      route: s.route,
      learnMore: s.learnMore,
      waitMs: s.waitMs ?? 1500,
    }))
}

export const tourSelector = (id: string) =>
  `[data-tour="${typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(id) : id.replace(/["\\]/g, '\\$&')}"]`

export function defaultIsVisible(el: Element): boolean {
  const check = (el as Element & { checkVisibility?: () => boolean }).checkVisibility
  if (typeof check === 'function') return check.call(el)
  return el.getClientRects().length > 0
}

export type TargetResult = { el: Element; fallback: boolean } | 'absent' | 'hidden'

/**
 * Where a step points. 'absent': the first target isn't on the page (skip the step).
 * 'hidden': it's there but nothing visible stands in for it (show the card in the middle).
 */
export function resolveTarget(ids: string[], root: ParentNode, isVisible: (el: Element) => boolean): TargetResult {
  if (!ids.length) return 'hidden'
  const first = Array.from(root.querySelectorAll(tourSelector(ids[0])))
  if (!first.length) return 'absent'
  const shown = first.find(isVisible)
  if (shown) return { el: shown, fallback: false }
  for (const id of ids.slice(1)) {
    const el = Array.from(root.querySelectorAll(tourSelector(id))).find(isVisible)
    if (el) return { el, fallback: true }
  }
  return 'hidden'
}

export interface Box { top: number; left: number; width: number; height: number }

/**
 * Where the step card goes: beside the target on the preferred side, flipped to the other side when
 * it doesn't fit, then kept inside the window. No target (or 'center'): the middle of the window.
 */
export function placeCard(
  target: Box | null,
  card: { w: number; h: number },
  vp: { w: number; h: number },
  placement: TourPlacement = 'bottom',
  gap = 12,
  margin = 12,
): { top: number; left: number; side: TourPlacement } {
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, Math.max(lo, hi)))
  if (!target || placement === 'center') {
    return { top: clamp((vp.h - card.h) / 2, margin, vp.h - card.h - margin), left: clamp((vp.w - card.w) / 2, margin, vp.w - card.w - margin), side: 'center' }
  }
  const room = {
    top: target.top - gap - margin,
    bottom: vp.h - (target.top + target.height) - gap - margin,
    left: target.left - gap - margin,
    right: vp.w - (target.left + target.width) - gap - margin,
  }
  const fits = (s: Exclude<TourPlacement, 'center'>) => (s === 'top' || s === 'bottom' ? room[s] >= card.h : room[s] >= card.w)
  const opposite = { top: 'bottom', bottom: 'top', left: 'right', right: 'left' } as const
  const order: Exclude<TourPlacement, 'center'>[] = [placement, opposite[placement], ...(['bottom', 'top', 'right', 'left'] as const)]
  const side = order.find(fits) ?? (room.bottom >= room.top ? 'bottom' : 'top')
  let top: number
  let left: number
  if (side === 'top' || side === 'bottom') {
    top = side === 'bottom' ? target.top + target.height + gap : target.top - gap - card.h
    left = target.left + target.width / 2 - card.w / 2
  } else {
    left = side === 'right' ? target.left + target.width + gap : target.left - gap - card.w
    top = target.top + target.height / 2 - card.h / 2
  }
  return { top: clamp(top, margin, vp.h - card.h - margin), left: clamp(left, margin, vp.w - card.w - margin), side }
}
