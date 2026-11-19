import { useEffect, useState } from 'react'
import { CheckCircle2, Clock, Loader2, MailWarning, Phone, Search, XCircle } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth } from '@receptionist/shared/auth/auth'
import { Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Input } from '@receptionist/shared/components/ui/input'
import { useNumberOrders, useNumberSearch, useOrderNumber, useResendVerification, type NumberOrder } from '@/lib/billing'
import { cn } from '@receptionist/shared/lib/utils'

const COUNTRIES = [
  { value: 'US', label: 'United States' },
  { value: 'CA', label: 'Canada' },
  { value: 'GB', label: 'United Kingdom' },
  { value: 'AE', label: 'United Arab Emirates' },
]

const STEP_LABEL: Record<string, string> = {
  purchased: 'Reserving the number',
  trunk: 'Connecting it to the phone network',
  livekit: 'Connecting it to your assistant',
  mapped: 'Routing calls to your business',
  verified: 'Placing a test call',
}

function OrderProgress({ order }: { order: NumberOrder }) {
  const open = ['pending', 'running'].includes(order.status)
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-lg">{order.e164}</span>
        {order.status === 'active' && <span className="flex items-center gap-1 text-sm text-emerald-600"><CheckCircle2 className="size-4" /> Live</span>}
        {open && <span className="flex items-center gap-1 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Setting up…</span>}
        {order.status === 'needs_documents' && <span className="flex items-center gap-1 text-sm text-amber-600"><Clock className="size-4" /> Pending approval</span>}
        {['failed', 'verify_failed'].includes(order.status) && <span className="flex items-center gap-1 text-sm text-destructive"><XCircle className="size-4" /> Needs attention</span>}
      </div>
      {open && (
        <ol className="space-y-1 text-sm">
          {Object.entries(STEP_LABEL).map(([key, label]) => (
            <li key={key} className={cn('flex items-center gap-2', order.steps[key] ? 'text-foreground' : 'text-muted-foreground')}>
              {order.steps[key] ? <CheckCircle2 className="size-3.5 text-emerald-600" /> : <span className="size-3.5 rounded-full border" />}
              {label}
            </li>
          ))}
        </ol>
      )}
      {order.note && <p className="text-sm text-muted-foreground">{order.note}</p>}
      {['failed', 'verify_failed'].includes(order.status) && (
        <p className="text-sm text-muted-foreground">
          We're retrying automatically and have been notified. {order.error && <span className="block text-xs">({order.error})</span>}
        </p>
      )}
    </div>
  )
}

/** Search → pick → buy, then live progress of the order. */
export function NumberPicker({ onLive }: { onLive?: () => void }) {
  const { user } = useAuth()
  const [country, setCountry] = useState('US')
  const [areaCode, setAreaCode] = useState('')
  const [query, setQuery] = useState<{ country: string; area_code: string } | null>(null)
  const orders = useNumberOrders(true)
  const search = useNumberSearch(query ?? { country, area_code: '' }, !!query)
  const order = useOrderNumber()
  const resend = useResendVerification()
  const list = orders.data?.orders ?? []
  const current = list.find((o) => o.status !== 'cancelled')
  const live = current?.status === 'active'
  useEffect(() => {
    if (live && onLive) onLive()
  }, [live, onLive])

  if (current) return <OrderProgress order={current} />
  if (orders.data?.mode === 'off') {
    return (
      <p className="text-sm text-muted-foreground">
        New numbers aren't available here yet. Keep your current number and forward it to your assistant — we'll send the
        forwarding steps once your number is assigned.
      </p>
    )
  }
  if (user && user.email_verified === false) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
        <MailWarning className="mt-0.5 size-4 shrink-0 text-amber-600" />
        <div className="flex-1 space-y-2">
          <p>Confirm your e-mail address before we buy a number for you — check your inbox for our link.</p>
          <Button size="xs" variant="outline" disabled={resend.isPending}
            onClick={() => resend.mutate(undefined, { onSuccess: (r) => toast.success(r.already_verified ? 'Already confirmed — reload the page' : 'Sent — check your inbox') })}>
            Send the link again
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <form className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => { e.preventDefault(); setQuery({ country, area_code: areaCode.trim() }) }}>
        <Field label="Country"><SimpleSelect value={country} onChange={setCountry} options={COUNTRIES} className="w-48" /></Field>
        <Field label="Area code (optional)">
          <Input className="w-32" inputMode="numeric" maxLength={5} value={areaCode} placeholder="512"
            onChange={(e) => setAreaCode(e.target.value.replace(/\D/g, ''))} />
        </Field>
        <Button type="submit" variant="outline" disabled={search.isFetching}>
          {search.isFetching ? <Loader2 className="animate-spin" /> : <Search />} Find numbers
        </Button>
      </form>
      {search.error && <p className="text-sm text-destructive">{(search.error as Error).message}</p>}
      {search.data && !search.data.instant && (
        <p className="text-sm text-muted-foreground">
          Numbers in this country need the carrier to approve your business documents first (usually 1–3 business days).
          You can test from the browser meanwhile.
        </p>
      )}
      {search.data && search.data.numbers.length === 0 && <p className="text-sm text-muted-foreground">Nothing free in that area code — try a nearby one.</p>}
      <div className="grid gap-2 sm:grid-cols-2">
        {search.data?.numbers.map((n) => (
          <button key={n.e164} type="button" disabled={order.isPending}
            onClick={() => order.mutate(n.e164, { onSuccess: () => toast.success(`Setting up ${n.e164}…`) })}
            className="flex items-center justify-between rounded-lg border p-3 text-left transition-colors hover:bg-muted disabled:opacity-50">
            <span>
              <span className="block font-mono">{n.e164}</span>
              <span className="text-xs text-muted-foreground">{[n.locality, n.region].filter(Boolean).join(', ')}{n.fake && ' · development'}</span>
            </span>
            <Phone className="size-4 text-muted-foreground" />
          </button>
        ))}
      </div>
    </div>
  )
}
