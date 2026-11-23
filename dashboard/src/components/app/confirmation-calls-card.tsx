import { Field } from '@receptionist/shared/components/blocks'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import type { ConfirmationCallConfig } from '@/lib/tenant-types'

/** Phase 2.5: the assistant rings patients the day before to confirm, move or cancel. */
export function ConfirmationCallsCard({ value, onChange, available }: {
  value: ConfirmationCallConfig
  onChange: (v: ConfirmationCallConfig) => void
  available: boolean
}) {
  const [from = '10:00', to = '18:00'] = value.window.split('-')
  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Confirmation calls</CardTitle>
        <CardDescription>
          The day before an appointment, the assistant calls the patient from your practice number, says it's your AI
          assistant, and confirms, moves or cancels the booking. A voicemail only gives your practice name and number.
          Never on Sundays, and never within two hours of the visit.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!available && (
          <p className="text-sm text-muted-foreground">Calling out isn't set up for your account yet. Contact support to turn it on.</p>
        )}
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={value.enabled} disabled={!available} onCheckedChange={(v) => onChange({ ...value, enabled: v })} />
          Call patients to confirm the day before
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={value.only_without_whatsapp} disabled={!available}
            onCheckedChange={(v) => onChange({ ...value, only_without_whatsapp: v })} />
          Only patients the WhatsApp reminder couldn't reach
        </label>
        <div className="flex flex-wrap gap-3">
          <Field label="Call from"><Input type="time" className="w-32" value={from} disabled={!available}
            onChange={(e) => onChange({ ...value, window: `${e.target.value}-${to}` })} /></Field>
          <Field label="Until"><Input type="time" className="w-32" value={to} disabled={!available}
            onChange={(e) => onChange({ ...value, window: `${from}-${e.target.value}` })} /></Field>
        </div>
      </CardContent>
    </Card>
  )
}
