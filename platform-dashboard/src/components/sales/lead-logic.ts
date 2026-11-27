/** How lead statuses read, and the stages a person may set by hand. */
import { titleCase } from '@receptionist/shared/lib/format'
import type { Stage } from '@/lib/sales'

export const STATUS_STYLE: Record<string, string> = {
  queued: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
  in_progress: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  callback: 'bg-violet-100 text-violet-800 dark:bg-violet-500/20 dark:text-violet-300',
  interested: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300',
  demo_booked: 'bg-emerald-200 text-emerald-900 dark:bg-emerald-500/30 dark:text-emerald-200',
  dnc: 'bg-rose-100 text-rose-800 dark:bg-rose-500/20 dark:text-rose-300',
  blocked: 'bg-orange-100 text-orange-800 dark:bg-orange-500/20 dark:text-orange-300',
  bad_number: 'bg-orange-100 text-orange-800 dark:bg-orange-500/20 dark:text-orange-300',
}
const STATUS_LABEL: Record<string, string> = { dnc: 'Do not call', blocked: 'Cannot be called', new: 'Not in a campaign' }
export const statusLabel = (s: string) => STATUS_LABEL[s] ?? titleCase(s)

export const STAGE_MANUAL: Stage[] = ['interested', 'meeting_booked', 'proposal', 'won', 'lost']
