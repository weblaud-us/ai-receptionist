import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { ShieldCheck } from 'lucide-react'
import { RequireAuth } from '@receptionist/shared/auth/auth'
import { ConfirmEmailChange, ForgotPassword, LoginPage, ResetPassword } from '@receptionist/shared/auth/login-pages'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { PlatformLayout } from '@/components/layout'
import { RequirePlatformAdmin } from '@/components/require-platform-admin'

const Businesses = lazy(() => import('@/pages/Businesses'))
const BusinessDetail = lazy(() => import('@/pages/BusinessDetail'))
const NewBusiness = lazy(() => import('@/pages/NewBusiness'))
const Economics = lazy(() => import('@/pages/Economics'))
const Dialer = lazy(() => import('@/pages/Dialer'))
const Pilots = lazy(() => import('@/pages/Pilots'))
const Reviews = lazy(() => import('@/pages/Reviews'))
const Compliance = lazy(() => import('@/pages/Compliance'))
const Legal = lazy(() => import('@/pages/Legal'))
const Numbers = lazy(() => import('@/pages/Numbers'))
const DemoLeads = lazy(() => import('@/pages/DemoLeads'))
const Account = lazy(() => import('@/pages/Account'))
const Help = lazy(() => import('@/pages/Help'))
const HelpArticle = lazy(() => import('@/pages/Help').then((m) => ({ default: m.HelpArticle })))

const Fallback = () => (
  <div className="space-y-4">
    <Skeleton className="h-8 w-56" />
    <Skeleton className="h-64 w-full" />
  </div>
)

function Login() {
  return <LoginPage icon={ShieldCheck} title="Platform admin" description="Sign in to manage businesses, numbers and the sales dialer." />
}

export default function App() {
  return (
    <Suspense fallback={<Fallback />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/confirm-email" element={<ConfirmEmailChange />} />
        <Route
          element={
            <RequireAuth>
              <RequirePlatformAdmin>
                <PlatformLayout />
              </RequirePlatformAdmin>
            </RequireAuth>
          }
        >
          <Route index element={<Businesses />} />
          <Route path="businesses/new" element={<NewBusiness />} />
          <Route path="businesses/:id" element={<BusinessDetail />} />
          <Route path="economics" element={<Economics />} />
          <Route path="dialer" element={<Dialer />} />
          <Route path="demo-leads" element={<DemoLeads />} />
          <Route path="pilots" element={<Pilots />} />
          <Route path="reviews" element={<Reviews />} />
          <Route path="compliance" element={<Compliance />} />
          <Route path="legal" element={<Legal />} />
          <Route path="numbers" element={<Numbers />} />
          <Route path="account" element={<Account />} />
          <Route path="help" element={<Help />} />
          <Route path="help/:slug" element={<HelpArticle />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
