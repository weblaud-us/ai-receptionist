import type { ReactNode } from 'react'
import { ShieldAlert } from 'lucide-react'
import { useAuth } from '@receptionist/shared/auth/auth'
import { AuthCard } from '@receptionist/shared/auth/login-pages'
import { Button } from '@receptionist/shared/components/ui/button'
import { CLIENT_URL } from '@receptionist/shared/lib/urls'

/**
 * The whole app is for platform administrators. A business user who signs in here is pointed to
 * their own dashboard. (The API enforces this too: every /platform/* route needs a platform admin.)
 */
export function RequirePlatformAdmin({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  if (user?.is_platform_admin) return <>{children}</>
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
