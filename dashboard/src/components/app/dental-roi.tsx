import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CalendarHeart, Coins, MessageSquareReply, PhoneForwarded, Repeat, Siren } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { cn } from '@receptionist/shared/lib/utils'
import type { DentalInsights } from '@/lib/compliance'
import { useInsights } from '@/lib/compliance'
import { useCtx } from '@/lib/tenant'
import { formatMinor } from '@/lib/tenant-format'

function Tile({ label, value, sub, icon, highlight }: { label: string; value: ReactNode; sub?: ReactNode; icon: ReactNode; highlight?: boolean }) {
  return (
    <Card className={cn('h-full', highlight && 'border-primary/40 bg-primary/5')}>
      <CardContent className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm text-muted-foreground">{label}</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
          {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
        </div>
        <div className="text-muted-foreground">{icon}</div>
      </CardContent>
    </Card>
  )
}

/** The numbers a dental practice pays the retainer for: calls reception couldn't take, consults, pipeline. */
export function DentalRoi({ dental, locale }: { dental: DentalInsights; locale: string }) {
  const w = dental.by_window
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <Tile highlight label="Estimated treatment pipeline" icon={<Coins className="size-4" />}
        value={formatMinor(dental.estimated_pipeline_value_minor, dental.currency, locale)}
        sub={<Link className="underline" to="/services">Set treatment values</Link>} />
      <Tile label="Calls reception couldn’t take" value={dental.overflow_calls_answered} icon={<PhoneForwarded className="size-4" />}
        sub={`${w.lunch_break} lunch · ${w.after_hours} after hours · ${w.busy_overflow} busy`} />
      <Tile label="Consultations booked" value={dental.consultations_booked} icon={<CalendarHeart className="size-4" />}
        sub={`${dental.cosmetic_leads} more treatment enquiries qualified`} />
      <Tile label="Emergencies triaged" value={dental.emergency_triages_captured} icon={<Siren className="size-4" />}
        sub={<Link className="underline" to="/leads">See captured leads</Link>} />
      <Tile label="Missed callers texted" value={dental.missed_call_sms_recoveries} icon={<MessageSquareReply className="size-4" />}
        sub="With your booking link" />
      <Tile label="Bookings from recall texts" value={dental.reactivation_bookings_attributed ?? 0} icon={<Repeat className="size-4" />}
        sub={`${dental.recall_texts_sent ?? 0} overdue patients texted`} />
    </div>
  )
}

/** Overview card: the last 30 days of dental ROI. Rendered only for dental practices. */
export function DentalRoiCard() {
  const ctx = useCtx()
  const q = useInsights(30)
  const dental = q.data?.dental
  if (!dental) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your practice, last 30 days</CardTitle>
        <CardDescription>Calls your team couldn’t take and the treatment value your assistant captured.</CardDescription>
      </CardHeader>
      <CardContent>
        <DentalRoi dental={dental} locale={ctx.tenant.locale} />
      </CardContent>
    </Card>
  )
}
