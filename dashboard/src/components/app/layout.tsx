import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import {
  BedDouble,
  CalendarDays,
  CalendarRange,
  Bell,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  PhoneCall,
  Settings,
  Sun,
  Users,
  ConciergeBell,
} from 'lucide-react'
import { useAuth } from '@/auth/auth'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { useLiveCalls, useSystem } from '@/lib/hooks'
import { useTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/bookings', label: 'Bookings', icon: CalendarDays },
  { to: '/calendar', label: 'Availability', icon: CalendarRange },
  { to: '/rooms', label: 'Rooms', icon: BedDouble },
  { to: '/conversations', label: 'Conversations', icon: MessageSquare },
  { to: '/guests', label: 'Guests', icon: Users },
  { to: '/calls', label: 'Calls & Messages', icon: PhoneCall, live: true },
  { to: '/reminders', label: 'Reminders', icon: Bell },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const live = useLiveCalls()
  const liveCount = live.data?.total ?? 0
  return (
    <nav className="flex flex-col gap-1 px-3">
      {NAV.map(({ to, label, icon: Icon, end, live: isLive }) => (
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
    </nav>
  )
}

function Brand() {
  const system = useSystem()
  const name = system.data?.hotel.name
  return (
    <div className="flex items-center gap-3 px-5 py-5">
      <div className="flex size-9 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
        <ConciergeBell className="size-5" />
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-sidebar-foreground">{name ?? 'Front Desk'}</div>
        <div className="text-xs text-sidebar-foreground/60">AI Receptionist</div>
      </div>
    </div>
  )
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const dark = theme === 'dark'
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Toggle theme"
      onClick={() => setTheme(dark ? 'light' : 'dark')}
    >
      {dark ? <Sun /> : <Moon />}
    </Button>
  )
}

export function AppLayout() {
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
              <div className="truncate font-medium text-sidebar-foreground">{user}</div>
              <div className="text-xs text-sidebar-foreground/60">Administrator</div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Log out"
              className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={logout}
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
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={logout} aria-label="Log out">
            <LogOut />
          </Button>
        </header>
        <main className="flex-1 px-4 py-6 lg:px-8">
          <Outlet />
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
