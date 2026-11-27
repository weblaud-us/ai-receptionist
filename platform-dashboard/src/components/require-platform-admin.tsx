import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import { useAuth } from '@receptionist/shared/auth/auth'
import { AuthCard } from '@receptionist/shared/auth/login-pages'
import { Button } from '@receptionist/shared/components/ui/button'
import { CLIENT_URL } from '@receptionist/shared/lib/urls'
import { salesOnly } from '@/lib/roles'

/** Where a sales-team member (not a platform admin) may go: the dialer, their own account and the guides. */
const SALES_PATHS = ['/dialer', '/account', '/help']

/**
 * The whole app is for platform administrators. A business user who signs in here is pointed to
 * their own dashboard. The sales team (SALES_SUITE_PLAN.md Phase 2) gets the Sales dialer only. (The API
 * enforces this too: /platform/* needs a platform admin, /platform/sales a sales role.)
 */
export function RequirePlatformAdmin({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const { pathname } = useLocation()
  if (user?.is_platform_admin) return <>{children}</>
  if (salesOnly(user)) {
    return SALES_PATHS.some((p) => pathname.startsWith(p)) ? <>{children}</> : <Navigate to="/dialer" replace />
  }
  return (
    <AuthCard icon={ShieldAlert} title="Platform administrators only" description={`${user?.email ?? 'This account'} isn't a platform admin.`}>
      <div className="space-y-2">
        <Button className="w-full" nativeButton={false} render={<a href={CLIENT_URL} />}>
          Go to your dashboard
        </Button>
        <Button variant="outline" className="w-full" onClick={() => void logout()}>
          Sign in with another account
        </Button>
      </div>
    </AuthCard>
  )
}
