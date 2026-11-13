import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Check, Loader2, MessageCircle, Search, Send } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, KeyValue, PageHeader, Pagination, TableSkeleton } from '@/components/app/blocks'
import { SimpleSelect } from '@/components/app/simple-select'
import { BookingStatusBadge, TagBadge } from '@/components/app/status'
import { BookingSheet } from '@/components/bookings/BookingSheet'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { dateOnly, dateTime, money, timeAgo, titleCase } from '@/lib/format'
import { useGuest, useGuests, useSendWhatsApp, useUpdateGuest } from '@/lib/hooks'
import { VIP_TIERS, type GuestDetail } from '@/lib/types'
import { useDebounced } from '@/lib/use-debounced'

const ALL = 'all'

export default function Guests() {
  const [sp, setSp] = useSearchParams()
  const phone = sp.get('phone')
  const open = (p: string | null) => {
    const next = new URLSearchParams(sp)
    if (p) next.set('phone', p)
    else next.delete('phone')
    setSp(next, { replace: true })
  }

  const [q, setQ] = useState('')
  const [tier, setTier] = useState(ALL)
  const [page, setPage] = useState(1)
  const [booking, setBooking] = useState<string | null>(null)

  const debouncedQ = useDebounced(q)
  const list = useGuests({ q: debouncedQ.trim() || undefined, vip_tier: tier === ALL ? undefined : tier, page, limit: 20 })

  return (
    <>
      <PageHeader title="Guests" description="Everyone who has called or messaged the hotel, with their booking history." />

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <div className="relative min-w-56 flex-1">
            <Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted-foreground" />
            <Input
              className="pl-8"
              placeholder="Search name, phone, email…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setPage(1)
              }}
            />
          </div>
          <SimpleSelect
            value={tier}
            onChange={(v) => {
              setTier(v)
              setPage(1)
            }}
            options={[{ value: ALL, label: 'All tiers' }, ...VIP_TIERS.map((t) => ({ value: t, label: titleCase(t) }))]}
          />
        </div>

        {list.error && (
          <div className="p-4">
            <ErrorBanner error={list.error} onRetry={() => void list.refetch()} />
          </div>
        )}
        {list.isLoading ? (
          <TableSkeleton cols={6} />
        ) : list.data && list.data.guests.length === 0 ? (
          <EmptyState title="No guests found" description="Guests are added automatically the first time someone contacts the hotel." />
        ) : (
          list.data && (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Guest</TableHead>
                    <TableHead>Email</TableHead>
                    <TableHead>Tier</TableHead>
                    <TableHead className="text-right">Bookings</TableHead>
                    <TableHead className="text-right">Spent</TableHead>
                    <TableHead>Last contact</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.data.guests.map((g) => (
                    <TableRow key={g.phone} className="cursor-pointer" onClick={() => open(g.phone)}>
                      <TableCell>
                        <div className="font-medium">{g.name || <span className="text-muted-foreground">Unnamed</span>}</div>
                        <div className="text-xs text-muted-foreground">{g.phone}</div>
                      </TableCell>
                      <TableCell className="text-sm">{g.email || '—'}</TableCell>
                      <TableCell>
                        <TagBadge value={g.vip_tier} />
                      </TableCell>
                      <TableCell className="text-right">{g.booking_count}</TableCell>
                      <TableCell className="text-right">{g.total_spent ? money(g.total_spent) : '—'}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{timeAgo(g.last_interaction)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pagination page={list.data.page} pages={list.data.pages} total={list.data.total} onPage={setPage} label="guests" />
            </>
          )
        )}
      </Card>

      <Sheet open={!!phone} onOpenChange={(o) => !o && open(null)}>
        <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
          {phone && <GuestBody key={phone} phone={phone} onOpenBooking={setBooking} />}
        </SheetContent>
      </Sheet>
      <BookingSheet bookingId={booking} onClose={() => setBooking(null)} />
    </>
  )
}

function GuestBody({ phone, onOpenBooking }: { phone: string; onOpenBooking: (id: string) => void }) {
  const q = useGuest(phone)
  if (q.isLoading)
    return (
      <div className="space-y-3 p-6">
        <SheetTitle className="sr-only">Guest</SheetTitle>
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-48" />
      </div>
    )
  if (q.error || !q.data)
    return (
      <div className="p-6">
        <SheetTitle className="sr-only">Guest</SheetTitle>
        <ErrorBanner error={q.error ?? new Error('Guest not found')} onRetry={() => void q.refetch()} />
      </div>
    )
  return <GuestDetails g={q.data} onOpenBooking={onOpenBooking} />
}

function GuestDetails({ g, onOpenBooking }: { g: GuestDetail; onOpenBooking: (id: string) => void }) {
  const update = useUpdateGuest()
  const send = useSendWhatsApp()
  const [name, setName] = useState(g.name ?? '')
  const [email, setEmail] = useState(g.email ?? '')
  const [nationality, setNationality] = useState(g.nationality ?? '')
  const [tier, setTier] = useState<string>(g.vip_tier)
  const [notes, setNotes] = useState(g.notes ?? '')
  const [message, setMessage] = useState('')

  const dirty =
    name !== (g.name ?? '') || email !== (g.email ?? '') || nationality !== (g.nationality ?? '') || tier !== g.vip_tier || notes !== (g.notes ?? '')
  const spent = g.bookings.filter((b) => ['confirmed', 'checked_in', 'checked_out'].includes(b.status)).reduce((s, b) => s + b.pricing.final_total, 0)

  return (
    <>
      <SheetHeader className="border-b">
        <div className="flex items-center gap-2 pr-8">
          <SheetTitle>{g.name || 'Unnamed guest'}</SheetTitle>
          <TagBadge value={g.vip_tier} />
        </div>
        <SheetDescription>{g.phone}</SheetDescription>
      </SheetHeader>

      <div className="space-y-5 p-4">
        <section className="grid grid-cols-3 gap-2 text-center">
          {[
            ['Bookings', g.bookings.length],
            ['Total spent', spent ? money(spent) : '—'],
            ['Last contact', timeAgo(g.last_interaction)],
          ].map(([label, value]) => (
            <div key={label as string} className="rounded-lg border p-2">
              <div className="text-xs text-muted-foreground">{label}</div>
              <div className="text-sm font-semibold">{value}</div>
            </div>
          ))}
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Profile</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name">
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Email">
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Nationality">
              <Input value={nationality} onChange={(e) => setNationality(e.target.value)} />
            </Field>
            <Field label="VIP tier">
              <SimpleSelect className="w-full" value={tier} onChange={setTier} options={VIP_TIERS.map((t) => ({ value: t, label: titleCase(t) }))} />
            </Field>
          </div>
          <Field label="Internal notes">
            <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Preferences, allergies, VIP details…" />
          </Field>
          <Button
            size="sm"
            disabled={!dirty || update.isPending}
            onClick={() =>
              update.mutate(
                { phone: g.phone, name: name.trim() || null, email: email.trim() || null, nationality: nationality.trim() || null, vip_tier: tier, notes: notes.trim() || null },
                { onSuccess: () => toast.success('Guest saved') },
              )
            }
          >
            {update.isPending ? <Loader2 className="animate-spin" /> : <Check />} Save profile
          </Button>
        </section>

        <Separator />

        <section>
          <h3 className="mb-2 text-sm font-semibold">Bookings ({g.bookings.length})</h3>
          {g.bookings.length === 0 ? (
            <p className="text-sm text-muted-foreground">No bookings under this phone number.</p>
          ) : (
            <ul className="divide-y rounded-lg border">
              {g.bookings.map((b) => (
                <li key={b.booking_id}>
                  <button type="button" className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted/60" onClick={() => onOpenBooking(b.booking_id)}>
                    <div className="min-w-0 flex-1">
                      <div className="font-mono text-xs font-medium">{b.booking_id}</div>
                      <div className="text-xs text-muted-foreground">
                        {dateOnly(b.check_in_date, 'd MMM')} → {dateOnly(b.check_out_date, 'd MMM yyyy')} · Room {b.room_number}
                      </div>
                    </div>
                    <span className="text-sm">{money(b.pricing.final_total, b.pricing.currency)}</span>
                    <BookingStatusBadge status={b.status} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <h3 className="mb-2 text-sm font-semibold">Conversations ({g.conversations.length})</h3>
          {g.conversations.length === 0 ? (
            <p className="text-sm text-muted-foreground">None recorded.</p>
          ) : (
            <ul className="divide-y rounded-lg border">
              {g.conversations.map((c) => (
                <li key={c.session_id}>
                  <Link to={`/conversations?session=${c.session_id}`} className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-muted/60">
                    <TagBadge value={c.channel} />
                    <span className="flex-1 text-muted-foreground">{dateTime(c.created_at)}</span>
                    <span className="text-xs text-muted-foreground">{c.message_count} msgs</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <Separator />

        <section className="space-y-2">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold">
            <MessageCircle className="size-4" /> Send a WhatsApp message
          </h3>
          <Textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} placeholder={`Message to ${g.name || g.phone}…`} />
          <p className="text-xs text-muted-foreground">WhatsApp only delivers free-form text within 24 hours of the guest's last message.</p>
          <Button
            size="sm"
            disabled={!message.trim() || send.isPending}
            onClick={() =>
              send.mutate(
                { to: g.phone, message: message.trim() },
                {
                  onSuccess: () => {
                    toast.success('Message sent')
                    setMessage('')
                  },
                },
              )
            }
          >
            {send.isPending ? <Loader2 className="animate-spin" /> : <Send />} Send
          </Button>
        </section>

        <KeyValue label="Guest since">{dateTime(g.created_at, 'd MMM yyyy')}</KeyValue>
      </div>
    </>
  )
}
