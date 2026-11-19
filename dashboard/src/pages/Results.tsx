import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import { BookOpen, CalendarCheck, Clock, Inbox, Loader2, PhoneCall, PhoneMissed, ShieldCheck, Star, Timer, UserRound } from 'lucide-react'
import { ErrorBanner, PageHeader } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { money } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useFeedback, useInsights, type Insights } from '@/lib/compliance'

const PERIODS = [
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
]

const MODE_HINT: Record<Insights['answering_mode'], string> = {
  all_calls: 'Calls that came in while you were closed.',
  overflow: 'You forward calls you can’t take, so every call it answered.',
  after_hours: 'You forward calls after hours, so every call it answered.',
}

function Stat({ label, value, sub, icon, highlight }: { label: string; value: ReactNode; sub?: ReactNode; icon: ReactNode; highlight?: boolean }) {
  return (
    <Card className={cn('h-full', highlight && 'border-primary/40 bg-primary/5')}>
      <CardContent className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm text-muted-foreground">{label}</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
          {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
        </div>
        <div className="rounded-lg bg-muted p-2 text-muted-foreground">{icon}</div>
      </CardContent>
    </Card>
  )
}

function FeedbackCard({ current }: { current: Insights['satisfaction'] }) {
  const send = useFeedback()
  const [score, setScore] = useState<number | null>(null)
  const [comment, setComment] = useState('')
  const [testimonial, setTestimonial] = useState(false)
  const submit = () => {
    if (score === null) return
    send.mutate(
      { score, comment, testimonial_ok: testimonial },
      {
        onSuccess: () => {
          toast.success('Thanks — that helps us improve your assistant')
          setScore(null)
          setComment('')
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : 'Could not send'),
      },
    )
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Star className="size-4" /> How is your assistant doing?</CardTitle>
        <CardDescription>
          {current ? `Your last score: ${current.score}/10.` : 'Tell us once a week — we read every answer.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Score from 1 to 10">
          {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
            <Button
              key={n}
              type="button"
              size="sm"
              role="radio"
              aria-checked={score === n}
              variant={score === n ? 'default' : 'outline'}
              className="w-9 tabular-nums"
              onClick={() => setScore(n)}
            >
              {n}
            </Button>
          ))}
        </div>
        <Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="What went well, or what should it do better?" rows={3} maxLength={2000} />
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={testimonial} onCheckedChange={(v) => setTestimonial(v === true)} />
          You may quote this on your website
        </label>
        <Button onClick={submit} disabled={score === null || send.isPending}>
          {send.isPending && <Loader2 className="animate-spin" />}
          Send
        </Button>
      </CardContent>
    </Card>
  )
}

/** Phase 7: what the assistant did for the business — the pilot scorecard and the monthly retention story. */
export default function Results() {
  const [days, setDays] = useState('30')
  const q = useInsights(Number(days))
  const d = q.data
  return (
    <>
      <PageHeader
        title="Results"
        description="What your assistant did for you — calls it caught, bookings it made, and time it saved."
        actions={<SimpleSelect value={days} onChange={setDays} options={PERIODS} className="w-40" />}
      />
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {!d && !q.error && <Skeleton className="h-96 w-full" />}
      {d && (
        <div className="space-y-4">
          {d.pilot && (
            <div className="flex items-center gap-2 rounded-lg border bg-muted/40 px-4 py-3 text-sm">
              <Badge>Pilot</Badge> You’re on the free pilot. We go through these numbers with you on the weekly call.
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat highlight label="Calls that would have been missed" value={d.would_have_missed} sub={MODE_HINT[d.answering_mode]} icon={<PhoneMissed className="size-4" />} />
            <Stat label="Bookings made" value={d.bookings} sub={d.est_booking_value ? `About ${money(d.est_booking_value, d.currency)} at list price` : 'By phone or WhatsApp'} icon={<CalendarCheck className="size-4" />} />
            <Stat label="Calls answered" value={d.calls_answered} sub={`${d.minutes} minutes on the phone`} icon={<PhoneCall className="size-4" />} />
            <Stat label="Handled without your team" value={d.handled_without_human_pct === null ? '—' : `${Math.round(d.handled_without_human_pct)}%`} sub={`${d.transfers} passed to your team`} icon={<UserRound className="size-4" />} />
            <Stat label="Messages & job requests" value={d.messages + d.leads} sub={<Link className="underline" to="/calls">See them in Calls & messages</Link>} icon={<Inbox className="size-4" />} />
            <Stat label="Typical response time" value={d.latency_p50_s === null ? '—' : `${d.latency_p50_s.toFixed(1)} s`} sub="From when the caller stops talking" icon={<Timer className="size-4" />} />
            <Stat label="Questions it couldn’t answer" value={d.unanswered_questions} sub={<Link className="underline" to="/knowledge">Add answers in Knowledge</Link>} icon={<BookOpen className="size-4" />} />
            <Stat label="Your score" value={d.satisfaction ? `${d.satisfaction.score}/10` : '—'} sub="Rate it below" icon={<Star className="size-4" />} />
          </div>
          <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Clock className="size-4" /> Calls answered per day</CardTitle>
              </CardHeader>
              <CardContent>
                {d.by_day.length === 0 ? (
                  <p className="py-12 text-center text-sm text-muted-foreground">No answered calls in this period yet.</p>
                ) : (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={d.by_day} margin={{ left: -20, right: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                        <XAxis dataKey="day" tickFormatter={(v: string) => v.slice(5)} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                        <YAxis allowDecimals={false} tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                        <ChartTooltip contentStyle={{ background: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8 }} />
                        <Bar dataKey="calls" fill="var(--chart-1)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
            <FeedbackCard current={d.satisfaction} />
          </div>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheck className="size-3.5" /> Booking value is an estimate from your service prices. How you forward calls is set in Settings.
          </p>
        </div>
      )}
    </>
  )
}
