import { useState, type ReactNode } from 'react'
import { AlertTriangle, Check, Circle, Loader2, Search, XCircle } from 'lucide-react'
import { EmptyState, Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Input } from '@receptionist/shared/components/ui/input'
import { Skeleton } from '@receptionist/shared/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { COUNTRIES, titleCase } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useAvailableNumbers, useOwnedNumbers, type NumberChoice, type NumberOption, type NumberOrder } from '@/lib/platform'

/** What's missing for automatic numbers (from the API), so the admin knows exactly what to set. */
export function SetupProblems({ problems }: { problems: string[] }) {
  return (
    <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
      <div className="flex items-center gap-2 font-medium"><AlertTriangle className="size-4 text-amber-600" /> Automatic numbers aren't set up yet</div>
      <ul className="list-disc space-y-1 pl-6 text-muted-foreground">
        {problems.map((p) => <li key={p}>{p}</li>)}
      </ul>
      <p className="text-xs text-muted-foreground">
        Add these to the API's .env, then recreate the containers so they read it: <code>docker compose up -d api worker jobs</code>
        (a plain <code>restart</code> keeps the old values). Then reload this page.
      </p>
    </div>
  )
}

function NumberRow({ n, selected, onPick }: { n: NumberOption; selected: boolean; onPick: () => void }) {
  return (
    <button type="button" onClick={onPick}
      className={cn('flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm transition-colors hover:bg-muted',
        selected && 'bg-primary/5 ring-2 ring-inset ring-primary/40')}>
      <span>
        <span className="font-mono">{n.e164}</span>
        <span className="ml-2 text-xs text-muted-foreground">
          {[n.locality, n.region, n.friendly_name, n.country].filter(Boolean).join(' · ')}
        </span>
      </span>
      {selected && <Check className="size-4 text-primary" />}
    </button>
  )
}

function OwnedTab({ value, onChange }: { value: NumberChoice; onChange: (v: NumberChoice) => void }) {
  const q = useOwnedNumbers()
  if (q.isLoading) return <Skeleton className="h-24 w-full" />
  if (q.error) return <p className="text-sm text-destructive">{(q.error as Error).message}</p>
  if (q.data?.mode === 'off') return <SetupProblems problems={q.data.problems} />
  const list = q.data?.numbers ?? []
  if (list.length === 0) return <EmptyState className="py-6" title="No unused numbers" description="Every number in your Twilio account is already assigned. Buy a new one instead." />
  return (
    <div className="max-h-64 divide-y overflow-auto rounded-lg border">
      {list.map((n) => (
        <NumberRow key={n.e164} n={n} selected={value.source === 'owned' && value.e164 === n.e164}
          onPick={() => onChange({ source: 'owned', e164: n.e164 })} />
      ))}
    </div>
  )
}

function BuyTab({ value, onChange, defaultCountry }: { value: NumberChoice; onChange: (v: NumberChoice) => void; defaultCountry: string }) {
  const [country, setCountry] = useState(COUNTRIES.some(([c]) => c === defaultCountry) ? defaultCountry : 'AU')
  const [areaCode, setAreaCode] = useState('')
  const [contains, setContains] = useState('')
  const [search, setSearch] = useState<{ country: string; area_code: string; contains: string } | null>(null)
  const q = useAvailableNumbers(search)
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-[10rem_7rem_1fr_auto] sm:items-end">
        <Field label="Country">
          <SimpleSelect value={country} onChange={setCountry} options={COUNTRIES.map(([c, name]) => ({ value: c, label: name }))} />
        </Field>
        <Field label="Area code"><Input value={areaCode} inputMode="numeric" placeholder={country === 'GB' ? '20' : '2'} onChange={(e) => setAreaCode(e.target.value.replace(/\D/g, ''))} /></Field>
        <Field label="Contains (optional)"><Input value={contains} placeholder="e.g. 2000" onChange={(e) => setContains(e.target.value.replace(/[^\d*]/g, ''))} /></Field>
        <Button variant="outline" onClick={() => setSearch({ country, area_code: areaCode, contains })} disabled={q.isFetching}>
          {q.isFetching ? <Loader2 className="animate-spin" /> : <Search />} Search
        </Button>
      </div>
      {q.data?.mode === 'off' && <SetupProblems problems={q.data.problems} />}
      {q.data && q.data.mode !== 'off' && q.data.instant === false && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          Numbers in {country} need a regulatory bundle approved by Twilio first (1–3 business days). The order waits on the Numbers page until you approve it.
        </p>
      )}
      {q.data && q.data.mode !== 'off' && (q.data.numbers.length === 0 ? (
        <EmptyState className="py-6" title="Nothing found" description="Try another area code, or leave it empty." />
      ) : (
        <div className="max-h-64 divide-y overflow-auto rounded-lg border">
          {q.data.numbers.map((n) => (
            <NumberRow key={n.e164} n={n} selected={value.source === 'buy' && value.e164 === n.e164}
              onPick={() => onChange({ source: 'buy', e164: n.e164 })} />
          ))}
        </div>
      ))}
    </div>
  )
}

/**
 * Pick the business's phone number: one already in the Twilio account, a new one to buy, or none yet.
 * Whatever is picked is then set up end to end (Twilio trunk, LiveKit trunk, routing, test call).
 */
export function NumberChooser({ value, onChange, allowNone, defaultCountry = 'AU' }: {
  value: NumberChoice
  onChange: (v: NumberChoice) => void
  allowNone?: boolean
  defaultCountry?: string
}) {
  const [tab, setTab] = useState<string>(value.source === 'none' && allowNone ? 'owned' : value.source === 'buy' ? 'buy' : 'owned')
  return (
    <Tabs value={tab} onValueChange={(t) => setTab(String(t))}>
      <TabsList>
        <TabsTrigger value="owned">Use a number I own</TabsTrigger>
        <TabsTrigger value="buy">Buy a new number</TabsTrigger>
        {allowNone && <TabsTrigger value="none">Later</TabsTrigger>}
      </TabsList>
      <TabsContent value="owned" className="pt-2"><OwnedTab value={value} onChange={onChange} /></TabsContent>
      <TabsContent value="buy" className="pt-2"><BuyTab value={value} onChange={onChange} defaultCountry={defaultCountry} /></TabsContent>
      {allowNone && (
        <TabsContent value="none" className="pt-2">
          <div className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm">
            <span className="text-muted-foreground">No number now. Add one later from the business's Numbers tab.</span>
            <Button size="sm" variant={value.source === 'none' ? 'default' : 'outline'} onClick={() => onChange({ source: 'none', e164: '' })}>
              {value.source === 'none' && <Check />} No number yet
            </Button>
          </div>
        </TabsContent>
      )}
    </Tabs>
  )
}

const STEP_LABELS: [string, string, string][] = [
  ['purchased', 'Bought', 'Found in your account'],
  ['trunk', 'On the Twilio SIP trunk', 'On the Twilio SIP trunk'],
  ['livekit', 'On the LiveKit trunk', 'On the LiveKit trunk'],
  ['mapped', 'Routed to the business', 'Routed to the business'],
  ['verified', 'Test call answered', 'Test call answered'],
]

function orderVariant(status: NumberOrder['status']): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'active') return 'default'
  if (status === 'failed' || status === 'verify_failed') return 'destructive'
  if (status === 'needs_documents') return 'secondary'
  return 'outline'
}

/** One number order's steps, live. Calls are already answered once "Routed" is done. */
export function OrderProgress({ order, action }: { order: NumberOrder; action?: ReactNode }) {
  const running = order.status === 'pending' || order.status === 'running'
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{order.e164}</span>
          <Badge variant={orderVariant(order.status)}>{titleCase(order.status)}</Badge>
          {running && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
        </div>
        {action}
      </div>
      <ol className="grid gap-1 text-sm sm:grid-cols-5">
        {STEP_LABELS.map(([key, buy, owned]) => {
          const done = order.steps[key]
          const failed = !done && order.failed_step === key && (order.status === 'failed' || order.status === 'verify_failed')
          return (
            <li key={key} className={cn('flex items-center gap-1.5', done ? 'text-foreground' : 'text-muted-foreground')}>
              {done ? <Check className="size-3.5 text-primary" /> : failed ? <XCircle className="size-3.5 text-destructive" /> : <Circle className="size-3" />}
              {order.source === 'owned' ? owned : buy}
            </li>
          )
        })}
      </ol>
      {order.error && <p className="text-xs text-destructive">{order.error}</p>}
      {order.status === 'needs_documents' && <p className="text-xs text-muted-foreground">Waiting for the regulatory bundle: approve it on the Numbers page.</p>}
    </div>
  )
}
