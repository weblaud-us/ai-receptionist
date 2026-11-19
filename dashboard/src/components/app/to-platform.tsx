import { useEffect } from 'react'
import { Eye } from 'lucide-react'
import { FullPageSpinner, useAuth } from '@receptionist/shared/auth/auth'
import { Button } from '@receptionist/shared/components/ui/button'
import { PLATFORM_URL } from '@receptionist/shared/lib/urls'
import { useTenant } from '@/lib/tenant'

/**
 * A platform admin with no business open has nothing to see in the client dashboard:
 * send them to the platform admin app.
 */
export function ToPlatform() {
  useEffect(() => {
    window.location.replace(PLATFORM_URL)
  }, [])
  return <FullPageSpinner />
}

/** Leave the business and go back to the platform admin app. */
export function ExitToPlatformButton({ label = 'Exit to platform', className }: { label?: string; className?: string }) {
  const { switchTenant } = useAuth()
  const exit = async () => {
    await switchTenant(null)
    window.location.assign(PLATFORM_URL)
  }
  return (
    <Button size="xs" variant="outline" className={className} onClick={() => void exit()}>
      {label}
    </Button>
  )
}

/** Always-visible notice while a platform admin is acting inside a business. */
export function ImpersonationBanner() {
  const { ctx } = useTenant()
  if (!ctx?.platform_admin || ctx.legacy) return null
  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-amber-500/40 bg-amber-50 px-4 py-2 text-sm text-amber-950 dark:bg-amber-950 dark:text-amber-50 lg:px-8">
      <Eye className="size-4 shrink-0 text-amber-600" />
      <span className="flex-1">
        You're viewing <span className="font-medium">{ctx.tenant.name}</span> as a platform admin. Every change is audited.
      </span>
      <ExitToPlatformButton />
    </div>
  )
}
