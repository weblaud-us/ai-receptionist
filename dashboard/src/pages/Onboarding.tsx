import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { ArrowLeft, ArrowRight, Check, Loader2, Plus, Trash2 } from 'lucide-react'
import { ErrorBanner, Field, PageHeader } from '@/components/app/blocks'
import { HoursEditor } from '@/components/app/hours-editor'
import { SimpleSelect } from '@/components/app/simple-select'
import { VoicePicker } from '@/components/app/voice-picker'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { isValidPhone, normalizePhone } from '@/lib/format'
import { useCtx } from '@/lib/tenant'
import { TIMEZONES } from '@/lib/tenant-format'
import { useOnboarding, useServiceMutations, useServices, useUpdateSettings, useVerticals } from '@/lib/tenant-hooks'
import type { HoursEntry } from '@/lib/tenant-types'
import { cn } from '@/lib/utils'
import { TestCallButton } from './Assistant'

const STEPS = ['type', 'basics', 'hours', 'services', 'handoff', 'voice', 'test'] as const
type Step = (typeof STEPS)[number]
const TITLES: Record<Step, string> = {
  type: 'What kind of business?', basics: 'The basics', hours: 'Opening hours', services: 'What callers can book',
  handoff: 'Transfers and alerts', voice: 'Voice and greeting', test: 'Call your assistant',
}

export default function Onboarding() {
  const ctx = useCtx()
  const navigate = useNavigate()
  const ob = useOnboarding()
  const save = useUpdateSettings()
  const steps = STEPS.filter((s) => s !== 'services' || ctx.vertical.uses_scheduling)
  const saved = ctx.tenant.onboarding?.step as Step | undefined
  const [step, setStep] = useState<Step>(saved && steps.includes(saved) ? saved : 'type')
  const i = steps.indexOf(step)
  const busy = ob.template.isPending || ob.step.isPending || save.isPending

  const go = (next: Step) => {
    setStep(next)
    ob.step.mutate({ step: next })
  }
  const finish = () => ob.step.mutate({ step: 'test', completed: true }, {
    onSuccess: () => { toast.success("You're live! Now forward your calls."); navigate('/phone') },
  })

  if (ctx.legacy) {
    return <ErrorBanner error={new Error('This deployment still runs from .env. Run migration m0003 before using setup.')} />
  }

  return (
    <>
      <PageHeader title="Set up your assistant" description="About ten minutes. You can change everything later." />
      <ol className="mb-6 flex flex-wrap gap-2">
        {steps.map((s, n) => (
          <li key={s}>
            <button type="button" onClick={() => n <= i && setStep(s)}
              className={cn('flex items-center gap-2 rounded-full border px-3 py-1 text-xs',
                n === i ? 'border-primary bg-primary text-primary-foreground' : n < i ? 'bg-muted' : 'text-muted-foreground')}>
              {n < i ? <Check className="size-3" /> : <span>{n + 1}</span>} {TITLES[s]}
            </button>
          </li>
        ))}
      </ol>
      <Card className="max-w-3xl">
        <CardHeader><CardTitle>{TITLES[step]}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          {step === 'type' && <TypeStep onNext={() => go(steps[i + 1])} />}
          {step === 'basics' && <BasicsStep onSave={(changes) => save.mutate(changes, { onSuccess: () => go(steps[i + 1]) })} />}
          {step === 'hours' && <HoursStep onSave={(hours) => save.mutate({ hours }, { onSuccess: () => go(steps[i + 1]) })} />}
          {step === 'services' && <ServicesStep />}
          {step === 'handoff' && <HandoffStep onSave={(changes) => save.mutate(changes, { onSuccess: () => go(steps[i + 1]) })} />}
          {step === 'voice' && <VoiceStep onSave={(changes) => save.mutate(changes, { onSuccess: () => go(steps[i + 1]) })} />}
          {step === 'test' && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Ask it what you'd ask as a customer: your hours, a booking, something it shouldn't know. Then forward your phone line to it.
              </p>
              <TestCallButton />
            </div>
          )}
          <div className="flex justify-between border-t pt-4">
            <Button variant="ghost" disabled={i === 0} onClick={() => setStep(steps[i - 1])}><ArrowLeft /> Back</Button>
            {step === 'services' && <Button onClick={() => go(steps[i + 1])}>Next <ArrowRight /></Button>}
            {step === 'test' && <Button disabled={busy} onClick={finish}><Check /> Finish setup</Button>}
            {busy && <Loader2 className="size-5 animate-spin text-muted-foreground" />}
          </div>
        </CardContent>
      </Card>
    </>
  )
}

function TypeStep({ onNext }: { onNext: () => void }) {
  const ctx = useCtx()
  const verticals = useVerticals()
  const ob = useOnboarding()
  const [picked, setPicked] = useState(ctx.tenant.vertical)
  return (
    <>
      <div className="grid gap-2 sm:grid-cols-2">
        {verticals.data?.verticals.map((v) => (
          <button key={v.key} type="button" onClick={() => setPicked(v.key)}
            className={cn('rounded-lg border p-3 text-left transition-colors hover:bg-muted', picked === v.key && 'border-primary ring-2 ring-primary/30')}>
            <div className="font-medium">{v.label}</div>
            <div className="text-xs text-muted-foreground">{v.description}</div>
          </button>
        ))}
      </div>
      <Button disabled={ob.template.isPending}
        onClick={() => ob.template.mutate({ vertical: picked }, {
          onSuccess: (r) => {
            if (r.added.services || r.added.knowledge) toast.success(`Added ${r.added.services} example services and ${r.added.knowledge} answers to start from`)
            onNext()
          },
        })}>
        {ob.template.isPending && <Loader2 className="animate-spin" />} Continue <ArrowRight />
      </Button>
    </>
  )
}

function BasicsStep({ onSave }: { onSave: (changes: Record<string, unknown>) => void }) {
  const t = useCtx().tenant
  const [name, setName] = useState(t.name)
  const [phone, setPhone] = useState(t.phone)
  const [line1, setLine1] = useState(t.address.line1)
  const [city, setCity] = useState(t.address.city)
  const [tz, setTz] = useState(t.timezone)
  const [description, setDescription] = useState(t.description)
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Business name" className="sm:col-span-2"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="One sentence about you" className="sm:col-span-2" hint="Your assistant uses it when callers ask what you do.">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="Public phone number"><Input value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
        <Field label="Timezone"><SimpleSelect value={tz} onChange={setTz} options={[...new Set([tz, ...TIMEZONES])].map((z) => ({ value: z, label: z }))} /></Field>
        <Field label="Street address"><Input value={line1} onChange={(e) => setLine1(e.target.value)} /></Field>
        <Field label="City"><Input value={city} onChange={(e) => setCity(e.target.value)} /></Field>
      </div>
      <Button disabled={!name.trim()} onClick={() => onSave({ name: name.trim(), phone, timezone: tz, description, address: { line1, city } })}>
        Save and continue <ArrowRight />
      </Button>
    </>
  )
}

function HoursStep({ onSave }: { onSave: (hours: HoursEntry[]) => void }) {
  const [hours, setHours] = useState(useCtx().tenant.hours)
  return (
    <>
      <HoursEditor value={hours} onChange={setHours} />
      <Button onClick={() => onSave(hours)}>Save and continue <ArrowRight /></Button>
    </>
  )
}

function ServicesStep() {
  const L = useCtx().vertical.labels
  const q = useServices()
  const m = useServiceMutations()
  const [name, setName] = useState('')
  const [minutes, setMinutes] = useState(30)
  const [price, setPrice] = useState('')
  return (
    <>
      <CardDescription>We added examples for your type of business — rename, delete or add your own. Staff and closures are under {L.services} later.</CardDescription>
      <div className="divide-y rounded-lg border">
        {q.data?.services.filter((s) => s.active).map((s) => (
          <div key={s.id} className="flex items-center justify-between px-3 py-2 text-sm">
            <span><span className="font-medium">{s.name}</span> · {s.duration_min} min {s.price?.display && `· ${s.price.display}`}</span>
            <Button size="icon-xs" variant="ghost" aria-label="Remove" onClick={() => m.remove.mutate(s.id)}><Trash2 /></Button>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label={L.service}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Minutes"><Input type="number" className="w-24" min={5} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} /></Field>
        <Field label="Price (optional)"><Input className="w-32" value={price} placeholder="from $45" onChange={(e) => setPrice(e.target.value)} /></Field>
        <Button variant="outline" disabled={!name.trim() || m.create.isPending}
          onClick={() => m.create.mutate({ name: name.trim(), duration_min: minutes, price: price ? { display: price } : null },
            { onSuccess: () => { setName(''); setPrice('') } })}>
          <Plus /> Add
        </Button>
      </div>
    </>
  )
}

function HandoffStep({ onSave }: { onSave: (changes: Record<string, unknown>) => void }) {
  const t = useCtx().tenant
  const [transfer, setTransfer] = useState(t.transfer.number)
  const [mobile, setMobile] = useState(t.notifications.owner_phone)
  const [email, setEmail] = useState(t.notifications.owner_email)
  const tNorm = transfer ? normalizePhone(transfer) : ''
  const mNorm = mobile ? normalizePhone(mobile) : ''
  const ok = (!tNorm || isValidPhone(tNorm)) && (!mNorm || isValidPhone(mNorm))
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Transfer callers to" hint="When someone asks for a person. Empty = take a message instead.">
          <Input value={transfer} placeholder="+1…" onChange={(e) => setTransfer(e.target.value)} />
        </Field>
        <Field label="Text me new messages at" hint="Your mobile, in international format.">
          <Input value={mobile} placeholder="+1…" onChange={(e) => setMobile(e.target.value)} />
        </Field>
        <Field label="E-mail me bookings at" className="sm:col-span-2"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      </div>
      <Button disabled={!ok} onClick={() => onSave({
        transfer: { number: tNorm, only_during_hours: true },
        notifications: { owner_phone: mNorm, owner_email: email.trim() },
      })}>
        Save and continue <ArrowRight />
      </Button>
    </>
  )
}

function VoiceStep({ onSave }: { onSave: (changes: Record<string, unknown>) => void }) {
  const ctx = useCtx()
  const [name, setName] = useState(ctx.tenant.agent.name)
  const [greeting, setGreeting] = useState(ctx.tenant.agent.greeting)
  const [tone, setTone] = useState(ctx.tenant.agent.tone)
  const [voiceId, setVoiceId] = useState(ctx.tenant.agent.voice_id)
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Assistant's name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Tone">
          <SimpleSelect value={tone} onChange={(v) => setTone(v as typeof tone)}
            options={[{ value: 'warm', label: 'Warm' }, { value: 'professional', label: 'Professional' }, { value: 'upbeat', label: 'Upbeat' }]} />
        </Field>
        <Field label="Voice" className="sm:col-span-2" hint="Press play to hear each voice before you pick.">
          <VoicePicker provider={ctx.tenant.agent.voice_provider} value={voiceId} onChange={setVoiceId} />
        </Field>
        <Field label="Greeting (optional)" className="sm:col-span-2" hint="Must say it's a virtual assistant. Leave empty for the standard one.">
          <Textarea rows={2} value={greeting} placeholder={ctx.greeting} onChange={(e) => setGreeting(e.target.value)} />
        </Field>
      </div>
      <Button disabled={!name.trim()} onClick={() => onSave({ agent: { name: name.trim(), greeting, tone, voice_id: voiceId } })}>
        Save and continue <ArrowRight />
      </Button>
    </>
  )
}
