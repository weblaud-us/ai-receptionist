import { Suspense, useState, type ComponentType } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Building2, ChartNoAxesCombined, ClipboardCheck, ExternalLink, Hash, Loader2, LogOut, Menu, Moon, PhoneOutgoing, Rocket, Scale, ShieldCheck, Sun } from 'lucide-react'
import { useAuth } from '@receptionist/shared/auth/auth'
import { ErrorBoundary } from '@receptionist/shared/components/error-boundary'
import { Button } from '@receptionist/shared/components/ui/button'
import { Sheet, SheetContent, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { CLIENT_URL } from '@receptionist/shared/lib/urls'
import { cn } from '@receptionist/shared/lib/utils'
import { useTheme } from '@receptionist/shared/providers/theme'

interface NavItem {
  to: string
  label: string
  icon: ComponentType<{ className?: string }>
  end?: boolean
  /** Also highlight under this path prefix (e.g. a list's detail pages). */
  also?: string
}

const NAV: NavItem[] = [
  { to: '/', label: 'Businesses', icon: Building2, end: true, also: '/businesses/' },
  { to: '/numbers', label: 'Numbers', icon: Hash },
  { to: '/economics', label: 'Unit economics', icon: ChartNoAxesCombined },
  { to: '/dialer', label: 'Sales dialer', icon: PhoneOutgoing },
  { to: '/pilots', label: 'Pilots', icon: Rocket },
  { to: '/reviews', label: 'Transcript review', icon: ClipboardCheck },
  { to: '/compliance', label: 'Compliance', icon: Scale },
]

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { pathname } = useLocation()
  return (
    <nav className="flex flex-col gap-1 px-3">
      {NAV.map(({ to, label, icon: Icon, end, also }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
              isActive || (also && pathname.startsWith(also))
                ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground',
            )
          }
        >
          <Icon className="size-4" />
          <span className="flex-1">{label}</span>
        </NavLink>
      ))}
      <a
        href={CLIENT_URL}
        target="_blank"
        rel="noopener"
        className="mt-3 flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-sidebar-foreground/60 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
      >
        <ExternalLink className="size-4" />
        <span className="flex-1">Client dashboard</span>
      </a>
    </nav>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-3 px-5 py-5">
      <div className="flex size-9 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
        <ShieldCheck className="size-5" />
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-sidebar-foreground">AI Receptionist</div>
        <div className="text-xs text-sidebar-foreground/60">Platform admin</div>
      </div>
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

const PageLoading = () => (
  <div className="flex justify-center py-20">
    <Loader2 className="size-6 animate-spin text-muted-foreground" />
  </div>
)

export function PlatformLayout() {
  const { pathname } = useLocation()
  const { user, logout } = useAuth()
  const [menu, setMenu] = useState(false)

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[16rem_1fr]">
      <aside className="sticky top-0 hidden h-screen flex-col border-r border-sidebar-border bg-sidebar lg:flex">
        <Brand />
        <div className="flex-1 overflow-y-auto pb-4">
          <NavList />
        </div>
        <div className="border-t border-sidebar-border p-3">
          <div className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5">
            <div className="min-w-0 text-sm">
              <div className="truncate font-medium text-sidebar-foreground">{user?.name || user?.email}</div>
              <div className="text-xs text-sidebar-foreground/60">Platform admin</div>
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
        <header className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur lg:px-8">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMenu(true)} aria-label="Menu">
            <Menu />
          </Button>
          <div className="flex-1" />
          <ThemeToggle />
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => void logout()} aria-label="Log out">
            <LogOut />
          </Button>
        </header>
        <main className="flex-1 px-4 py-6 lg:px-8">
          <Suspense fallback={<PageLoading />}>
            <ErrorBoundary scope="page" key={pathname}><Outlet /></ErrorBoundary>
          </Suspense>
        </main>
      </div>

      <Sheet open={menu} onOpenChange={setMenu}>
        <SheetContent side="left" className="w-72 border-sidebar-border bg-sidebar p-0 text-sidebar-foreground">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Brand />
          <NavList onNavigate={() => setMenu(false)} />
        </SheetContent>
      </Sheet>
    </div>
  )
}
