import { useState, type ComponentType } from 'react'
import { Navigate, NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  BedDouble,
  Bell,
  BookOpen,
  Bot,
  Building2,
  CalendarClock,
  CalendarDays,
  CalendarRange,
  ClipboardList,
  ConciergeBell,
  Contact,
  CreditCard,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  Phone,
  PhoneCall,
  Scissors,
  Settings,
  Sun,
  Users,
  Wrench,
} from 'lucide-react'
import { useAuth } from '@receptionist/shared/auth/auth'
import { BillingBanner } from '@/components/app/billing-banner'
import { ExitToPlatformButton, ImpersonationBanner, ToPlatform } from '@/components/app/to-platform'
import { ErrorBanner } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Sheet, SheetContent, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { useLiveCalls } from '@/lib/hooks'
import { useTenant } from '@/lib/tenant'
import { ROLE_LABEL } from '@/lib/tenant-format'
import type { Role, TenantContext } from '@/lib/tenant-types'
import { useTheme } from '@receptionist/shared/providers/theme'
import { cn } from '@receptionist/shared/lib/utils'

interface NavItem {
  to: string
  label: string
  icon: ComponentType<{ className?: string }>
  end?: boolean
  live?: boolean
  role?: Role
}

/** Navigation adapts to the business: hotels keep rooms and bookings; everyone else gets appointments. */
function navFor(ctx: TenantContext): { title?: string; items: NavItem[] }[] {
  const L = ctx.vertical.labels
  const isHotel = ctx.vertical.key === 'hotel'
  const work: NavItem[] = isHotel
    ? [
        { to: '/bookings', label: 'Bookings', icon: CalendarDays },
        { to: '/calendar', label: 'Availability', icon: CalendarRange },
        { to: '/rooms', label: 'Rooms', icon: BedDouble },
        { to: '/guests', label: 'Guests', icon: Users },
        { to: '/conversations', label: 'Conversations', icon: MessageSquare },
        { to: '/reminders', label: 'Reminders', icon: Bell },
      ]
    : [
        { to: '/appointments', label: L.appointments, icon: CalendarClock },
        ...(ctx.vertical.key === 'home_services' ? [{ to: '/leads', label: 'Job requests', icon: ClipboardList }] : []),
        { to: '/contacts', label: L.contacts, icon: Contact },
        { to: '/services', label: `${L.services} & ${L.resources.toLowerCase()}`, icon: ctx.vertical.key === 'salon' ? Scissors : Wrench, role: 'admin' },
      ]
  return [
    {
      items: [
        { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
        { to: '/calls', label: 'Calls & messages', icon: PhoneCall, live: true },
        ...work,
      ],
    },
    {
      title: 'Your assistant',
      items: [
        { to: '/knowledge', label: 'Knowledge', icon: BookOpen },
        { to: '/assistant', label: 'Assistant', icon: Bot, role: 'admin' },
        { to: '/phone', label: 'Phone numbers', icon: Phone },
      ],
    },
    {
      title: 'Business',
      items: [
        { to: '/settings', label: 'Settings', icon: Settings },
        { to: '/team', label: 'Team', icon: Users },
        ...(ctx.legacy ? [] : [{ to: '/billing', label: 'Plan & billing', icon: CreditCard, role: 'owner' as Role }]),
      ],
    },
  ]
}

/** Pages a business without an active plan can still open (to pay, export or leave). */
const INACTIVE_OK = ['/billing']

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { ctx, can } = useTenant()
  const live = useLiveCalls(!!ctx)
  const liveCount = live.data?.total ?? 0
  const groups = ctx ? navFor(ctx) : []
  return (
    <nav className="flex flex-col gap-4 px-3">
      {groups.map((g, gi) => (
        <div key={gi} className="flex flex-col gap-1">
          {g.title && <div className="px-3 pb-1 text-xs font-medium uppercase tracking-wide text-sidebar-foreground/50">{g.title}</div>}
          {g.items
            .filter((item) => !item.role || can(item.role))
            .map(({ to, label, icon: Icon, end, live: isLive }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                      : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground',
                  )
                }
              >
                <Icon className="size-4" />
                <span className="flex-1">{label}</span>
                {isLive && liveCount > 0 && (
                  <span className="flex items-center gap-1 rounded-full bg-emerald-500/20 px-2 py-0.5 text-xs text-emerald-300">
                    <span className="size-1.5 animate-pulse rounded-full bg-emerald-400" />
                    {liveCount}
                  </span>
                )}
              </NavLink>
            ))}
        </div>
      ))}
    </nav>
  )
}

function Brand() {
  const { ctx } = useTenant()
  return (
    <div className="flex items-center gap-3 px-5 py-5">
      <div className="flex size-9 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
        {ctx?.vertical.key === 'hotel' ? <ConciergeBell className="size-5" /> : <Building2 className="size-5" />}
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-sidebar-foreground">{ctx?.tenant.name ?? 'AI Receptionist'}</div>
        <div className="text-xs text-sidebar-foreground/60">{ctx?.vertical.label.split(',')[0]}</div>
      </div>
    </div>
  )
}

function BusinessSwitcher() {
  const { memberships, tenantId, switchTenant, user } = useAuth()
  const { ctx } = useTenant()
  if (user?.is_platform_admin) {
    if (!ctx || ctx.legacy) return null
    return (
      <div className="px-4 pb-3">
        <ExitToPlatformButton label="← Back to platform" className="w-full" />
      </div>
    )
  }
  if (memberships.length < 2) return null
  return (
    <div className="px-4 pb-3">
      <SimpleSelect
        size="sm"
        className="w-full"
        value={tenantId ?? ''}
        onChange={(v) => void switchTenant(v)}
        options={memberships.map((m) => ({ value: m.tenant_id, label: m.name }))}
      />
    </div>
  )
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const dark = theme === 'dark'
  return (
    <Button variant="ghost" size="icon" aria-label="Toggle theme" onClick={() => setTheme(dark ? 'light' : 'dark')}>
      {dark ? <Sun /> : <Moon />}
    </Button>
  )
}

export function AppLayout() {
  const { user, logout } = useAuth()
  const { ctx, loading, error, needsTenant, refetch } = useTenant()
  const location = useLocation()
  const [menu, setMenu] = useState(false)

  // A platform admin with no business open belongs in the platform admin app.
  if (needsTenant) return <ToPlatform />
  // Not subscribed yet, paused or cancelled: the dashboard's data is locked, billing is not.
  const inactive = ctx && !ctx.platform_admin && ['incomplete', 'suspended', 'cancelled'].includes(ctx.billing?.status ?? '')
  if (inactive && !INACTIVE_OK.includes(location.pathname)) return <Navigate to="/billing" replace />

  const role = ctx ? (ctx.platform_admin ? 'Platform admin' : ROLE_LABEL[ctx.role]) : user?.is_platform_admin ? 'Platform admin' : ''

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[16rem_1fr]">
      <aside className="sticky top-0 hidden h-screen flex-col border-r border-sidebar-border bg-sidebar lg:flex">
        <Brand />
        <BusinessSwitcher />
        <div className="flex-1 overflow-y-auto pb-4">
          <NavList />
        </div>
        <div className="border-t border-sidebar-border p-3">
          <div className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5">
            <div className="min-w-0 text-sm">
              <div className="truncate font-medium text-sidebar-foreground">{user?.name || user?.email}</div>
              <div className="text-xs text-sidebar-foreground/60">{role}</div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Log out"
              className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={() => void logout()}
            >
              <LogOut />
            </Button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <div className="sticky top-0 z-20">
          <ImpersonationBanner />
          <header className="flex h-14 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur lg:px-8">
            <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMenu(true)} aria-label="Menu">
              <Menu />
            </Button>
            <div className="flex-1" />
            <ThemeToggle />
            <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => void logout()} aria-label="Log out">
              <LogOut />
            </Button>
          </header>
        </div>
        <main className="flex-1 px-4 py-6 lg:px-8">
          {error ? (
            <ErrorBanner error={error} onRetry={refetch} />
          ) : loading ? (
            <div className="flex justify-center py-20">
              <Loader2 className="size-6 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <>
              <BillingBanner />
              <Outlet />
            </>
          )}
        </main>
      </div>

      <Sheet open={menu} onOpenChange={setMenu}>
        <SheetContent side="left" className="w-72 border-sidebar-border bg-sidebar p-0 text-sidebar-foreground">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Brand />
          <BusinessSwitcher />
          <NavList onNavigate={() => setMenu(false)} />
        </SheetContent>
      </Sheet>
    </div>
  )
}
