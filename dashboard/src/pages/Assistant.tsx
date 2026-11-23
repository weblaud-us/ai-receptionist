import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ExternalLink, Loader2, PhoneCall, Save } from 'lucide-react'
import { ErrorBanner, Field, PageHeader } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { VoicePicker } from '@/components/app/voice-picker'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { isValidPhone, normalizePhone } from '@receptionist/shared/lib/format'
import { useCtx } from '@/lib/tenant'
import { useTestCall, useUpdateSettings } from '@/lib/tenant-hooks'
import type { AgentConfig, Links, Tenant } from '@/lib/tenant-types'
import { regionFor } from '@/lib/region'
import { useDraft } from '@/lib/use-draft'

// English only at launch: the fixed phrases (greeting, "are you there?", transfer lines) and turn
// detection aren't localized yet. Other languages stay listed only for a business that already uses one.
const LANGUAGES = [
  { value: 'en-GB', label: 'English (UK)' }, { value: 'en-AU', label: 'English (Australia)' }, { value: 'en-IE', label: 'English (Ireland)' },
  { value: 'en-US', label: 'English (US)' },
]
const LEGACY_LANGUAGES = [
  { value: 'es-US', label: 'Spanish (US)' }, { value: 'fr-CA', label: 'French (Canada)' }, { value: 'fr-FR', label: 'French' },
  { value: 'de-DE', label: 'German' }, { value: 'ar', label: 'Arabic' }, { value: 'multi', label: 'Auto (callers switch languages)' },
]
const languageOptions = (current: string) =>
  [...LANGUAGES, ...LEGACY_LANGUAGES.filter((l) => l.value === current)]

const PATIENCE = [
  { value: '1.5', label: 'Quick — replies fast, may cut slow talkers off' },
  { value: '2.5', label: 'Balanced (recommended)' },
  { value: '3.5', label: 'Patient — waits longer for callers reading numbers' },
]

type Draft = Pick<Tenant, 'agent' | 'transfer' | 'emergency_instructions' | 'links'>

const linkFields = (dental: boolean): { key: keyof Links; label: string; hint: string }[] => [
  { key: 'booking', label: 'Online booking page', hint: 'Sent when a caller would rather book themselves.' },
  { key: 'menu', label: dental ? 'Fee schedule or price list' : 'Menu or price list', hint: 'A PDF or web page.' },
  { key: 'directions', label: 'Directions', hint: 'Empty = a maps link built from your address.' },
  { key: 'payment', label: 'Payment page', hint: 'Deposits, invoices or online payment.' },
]

const emergencyExample = (dental: boolean, emergency: string) =>
  dental
    ? `e.g. "For facial swelling that affects breathing or swallowing, call ${emergency} or go to hospital now. For a knocked-out adult tooth, keep it in milk and we'll see you first thing."`
    : `e.g. "For a gas smell, leave the building and call ${emergency}, then call us."`

const instructionsExample = (dental: boolean) =>
  dental
    ? "Always offer our new-patient check-up special. Dr Lee doesn't do extractions on Fridays."
    : 'Always mention our loyalty card. Never book new customers on Saturday mornings.'

/** Starts a test call in a new tab. Shared with onboarding. */
export function TestCallButton({ label = 'Call your assistant now' }: { label?: string }) {
  const call = useTestCall()
  return (
    <Button
      size="lg"
      disabled={call.isPending}
      onClick={() =>
        call.mutate(undefined, {
          onSuccess: (r) => {
            window.open(r.url, '_blank', 'noopener')
            toast.success('Opened the call page — allow your microphone')
          },
        })
      }
    >
      {call.isPending ? <Loader2 className="animate-spin" /> : <PhoneCall />} {label}
    </Button>
  )
}

export default function Assistant() {
  const ctx = useCtx()
  const save = useUpdateSettings()
  const { draft, setDraft, dirty, reset } = useDraft<Draft>({
    agent: ctx.tenant.agent,
    transfer: ctx.tenant.transfer,
    emergency_instructions: ctx.tenant.emergency_instructions,
    links: ctx.tenant.links ?? { booking: '', menu: '', directions: '', payment: '' },
  })
  if (!draft) return null
  const a = draft.agent
  const setAgent = (patch: Partial<AgentConfig>) => setDraft({ ...draft, agent: { ...a, ...patch } })
  const region = regionFor(ctx.tenant.country)
  const dental = ctx.vertical.key === 'dental'
  const transferPhone = draft.transfer.number ? normalizePhone(draft.transfer.number, region.dial) : ''
  const transferOk = !transferPhone || isValidPhone(transferPhone)

  return (
    <>
      <PageHeader
        title="Assistant"
        description="How your assistant sounds and behaves. Changes apply from the next call."
        actions={
          <>
            {dirty && <Button variant="outline" onClick={reset}>Discard</Button>}
            <Button disabled={!dirty || !transferOk || save.isPending || ctx.legacy}
              onClick={() => save.mutate({ ...draft, transfer: { ...draft.transfer, number: transferPhone } },
                { onSuccess: () => toast.success('Saved — the next call uses the new settings') })}>
              {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save
            </Button>
          </>
        }
      />
      {ctx.legacy && (
        <div className="mb-4"><ErrorBanner error={new Error('This deployment still reads its settings from .env. Run migration m0003 to edit them here.')} /></div>
      )}
      <div className="grid gap-4 lg:grid-cols-2 lg:items-start">
        <Card>
          <CardHeader>
            <CardTitle>Voice & greeting</CardTitle>
            <CardDescription>Callers hear the greeting within half a second of answering.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Assistant's name"><Input value={a.name} onChange={(e) => setAgent({ name: e.target.value })} /></Field>
              <Field label="Tone">
                <SimpleSelect value={a.tone} onChange={(v) => setAgent({ tone: v as AgentConfig['tone'] })}
                  options={[{ value: 'warm', label: 'Warm' }, { value: 'professional', label: 'Professional' }, { value: 'upbeat', label: 'Upbeat' }]} />
              </Field>
              <Field label="Language">
                <SimpleSelect value={a.stt_language} onChange={(v) => setAgent({ stt_language: v, language: v === 'multi' ? 'en-US' : v })} options={languageOptions(a.stt_language)} />
              </Field>
              <Field label="Voice provider">
                {/* A voice id belongs to one provider: switching provider starts from its default voice. */}
                <SimpleSelect value={a.voice_provider} onChange={(v) => setAgent({ voice_provider: v as AgentConfig['voice_provider'], voice_id: '' })}
                  options={[{ value: 'deepgram', label: 'Deepgram Aura (fastest)' }, { value: 'elevenlabs', label: 'ElevenLabs' },
                    { value: 'cartesia', label: 'Cartesia' }, { value: 'openai', label: 'OpenAI' }]} />
              </Field>
              <Field label="Voice" className="sm:col-span-2">
                <VoicePicker provider={a.voice_provider} value={a.voice_id} onChange={(voice_id) => setAgent({ voice_id })}
                  country={ctx.tenant.country} />
              </Field>
            </div>
            <Field label="Greeting" hint="Leave empty for the standard greeting. Callers must hear that they're talking to a virtual assistant — that's the law in many places.">
              <Textarea rows={2} value={a.greeting} placeholder={ctx.greeting} onChange={(e) => setAgent({ greeting: e.target.value })} />
            </Field>
            {dental && (
              <Field label="Words to listen for"
                hint="Comma-separated: your dentists' names, brands and treatments callers say (e.g. Invisalign, Bupa, zirconia). Common dental terms are already included.">
                <Input value={(a.stt_keyterms ?? []).join(', ')}
                  onChange={(e) => setAgent({ stt_keyterms: e.target.value.split(',').map((w) => w.trimStart()).slice(0, 50) })}
                  onBlur={() => setAgent({ stt_keyterms: (a.stt_keyterms ?? []).map((w) => w.trim()).filter(Boolean) })} />
              </Field>
            )}
            {a.greeting && !/\b(virtual|digital|automated|artificial|AI|bot|robot)\b/i.test(a.greeting) && (
              <p className="text-xs text-amber-700 dark:text-amber-400">⚠ Your greeting doesn't say it's an AI, so we'll add "This is {a.name || 'your assistant'}, a virtual assistant" after the first sentence. Save to hear the result below.</p>
            )}
            <Field label="Patience">
              <SimpleSelect value={String(a.endpointing.max_delay <= 1.8 ? 1.5 : a.endpointing.max_delay >= 3.2 ? 3.5 : 2.5)}
                onChange={(v) => setAgent({ endpointing: { ...a.endpointing, max_delay: Number(v) } })} options={PATIENCE} />
            </Field>
            <label className="flex items-center gap-2 text-sm"><Switch checked={a.ambience} onCheckedChange={(v) => setAgent({ ambience: v })} /> Quiet office background sound</label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Try it</CardTitle>
            <CardDescription>Talk to your assistant from the browser, exactly as a caller would hear it (save first).</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <TestCallButton />
            <p className="text-sm text-muted-foreground">
              Current greeting: <span className="text-foreground">“{ctx.greeting}”</span>
            </p>
            <Link className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline" to="/knowledge">
              Add answers to common questions <ExternalLink className="size-3" />
            </Link>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Transfers & emergencies</CardTitle>
            <CardDescription>When a caller asks for a person, is upset, or has an emergency.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Field label="Transfer to (your staff line)" hint={!transferOk ? `Include the area code, e.g. ${region.phoneExample}` : 'Empty = never transfer; the assistant takes a message instead.'}>
              <Input value={draft.transfer.number} aria-invalid={!transferOk} placeholder={region.phoneExample} inputMode="tel"
                onChange={(e) => setDraft({ ...draft, transfer: { ...draft.transfer, number: e.target.value } })} />
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={draft.transfer.only_during_hours}
                onCheckedChange={(v) => setDraft({ ...draft, transfer: { ...draft.transfer, only_during_hours: v } })} />
              Only transfer while we're open
            </label>
            <Field label="Emergency instructions" hint={emergencyExample(dental, region.emergency)}>
              <Textarea rows={2} value={draft.emergency_instructions} onChange={(e) => setDraft({ ...draft, emergency_instructions: e.target.value })} />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Your instructions</CardTitle>
            <CardDescription>Preferences in your own words. They can't override safety and honesty rules.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea rows={6} maxLength={1500} value={a.custom_instructions}
              placeholder={instructionsExample(dental)}
              onChange={(e) => setAgent({ custom_instructions: e.target.value })} />
            <p className="text-right text-xs text-muted-foreground">{a.custom_instructions.length} / 1500</p>
            <div className="space-y-2 rounded-lg border p-3">
              <p className="text-sm font-medium">Calls are transcribed, never recorded</p>
              <p className="text-xs text-muted-foreground">⚖ {region.callNote}</p>
              <Field label="What callers are told" hint="Leave empty for the standard sentence.">
                <Input maxLength={200} value={a.call_notice ?? ''} placeholder={dental
                  ? 'Just so you know, this call is transcribed for the practice.'
                  : 'This call is transcribed for the team.'}
                  onChange={(e) => setAgent({ call_notice: e.target.value })} />
              </Field>
              <Field label="Delete call transcripts after (days)" hint="The call itself (who, when, outcome, summary) is kept.">
                <Input type="number" min={1} max={3650} className="w-32" value={a.transcript_retention_days ?? 90}
                  onChange={(e) => setAgent({ transcript_retention_days: Number(e.target.value) })} />
              </Field>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Links your assistant can text</CardTitle>
            <CardDescription>
              When a caller says yes to a message, the assistant sends one of these{ctx.sms_enabled === false ? ' on WhatsApp' : ' by SMS or WhatsApp'}. Leave a field empty and it won't be offered.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            {linkFields(dental).map((f) => (
              <Field key={f.key} label={f.label} hint={f.hint}>
                <Input value={draft.links[f.key] ?? ''} placeholder="https://…"
                  onChange={(e) => setDraft({ ...draft, links: { ...draft.links, [f.key]: e.target.value } })} />
              </Field>
            ))}
          </CardContent>
        </Card>
      </div>
    </>
  )
}
