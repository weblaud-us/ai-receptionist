import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Bot, MessageSquare, Phone, RefreshCw, Search, Wrench } from 'lucide-react'
import { EmptyState, ErrorBanner, PageHeader, Pagination, TableSkeleton } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { TagBadge } from '@/components/app/status'
import { BookingSheet } from '@/components/bookings/BookingSheet'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { dateTime, duration, timeAgo, titleCase } from '@/lib/format'
import { useConversation, useConversations } from '@/lib/hooks'
import type { ConversationDetail, ConversationMessage, ToolCall } from '@/lib/types'
import { useDebounced } from '@/lib/use-debounced'
import { useMediaQuery } from '@/lib/use-media-query'
import { cn } from '@/lib/utils'

const ALL = 'all'

export default function Conversations() {
  const [sp, setSp] = useSearchParams()
  const selected = sp.get('session')
  const select = (id: string | null) => {
    const next = new URLSearchParams(sp)
    if (id) next.set('session', id)
    else next.delete('session')
    setSp(next, { replace: true })
  }

  const [q, setQ] = useState('')
  const [channel, setChannel] = useState(ALL)
  const [status, setStatus] = useState(ALL)
  const [booked, setBooked] = useState(ALL)
  const [page, setPage] = useState(1)
  const [booking, setBooking] = useState<string | null>(null)
  const wide = useMediaQuery('(min-width: 1024px)')

  const debouncedQ = useDebounced(q)
  const list = useConversations({
    q: debouncedQ.trim() || undefined,
    channel: channel === ALL ? undefined : channel,
    status: status === ALL ? undefined : status,
    has_booking: booked === ALL ? undefined : booked === 'yes',
    page,
    limit: 20,
  })
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPage(1)
  }

  return (
    <>
      <PageHeader
        title="Conversations"
        description="Every call and WhatsApp chat the AI receptionist has handled, with the tools it used."
        actions={
          <Button variant="outline" size="icon" onClick={() => void list.refetch()} aria-label="Refresh">
            <RefreshCw className={list.isFetching ? 'animate-spin' : ''} />
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[24rem_1fr]">
        <Card className="gap-0 py-0 lg:max-h-[calc(100vh-11rem)] lg:overflow-hidden">
          <div className="space-y-2 border-b p-3">
            <div className="relative">
              <Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted-foreground" />
              <Input className="pl-8" placeholder="Phone or guest name…" value={q} onChange={(e) => reset(setQ)(e.target.value)} />
            </div>
            <div className="flex flex-wrap gap-2">
              <SimpleSelect
                size="sm"
                value={channel}
                onChange={reset(setChannel)}
                options={[
                  { value: ALL, label: 'All channels' },
                  { value: 'voice', label: 'Voice calls' },
                  { value: 'whatsapp', label: 'WhatsApp' },
                ]}
              />
              <SimpleSelect
                size="sm"
                value={status}
                onChange={reset(setStatus)}
                options={[
                  { value: ALL, label: 'Any status' },
                  { value: 'active', label: 'Active' },
                  { value: 'completed', label: 'Completed' },
                  { value: 'abandoned', label: 'Abandoned' },
                ]}
              />
              <SimpleSelect
                size="sm"
                value={booked}
                onChange={reset(setBooked)}
                options={[
                  { value: ALL, label: 'Any outcome' },
                  { value: 'yes', label: 'Made a booking' },
                  { value: 'no', label: 'No booking' },
                ]}
              />
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {list.error && (
              <div className="p-3">
                <ErrorBanner error={list.error} onRetry={() => void list.refetch()} />
              </div>
            )}
            {list.isLoading ? (
              <TableSkeleton rows={8} cols={1} />
            ) : list.data && list.data.conversations.length === 0 ? (
              <EmptyState title="No conversations" description="Calls and chats will show up here." icon={<MessageSquare className="size-8" />} />
            ) : (
              <ul className="divide-y">
                {list.data?.conversations.map((c) => (
                  <li key={c.session_id}>
                    <button
                      type="button"
                      onClick={() => select(c.session_id)}
                      className={cn('w-full px-4 py-3 text-left transition-colors hover:bg-muted/60', selected === c.session_id && 'bg-primary/5')}
                    >
                      <div className="flex items-center gap-2">
                        {c.channel === 'voice' ? <Phone className="size-3.5 text-indigo-500" /> : <MessageSquare className="size-3.5 text-emerald-500" />}
                        <span className="truncate text-sm font-medium">{c.guest_name || c.phone}</span>
                        <span className="ml-auto shrink-0 text-xs text-muted-foreground">{timeAgo(c.updated_at)}</span>
                      </div>
                      <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{c.last_message?.content || 'No messages'}</div>
                      <div className="mt-1.5 flex items-center gap-1.5">
                        <TagBadge value={c.status} />
                        {c.booking_ids.length > 0 && (
                          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                            {c.booking_ids.length} booking{c.booking_ids.length === 1 ? '' : 's'}
                          </span>
                        )}
                        <span className="ml-auto text-xs text-muted-foreground">{c.message_count} msgs</span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {list.data && <Pagination page={list.data.page} pages={list.data.pages} total={list.data.total} onPage={setPage} label="conversations" />}
        </Card>

        {wide ? (
          <Card className="gap-0 py-0 lg:max-h-[calc(100vh-11rem)] lg:overflow-hidden">
            {selected ? (
              <Transcript key={selected} sessionId={selected} onOpenBooking={setBooking} />
            ) : (
              <EmptyState title="Select a conversation" description="Pick one on the left to read the full transcript." className="h-full" />
            )}
          </Card>
        ) : (
          <Sheet open={!!selected} onOpenChange={(o) => !o && select(null)}>
            <SheetContent className="w-full gap-0 sm:max-w-xl">
              <SheetTitle className="sr-only">Conversation</SheetTitle>
              {selected && <Transcript key={selected} sessionId={selected} onOpenBooking={setBooking} />}
            </SheetContent>
          </Sheet>
        )}
      </div>

      <BookingSheet bookingId={booking} onClose={() => setBooking(null)} />
    </>
  )
}

function Transcript({ sessionId, onOpenBooking }: { sessionId: string; onOpenBooking: (id: string) => void }) {
  const q = useConversation(sessionId)
  const bottom = useRef<HTMLDivElement>(null)
  const count = q.data?.messages.length ?? 0

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [count])

  if (q.isLoading)
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-16 w-3/4" />
        <Skeleton className="ml-auto h-16 w-3/4" />
      </div>
    )
  if (q.error || !q.data) return <div className="p-4"><ErrorBanner error={q.error ?? new Error('Not found')} onRetry={() => void q.refetch()} /></div>

  const c = q.data
  return (
    <div className="flex h-full min-h-0 flex-col">
      <Header c={c} onOpenBooking={onOpenBooking} />
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-muted/20 p-4">
        {c.messages.length === 0 && <EmptyState title="No messages recorded" className="py-8" />}
        {c.messages
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .map((m, i) => (
            <Bubble key={i} m={m} />
          ))}
        <div ref={bottom} />
      </div>
    </div>
  )
}

function Header({ c, onOpenBooking }: { c: ConversationDetail; onOpenBooking: (id: string) => void }) {
  const ctx = c.context as Record<string, unknown>
  return (
    <div className="space-y-2 border-b p-4 pr-12">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">{c.guest_name || (ctx.guest_name as string) || c.phone}</h2>
        <TagBadge value={c.channel} />
        <TagBadge value={c.status} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <Link className="text-primary hover:underline" to={`/guests?phone=${encodeURIComponent(c.phone)}`}>
          {c.phone}
        </Link>
        <span>Started {dateTime(c.created_at)}</span>
        {c.duration_sec != null && <span>Duration {duration(c.duration_sec)}</span>}
        {typeof ctx.conversation_stage === 'string' && <span>Stage: {titleCase(ctx.conversation_stage)}</span>}
        {ctx.guest_gender ? <span>Addressed as {ctx.guest_gender === 'female' ? "ma'am" : 'sir'}</span> : null}
      </div>
      {c.bookings.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Bookings:</span>
          {c.bookings.map((b) => (
            <Button key={b.booking_id} size="xs" variant="outline" className="font-mono" onClick={() => onOpenBooking(b.booking_id)}>
              {b.booking_id}
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}

function Bubble({ m }: { m: ConversationMessage }) {
  const isGuest = m.role === 'user'
  return (
    <div className={cn('flex flex-col gap-1', isGuest ? 'items-start' : 'items-end')}>
      <div
        className={cn(
          'max-w-[85%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap shadow-sm',
          isGuest ? 'rounded-bl-sm bg-card' : 'rounded-br-sm bg-primary text-primary-foreground',
        )}
      >
        {m.content}
      </div>
      {m.tool_calls?.map((t, i) => <ToolChip key={i} t={t} />)}
      <div className="flex items-center gap-1 px-1 text-[11px] text-muted-foreground">
        {!isGuest && <Bot className="size-3" />}
        {isGuest ? 'Guest' : 'Receptionist'} · {dateTime(m.timestamp, 'd MMM, HH:mm')}
      </div>
    </div>
  )
}

function pretty(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}

function ToolChip({ t }: { t: ToolCall }) {
  return (
    <details className="group max-w-[85%] rounded-lg border bg-card text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-muted-foreground select-none">
        <Wrench className="size-3" />
        <span className="font-mono">{t.name}</span>
        <span className="text-[10px] group-open:hidden">· show details</span>
      </summary>
      <div className="space-y-2 border-t p-2.5">
        <div>
          <div className="mb-0.5 font-medium">Arguments</div>
          <pre className="max-h-40 overflow-auto rounded bg-muted p-2 font-mono text-[11px]">{JSON.stringify(t.args, null, 2)}</pre>
        </div>
        <div>
          <div className="mb-0.5 font-medium">Result</div>
          <pre className="max-h-56 overflow-auto rounded bg-muted p-2 font-mono text-[11px]">{pretty(t.result)}</pre>
        </div>
      </div>
    </details>
  )
}
