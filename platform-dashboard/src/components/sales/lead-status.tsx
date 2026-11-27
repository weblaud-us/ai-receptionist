/** How a lead's status looks, everywhere it is shown. */
import { Badge } from '@receptionist/shared/components/ui/badge'
import { cn } from '@receptionist/shared/lib/utils'
import { STATUS_STYLE, statusLabel } from '@/components/sales/lead-logic'

export function LeadStatus({ status }: { status: string }) {
  return <Badge variant="secondary" className={cn('border-0', STATUS_STYLE[status])}>{statusLabel(status)}</Badge>
}
