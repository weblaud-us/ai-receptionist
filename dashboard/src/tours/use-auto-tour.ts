/**
 * Starts a person's welcome tour by itself, once, the first time they open a business in a role.
 *
 * Never for a platform admin looking at a business, never mid-setup or on the billing page, and for
 * owners and admins only after the setup wizard is finished (the wizard comes first). The tour
 * records itself as done or skipped; Help → Take the tour replays it at any time.
 */
import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { useTour } from '@receptionist/shared/components/tour'
import { useTutorials } from '@receptionist/shared/hooks/use-tutorials'
import { useTenant } from '@/lib/tenant'
import type { TenantContext } from '@/lib/tenant-types'
import { tourFor } from './dashboard-tours'

const INACTIVE = ['incomplete', 'suspended', 'cancelled']

export const checklistKey = (ctx: TenantContext) => `checklist:dashboard:getting_started:${ctx.tenant.id}`

/** Whether this person should get their tour now (exported for tests). */
export function shouldAutoTour(ctx: TenantContext, pathname: string, seen: (key: string) => boolean): boolean {
  if (ctx.platform_admin || ctx.legacy) return false
  if (INACTIVE.includes(ctx.billing?.status ?? '')) return false
  if (seen(tourFor(ctx).key)) return false
  const landing = ctx.role === 'dentist' ? ['/', '/my-day'] : ['/']
  if (!landing.includes(pathname)) return false
  if ((ctx.role === 'owner' || ctx.role === 'admin') && !ctx.tenant.onboarding?.completed_at) return false
  return true
}

export function useAutoTour(): void {
  const { ctx } = useTenant()
  const tutorials = useTutorials()
  const tour = useTour()
  const { pathname } = useLocation()
  const fired = useRef(false)
  const ok = !!ctx && tutorials.ready && tutorials.persist && !tour.active && shouldAutoTour(ctx, pathname, tutorials.seen)

  useEffect(() => {
    if (!ok || fired.current || !ctx) return
    // A moment for the page to draw, so the first target is there.
    const timer = setTimeout(() => {
      fired.current = true
      tour.start(tourFor(ctx), ctx)
    }, 800)
    return () => clearTimeout(timer)
  }, [ok, ctx, tour])
}
