import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import { RequireAuth } from '@/auth/auth'
import { AppLayout } from '@/components/app/layout'
import { Skeleton } from '@/components/ui/skeleton'
import Login from '@/pages/Login'

// One chunk per page keeps the first load small (charts only load with the Overview).
const Overview = lazy(() => import('@/pages/Overview'))
const Bookings = lazy(() => import('@/pages/Bookings'))
const Calendar = lazy(() => import('@/pages/Calendar'))
const Rooms = lazy(() => import('@/pages/Rooms'))
const Conversations = lazy(() => import('@/pages/Conversations'))
const Guests = lazy(() => import('@/pages/Guests'))
const Calls = lazy(() => import('@/pages/Calls'))
const Reminders = lazy(() => import('@/pages/Reminders'))
const SettingsPage = lazy(() => import('@/pages/Settings'))

const Fallback = () => (
  <div className="space-y-4">
    <Skeleton className="h-8 w-56" />
    <Skeleton className="h-64 w-full" />
  </div>
)

export default function App() {
  return (
    <Suspense fallback={<Fallback />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Overview />} />
          <Route path="bookings" element={<Bookings />} />
          <Route path="calendar" element={<Calendar />} />
          <Route path="rooms" element={<Rooms />} />
          <Route path="conversations" element={<Conversations />} />
          <Route path="guests" element={<Guests />} />
          <Route path="calls" element={<Calls />} />
          <Route path="reminders" element={<Reminders />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Overview />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
