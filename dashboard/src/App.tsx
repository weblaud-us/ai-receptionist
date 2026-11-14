import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { RequireAuth } from '@/auth/auth'
import { AppLayout } from '@/components/app/layout'
import { Skeleton } from '@/components/ui/skeleton'
import { useTenant } from '@/lib/tenant'
import type { Role } from '@/lib/tenant-types'
import Login, { AcceptInvite, ForgotPassword, ResetPassword } from '@/pages/Login'

// One chunk per page keeps the first load small (charts only load with the overviews).
const HotelOverview = lazy(() => import('@/pages/Overview'))
const TenantOverview = lazy(() => import('@/pages/TenantOverview'))
const Bookings = lazy(() => import('@/pages/Bookings'))
const Calendar = lazy(() => import('@/pages/Calendar'))
const Rooms = lazy(() => import('@/pages/Rooms'))
const Conversations = lazy(() => import('@/pages/Conversations'))
const Guests = lazy(() => import('@/pages/Guests'))
const Calls = lazy(() => import('@/pages/Calls'))
const Reminders = lazy(() => import('@/pages/Reminders'))
const SettingsPage = lazy(() => import('@/pages/Settings'))
const Appointments = lazy(() => import('@/pages/Appointments'))
const Services = lazy(() => import('@/pages/Services'))
const Contacts = lazy(() => import('@/pages/Contacts'))
const Leads = lazy(() => import('@/pages/Leads'))
const Knowledge = lazy(() => import('@/pages/Knowledge'))
const Assistant = lazy(() => import('@/pages/Assistant'))
const PhoneNumbers = lazy(() => import('@/pages/PhoneNumbers'))
const Team = lazy(() => import('@/pages/Team'))
const Platform = lazy(() => import('@/pages/Platform'))
const Onboarding = lazy(() => import('@/pages/Onboarding'))

const Fallback = () => (
  <div className="space-y-4">
    <Skeleton className="h-8 w-56" />
    <Skeleton className="h-64 w-full" />
  </div>
)

/** Hotel tenants keep the hotel overview; every other business gets the generic one. */
function Home() {
  const { ctx } = useTenant()
  if (!ctx) return <Navigate to="/platform" replace />
  return ctx.vertical.key === 'hotel' ? <HotelOverview /> : <TenantOverview />
}

/** Pages that only exist for some businesses or roles. */
function Only({ hotel, scheduling, role, children }: { hotel?: boolean; scheduling?: boolean; role?: Role; children: ReactNode }) {
  const { ctx, can } = useTenant()
  if (!ctx) return <Navigate to="/platform" replace />
  if (hotel && ctx.vertical.key !== 'hotel') return <Navigate to="/" replace />
  if (scheduling && !ctx.vertical.uses_scheduling) return <Navigate to="/" replace />
  if (role && !can(role)) return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Suspense fallback={<Fallback />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Home />} />
          <Route path="calls" element={<Only><Calls /></Only>} />
          {/* hotel vertical */}
          <Route path="bookings" element={<Only hotel><Bookings /></Only>} />
          <Route path="calendar" element={<Only hotel><Calendar /></Only>} />
          <Route path="rooms" element={<Only hotel><Rooms /></Only>} />
          <Route path="guests" element={<Only hotel><Guests /></Only>} />
          <Route path="conversations" element={<Only><Conversations /></Only>} />
          <Route path="reminders" element={<Only hotel><Reminders /></Only>} />
          {/* every other vertical */}
          <Route path="appointments" element={<Only scheduling><Appointments /></Only>} />
          <Route path="services" element={<Only scheduling role="admin"><Services /></Only>} />
          <Route path="contacts" element={<Only scheduling><Contacts /></Only>} />
          <Route path="leads" element={<Only scheduling><Leads /></Only>} />
          {/* everyone */}
          <Route path="knowledge" element={<Only><Knowledge /></Only>} />
          <Route path="assistant" element={<Only role="admin"><Assistant /></Only>} />
          <Route path="phone" element={<Only><PhoneNumbers /></Only>} />
          <Route path="team" element={<Only><Team /></Only>} />
          <Route path="settings" element={<Only><SettingsPage /></Only>} />
          <Route path="onboarding" element={<Only role="admin"><Onboarding /></Only>} />
          <Route path="platform" element={<Platform />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
