import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Loader2, MessageSquareText, Search } from 'lucide-react'
import { ErrorBanner, Field, KeyValue } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { api } from '@receptionist/shared/lib/api'
import { useTenant } from '@/lib/tenant'
import { useUpdateSettings } from '@/lib/tenant-hooks'

interface RecallSettings {
  enabled: boolean
  recall_interval_months: number
  template: string
  send_window: { weekday: string[]; saturday: string[] | null }
  voice_enabled: boolean
}

interface RecallStatus {
  available: boolean
  settings: RecallSettings
  default_template: string | null
  queued: number
  last_30_days: { recall_texts_sent: number; reactivation_bookings_attributed: number }
}

interface RecallPreview {
  available: boolean
  eligible: number
  blocked: Record<string, number>
}

const INTERVALS = [6, 9, 12, 18, 24].map((m) => ({ value: String(m), label: `${m} months since the last visit` }))

const BLOCK_REASON: Record<string, string> = {
  not_mobile: 'no mobile number',
  opted_out: 'opted out',
  pms_marketing_opt_out: 'said no to texts in your practice system',
  consent_expired: 'last visit over 24 months ago',
  no_consent_basis: 'no consent on record',
  not_lapsed: 'seen recently',
  has_future_booking: 'already booked',
  too_soon: 'texted in the last 30 days',
  max_attempts: 'already had two recall texts',
}

/** Same checks the server makes (core/tenancy/models.recall_template_problems), for instant feedback. */
function templateHints(text: string): string[] {
  const out: string[] = []
  if (!text.trim()) return out
  if (!text.includes('{clinic_name}')) out.push('Keep {clinic_name} so patients know who it is from.')
  if (!/stop/i.test(text)) out.push('Keep the opt-out instruction, e.g. "Reply STOP to opt out."')
  return out
}

/**
 * Patient recall (dental): one neutral text, and one follow-up, to patients your practice system shows
 * as overdue. Shown only for dental practices with Cliniko or Dentally connected.
 */
export function RecallCard() {
  const { ctx } = useTenant()
  const q = useQuery({ queryKey: ['t-reactivation'], queryFn: () => api<RecallStatus>('/t/reactivation'),
    enabled: ctx?.vertical.key === 'dental' })
  if (ctx?.vertical.key !== 'dental' || !q.data?.available) return null
  return <RecallForm status={q.data} />
}

function RecallForm({ status }: { status: RecallStatus }) {
  const { can } = useTenant()
  const qc = useQueryClient()
  const save = useUpdateSettings()
  const preview = useMutation({ mutationFn: () => api<RecallPreview>('/t/reactivation/preview', { method: 'POST' }) })
  const [draft, setDraft] = useState<RecallSettings>(status.settings)
  const template = draft.template || status.default_template || ''
  const hints = templateHints(draft.template)
  const editable = can('admin')
  const onSave = () =>
    save.mutate({ reactivation: draft }, {
      onSuccess: () => { toast.success('Patient recall saved'); void qc.invalidateQueries({ queryKey: ['t-reactivation'] }) },
      onError: (e) => toast.error(e instanceof Error ? e.message : 'Could not save'),
    })

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><MessageSquareText className="size-4" /> Patient recall</CardTitle>
        <CardDescription>
          A friendly check-up reminder by text to patients your practice system shows as overdue, with your booking
          link. At most one text every 30 days and two per recall, only between 9 am and 6 pm on weekdays (10 am–4 pm on
          Saturdays) and never on Sundays. Anyone who replies STOP is never texted again.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={draft.enabled} disabled={!editable} onCheckedChange={(v) => setDraft({ ...draft, enabled: v })} />
          Send recall texts
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Recall patients after">
            <SimpleSelect value={String(draft.recall_interval_months)} options={INTERVALS} disabled={!editable}
              onChange={(v) => setDraft({ ...draft, recall_interval_months: Number(v) })} />
          </Field>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <KeyValue label="Waiting to send">{status.queued}</KeyValue>
            <KeyValue label="Booked from recall (30 days)">
              {status.last_30_days.reactivation_bookings_attributed} of {status.last_30_days.recall_texts_sent}
            </KeyValue>
          </div>
        </div>
        <Field label="Message"
          hint="Use {first_name}, {clinic_name} and {booking_link}. Keep it general (no treatments or health details) and keep the STOP line.">
          <Textarea rows={3} maxLength={320} value={template} disabled={!editable}
            onChange={(e) => setDraft({ ...draft, template: e.target.value })} />
        </Field>
        {hints.length > 0 && <ul className="list-disc pl-5 text-xs text-amber-700 dark:text-amber-400">{hints.map((h) => <li key={h}>{h}</li>)}</ul>}
        {save.error && <ErrorBanner error={save.error} />}
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={!editable || save.isPending || hints.length > 0} onClick={onSave}>
            {save.isPending && <Loader2 className="animate-spin" />} Save recall settings
          </Button>
          <Button variant="outline" disabled={preview.isPending} onClick={() => preview.mutate()}>
            {preview.isPending ? <Loader2 className="animate-spin" /> : <Search />} Preview who is due
          </Button>
        </div>
        {preview.error && <ErrorBanner error={preview.error} />}
        {preview.data && (
          <div className="rounded-lg border p-3 text-sm">
            <div className="font-medium">{preview.data.eligible} patient{preview.data.eligible === 1 ? '' : 's'} would get a recall text</div>
            {Object.keys(preview.data.blocked).length > 0 && (
              <div className="mt-1 text-muted-foreground">
                Not texted: {Object.entries(preview.data.blocked).map(([k, n]) => `${n} ${BLOCK_REASON[k] ?? k}`).join(' · ')}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
