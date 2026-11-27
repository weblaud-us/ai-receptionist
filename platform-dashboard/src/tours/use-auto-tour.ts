/** Starts a platform admin's or sales team member's welcome tour once, on their landing page. */
import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth, type AuthUser } from '@receptionist/shared/auth/auth'
import { useTour } from '@receptionist/shared/components/tour'
import { useTutorials } from '@receptionist/shared/hooks/use-tutorials'
import { landingFor, tourFor } from './platform-tours'

export function shouldAutoTour(user: AuthUser | null, pathname: string, seen: (key: string) => boolean): boolean {
  if (!user) return false
  const def = tourFor(user)
  return !!def && !seen(def.key) && pathname === landingFor(user)
}

export function useAutoTour(): void {
  const { user } = useAuth()
  const tutorials = useTutorials()
  const tour = useTour()
  const { pathname } = useLocation()
  const fired = useRef(false)
  const ok = tutorials.ready && tutorials.persist && !tour.active && shouldAutoTour(user, pathname, tutorials.seen)

  useEffect(() => {
    if (!ok || fired.current || !user) return
    const def = tourFor(user)
    if (!def) return
    const timer = setTimeout(() => {
      fired.current = true
      tour.start(def, user)
    }, 800)
    return () => clearTimeout(timer)
  }, [ok, user, tour])
}
