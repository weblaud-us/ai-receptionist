/** The signed-in user's menu at the foot of the sidebar (and in the mobile header):
 * account settings, theme and log out. Shared by the client dashboard and the platform admin app. */
import { ChevronsUpDown, LogOut, Moon, Settings, Sun } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/auth'
import { cn } from '../lib/utils'
import { useTheme } from '../providers/theme'
import { Avatar, AvatarFallback } from './ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu'

function initials(name: string, email: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length >= 2) return (words[0][0] + words[words.length - 1][0]).toUpperCase()
  return (words[0] ?? email).slice(0, 2).toUpperCase()
}

export function AccountMenu({ roleLabel, settingsPath = '/account', compact = false }: {
  /** e.g. "Platform admin", "Owner". */
  roleLabel: string
  settingsPath?: string
  /** Avatar only (mobile header). */
  compact?: boolean
}) {
  const { user, logout } = useAuth()
  const { theme, setTheme } = useTheme()
  const navigate = useNavigate()
  const name = user?.name || user?.email || ''
  const avatar = (
    <Avatar size="sm">
      <AvatarFallback className="bg-sidebar-accent text-[0.65rem] font-semibold text-sidebar-accent-foreground">
        {initials(user?.name ?? '', user?.email ?? '?')}
      </AvatarFallback>
    </Avatar>
  )

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Account menu"
        className={cn(
          'flex items-center gap-2.5 rounded-lg text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
          compact
            ? 'p-1 hover:bg-muted'
            : 'w-full px-2 py-1.5 text-sidebar-foreground hover:bg-sidebar-accent/60 data-[popup-open]:bg-sidebar-accent/60',
        )}
      >
        {avatar}
        {!compact && (
          <>
            <div className="min-w-0 flex-1 text-sm">
              <div className="truncate font-medium">{name}</div>
              <div className="truncate text-xs text-sidebar-foreground/60">{roleLabel}</div>
            </div>
            <ChevronsUpDown className="size-4 shrink-0 text-sidebar-foreground/60" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side={compact ? 'bottom' : 'top'} align={compact ? 'end' : 'start'} className="w-60">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="font-normal">
            <div className="truncate text-sm font-medium text-foreground">{name}</div>
            {user?.name && <div className="truncate text-xs text-muted-foreground">{user.email}</div>}
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => navigate(settingsPath)}>
          <Settings /> Account settings
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
          {theme === 'dark' ? <Sun /> : <Moon />} {theme === 'dark' ? 'Light mode' : 'Dark mode'}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={() => void logout()}>
          <LogOut /> Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
