import { Plus, Trash2 } from 'lucide-react'
import { Field } from '@receptionist/shared/components/blocks'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import type { EscalationConfig } from '@/lib/tenant-types'

const MAX_ON_CALL = 5

/** Phase 2.3: who gets paged, in order, when the assistant takes an urgent dental call. */
export function OnCallCard({ value, onChange, example }: {
  value: EscalationConfig
  onChange: (v: EscalationConfig) => void
  example: string
}) {
  const people = value.on_call
  const setPerson = (i: number, patch: Partial<EscalationConfig['on_call'][number]>) =>
    onChange({ ...value, on_call: people.map((p, j) => (j === i ? { ...p, ...patch } : p)) })
  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>On call for urgent calls</CardTitle>
        <CardDescription>
          When a caller has an urgent dental problem, the first person here gets a WhatsApp message and an e-mail with a
          link to say they're handling it. If nobody does within {value.ack_minutes} minutes, the next person is alerted,
          then you.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={value.enabled} onCheckedChange={(v) => onChange({ ...value, enabled: v })} />
          Alert the on-call list for urgent calls
        </label>
        {people.map((p, i) => (
          <div key={i} className="grid items-end gap-2 sm:grid-cols-[2rem_1fr_1fr_1fr_auto]">
            <span className="pb-2 text-sm text-muted-foreground">{i + 1}.</span>
            <Field label="Name"><Input value={p.name} onChange={(e) => setPerson(i, { name: e.target.value })} /></Field>
            <Field label="WhatsApp"><Input value={p.whatsapp} placeholder={example} inputMode="tel"
              onChange={(e) => setPerson(i, { whatsapp: e.target.value })} /></Field>
            <Field label="E-mail"><Input value={p.email} type="email" onChange={(e) => setPerson(i, { email: e.target.value })} /></Field>
            <Button variant="ghost" size="icon" aria-label={`Remove ${p.name || 'person'}`}
              onClick={() => onChange({ ...value, on_call: people.filter((_, j) => j !== i) })}>
              <Trash2 />
            </Button>
          </div>
        ))}
        <div className="flex flex-wrap items-end gap-3">
          <Button variant="outline" size="sm" disabled={people.length >= MAX_ON_CALL}
            onClick={() => onChange({ ...value, on_call: [...people, { name: '', whatsapp: '', email: '' }] })}>
            <Plus /> Add person
          </Button>
          <Field label="Minutes before the next person">
            <Input className="w-24" type="number" min={2} max={60} value={value.ack_minutes}
              onChange={(e) => onChange({ ...value, ack_minutes: Math.min(60, Math.max(2, Number(e.target.value) || 10)) })} />
          </Field>
        </div>
        <p className="text-xs text-muted-foreground">
          WhatsApp only delivers to someone who has messaged your practice's WhatsApp number in the last day, so the
          e-mail always goes too.
        </p>
      </CardContent>
    </Card>
  )
}
