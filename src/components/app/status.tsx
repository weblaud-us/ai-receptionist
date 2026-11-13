import { Badge } from '@/components/ui/badge'
import { titleCase } from '@/lib/format'
import { cn } from '@/lib/utils'

const BOOKING: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300',
  confirmed: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
  checked_in: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  checked_out: 'bg-slate-200 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300',
  cancelled: 'bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300',
  no_show: 'bg-orange-100 text-orange-800 dark:bg-orange-500/20 dark:text-orange-300',
}

const PAYMENT: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300',
  partial: 'bg-violet-100 text-violet-800 dark:bg-violet-500/20 dark:text-violet-300',
  paid: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
}

const GENERIC: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  completed: 'bg-slate-200 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300',
  abandoned: 'bg-orange-100 text-orange-800 dark:bg-orange-500/20 dark:text-orange-300',
  sent: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  pending: 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300',
  failed: 'bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300',
  standard: 'bg-slate-100 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300',
  silver: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-500/20 dark:text-zinc-300',
  gold: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-500/20 dark:text-yellow-300',
  platinum: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-500/20 dark:text-indigo-300',
  voice: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-500/20 dark:text-indigo-300',
  whatsapp: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
}

function Pill({ value, map, className }: { value: string; map: Record<string, string>; className?: string }) {
  return (
    <Badge variant="secondary" className={cn('border-0', map[value] ?? '', className)}>
      {titleCase(value)}
    </Badge>
  )
}

export const BookingStatusBadge = ({ status, className }: { status: string; className?: string }) => (
  <Pill value={status} map={BOOKING} className={className} />
)
export const PaymentBadge = ({ status, className }: { status: string; className?: string }) => (
  <Pill value={status} map={PAYMENT} className={className} />
)
export const TagBadge = ({ value, className }: { value: string; className?: string }) => (
  <Pill value={value} map={GENERIC} className={className} />
)
