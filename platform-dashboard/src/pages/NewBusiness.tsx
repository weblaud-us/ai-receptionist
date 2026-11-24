import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ArrowLeft, ArrowRight, Check, Copy, ExternalLink, Globe, Loader2, Mail, PencilLine, Phone, Rocket } from 'lucide-react'
import { Field, PageHeader } from '@receptionist/shared/components/blocks'
import { SiteDraftReview } from '@receptionist/shared/components/site-draft-review'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { draftForApi, emptyDraft, type SiteDraft } from '@receptionist/shared/lib/site-import'
import { titleCase } from '@receptionist/shared/lib/format'
import { impersonateUrl } from '@receptionist/shared/lib/urls'
import { cn } from '@receptionist/shared/lib/utils'
import { NumberChooser, OrderProgress } from '@/components/number-chooser'
import {
  usePlatformMutations,
  usePlatformTenant,
  useSiteImport,
  useVerticals,
  type NumberChoice,
  type ProvisionResult,
} from '@/lib/platform'

const STEPS = ['website', 'review', 'owner', 'number', 'create'] as const
type Step = (typeof STEPS)[number]
const LABELS: Record<Step, string> = { website: 'Website', review: 'Details', owner: 'Owner', number: 'Phone number', create: 'Create' }
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

const manualDraft = (): SiteDraft => {
  const d = emptyDraft()
  // LaudLine sells to dental practices in Australia and the UK.
  d.profile = { ...d.profile, vertical: 'dental', country: 'AU', timezone: 'Australia/Sydney', currency: 'AUD' }
  return d
}

/** One-step onboarding: website → reviewed details → owner → number → created, with everything automated. */
export default function NewBusinessPage() {
  const [run, setRun] = useState(0)
  return <NewBusiness key={run} onReset={() => setRun((n) => n + 1)} />
}

function NewBusiness({ onReset }: { onReset: () => void }) {
  const verticals = useVerticals()
  const siteImport = useSiteImport()
  const m = usePlatformMutations()
  const [step, setStep] = useState<Step>('website')
  const [url, setUrl] = useState('')
  const [draft, setDraft] = useState<SiteDraft>(manualDraft)
  const [owner, setOwner] = useState({ email: '', name: '' })
  const [sendInvite, setSendInvite] = useState(true)
  const [markOnboarded, setMarkOnboarded] = useState(true)
  const [number, setNumber] = useState<NumberChoice>({ source: 'none', e164: '' })
  const [result, setResult] = useState<ProvisionResult | null>(null)

  const p = draft.profile
  const detailsOk = p.name.trim().length >= 2 && !!p.vertical && !!p.timezone && /^[A-Z]{2}$/.test(p.country)
  const vlist = (verticals.data?.verticals ?? []).filter((v) => v.key === 'dental').map((v) => ({ key: v.key, label: v.label }))
  const verticalLabel = vlist.find((v) => v.key === p.vertical)?.label ?? titleCase(p.vertical)

  const fetchSite = () =>
    siteImport.mutate(url, {
      onSuccess: ({ draft: d }) => {
        setDraft(d)
        if (!owner.email && d.profile.email) setOwner((o) => ({ ...o, email: d.profile.email }))
        toast.success(`Read ${d.sources.length} page${d.sources.length === 1 ? '' : 's'}. Check the details.`)
        setStep('review')
      },
    })

  const create = () => {
    const kept = draftForApi(draft)
    m.provision.mutate({
      name: p.name.trim(), vertical: p.vertical, timezone: p.timezone, country: p.country, currency: p.currency || (p.country === 'GB' ? 'GBP' : 'AUD'),
      phone: p.phone.trim(), description: p.description.trim(), website: p.website.trim(), address: p.address,
      ...kept, owner_email: owner.email.trim(), owner_name: owner.name.trim(),
      number: number.source === 'none' ? { source: 'none', e164: '' } : number,
      send_invite: sendInvite, mark_onboarded: markOnboarded,
    }, { onSuccess: (r) => { setResult(r); toast.success(`${r.tenant.name} created`) } })
  }

  if (result) return <Done result={result} onReset={onReset} />

  const i = STEPS.indexOf(step)
  return (
    <>
      <Button size="sm" variant="ghost" className="-ml-2 mb-3" nativeButton={false} render={<Link to="/" />}>
        <ArrowLeft /> Businesses
      </Button>
      <PageHeader title="New business" description="Start from the business's website: details, hours, services and FAQs are filled in for you to check. The number, owner invite and setup are done automatically." />
      <ol className="mb-4 flex flex-wrap gap-2">
        {STEPS.map((s, n) => (
          <li key={s} className={cn('flex items-center gap-2 rounded-full border px-3 py-1 text-xs',
            n === i ? 'border-primary bg-primary/10 font-medium' : n < i ? 'text-foreground' : 'text-muted-foreground')}>
            <span className={cn('flex size-4 items-center justify-center rounded-full text-[10px]', n < i ? 'bg-primary text-primary-foreground' : 'border')}>
              {n < i ? <Check className="size-3" /> : n + 1}
            </span>
            {LABELS[s]}
          </li>
        ))}
      </ol>

      <Card>
        {step === 'website' && (
          <>
            <CardHeader>
              <CardTitle>Start from the website</CardTitle>
              <CardDescription>Reads the homepage and up to 7 more pages (contact, hours, services, prices, FAQ). Nothing is saved until you create the business.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input autoFocus value={url} placeholder="bellasalon.com" disabled={siteImport.isPending}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && url.trim().length > 3) fetchSite() }} />
                <Button disabled={url.trim().length < 4 || siteImport.isPending} onClick={fetchSite}>
                  {siteImport.isPending ? <Loader2 className="animate-spin" /> : <Globe />} Fetch details
                </Button>
              </div>
              {siteImport.isPending && <p className="text-sm text-muted-foreground">Reading the website. This usually takes 15–40 seconds…</p>}
              <div className="border-t pt-4">
                <Button variant="outline" disabled={siteImport.isPending} onClick={() => { setDraft(manualDraft()); setStep('review') }}>
                  <PencilLine /> No website: enter details by hand
                </Button>
              </div>
            </CardContent>
          </>
        )}

        {step === 'review' && (
          <>
            <CardHeader>
              <CardTitle>Check the details</CardTitle>
              <CardDescription>Name, type, country and timezone are required. Services and answers replace the type's examples.</CardDescription>
            </CardHeader>
            <CardContent>
              <SiteDraftReview draft={draft} onChange={setDraft} verticals={vlist} showServices={p.vertical !== 'hotel'} />
            </CardContent>
          </>
        )}

        {step === 'owner' && (
          <>
            <CardHeader>
              <CardTitle>Owner</CardTitle>
              <CardDescription>The person who signs in to the client dashboard. They get alerts for bookings and messages too.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Owner e-mail"><Input type="email" value={owner.email} onChange={(e) => setOwner({ ...owner, email: e.target.value })} /></Field>
                <Field label="Owner name (optional)"><Input value={owner.name} onChange={(e) => setOwner({ ...owner, name: e.target.value })} /></Field>
              </div>
              <Toggle checked={sendInvite} onChange={setSendInvite} title="E-mail the invite"
                hint="Sends a welcome e-mail with a link to set their password (valid 7 days). You also get the link to copy." />
              <Toggle checked={markOnboarded} onChange={setMarkOnboarded} title="Mark setup as done"
                hint="The owner skips the setup wizard and lands on their dashboard, since you've set everything up." />
            </CardContent>
          </>
        )}

        {step === 'number' && (
          <>
            <CardHeader>
              <CardTitle>Phone number</CardTitle>
              <CardDescription>Whatever you pick is put on the SIP trunk, added to LiveKit, routed to this business and test-called automatically.</CardDescription>
            </CardHeader>
            <CardContent>
              <NumberChooser value={number} onChange={setNumber} allowNone defaultCountry={p.country} />
            </CardContent>
          </>
        )}

        {step === 'create' && (
          <>
            <CardHeader>
              <CardTitle>Ready to create</CardTitle>
              <CardDescription>Everything below happens in one go.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Summary icon={<Rocket />} title={`${p.name} · ${verticalLabel}`}>
                {[p.address.city, p.country, p.timezone].filter(Boolean).join(' · ')}
              </Summary>
              <Summary icon={<Globe />} title="Setup">
                {draft.hours.length ? `${new Set(draft.hours.map((h) => h.dow)).size} open days` : 'Default hours'} ·{' '}
                {draftForApi(draft).services.length ? `${draftForApi(draft).services.length} services from the website` : 'example services'} ·{' '}
                {draftForApi(draft).faqs.length ? `${draftForApi(draft).faqs.length} answers from the website` : 'example answers'}
              </Summary>
              <Summary icon={<Mail />} title={owner.email}>
                {sendInvite ? 'Welcome e-mail with a set-password link' : 'No e-mail: you get the link to send'}
                {markOnboarded ? ' · setup marked done' : ' · owner runs the setup wizard'}
              </Summary>
              <Summary icon={<Phone />} title={number.source === 'none' ? 'No number yet' : number.e164}>
                {number.source === 'owned' ? 'From your Twilio account: connected automatically'
                  : number.source === 'buy' ? 'Bought and connected automatically' : 'Add one later from the business page'}
              </Summary>
            </CardContent>
          </>
        )}

        {step !== 'website' && (
          <CardContent className="flex justify-between border-t pt-4">
            <Button variant="ghost" disabled={m.provision.isPending} onClick={() => setStep(STEPS[i - 1])}><ArrowLeft /> Back</Button>
            {step === 'create' ? (
              <Button disabled={m.provision.isPending} onClick={create}>
                {m.provision.isPending ? <Loader2 className="animate-spin" /> : <Check />} Create business
              </Button>
            ) : (
              <Button
                disabled={(step === 'review' && !detailsOk) || (step === 'owner' && !EMAIL.test(owner.email.trim())) ||
                  (step === 'number' && number.source !== 'none' && !number.e164)}
                onClick={() => setStep(STEPS[i + 1])}>
                Next <ArrowRight />
              </Button>
            )}
          </CardContent>
        )}
      </Card>
    </>
  )
}

function Toggle({ checked, onChange, title, hint }: { checked: boolean; onChange: (v: boolean) => void; title: string; hint: string }) {
  return (
    <label className="flex items-start gap-3 rounded-lg border p-3">
      <Switch className="mt-0.5" checked={checked} onCheckedChange={onChange} />
      <span className="text-sm">
        <span className="font-medium">{title}</span>
        <span className="block text-muted-foreground">{hint}</span>
      </span>
    </label>
  )
}

function Summary({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border p-3 [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-muted-foreground">
      {icon}
      <div><div className="font-medium">{title}</div><div className="text-muted-foreground">{children}</div></div>
    </div>
  )
}

function Done({ result: r, onReset }: { result: ProvisionResult; onReset: () => void }) {
  const details = usePlatformTenant(r.tenant.id)              // refreshes every 3 s while the number is set up
  const order = details.data?.number_orders?.[0] ?? r.order
  const copy = (text: string) => void navigator.clipboard.writeText(text).then(() => toast.success('Copied'))
  return (
    <>
      <PageHeader title={`${r.tenant.name} is set up`} description="Here's what happened." />
      <Card>
        <CardContent className="space-y-3 pt-6 text-sm">
          <Summary icon={<Check className="text-primary!" />} title="Business created">
            {r.added.services || r.added.knowledge
              ? `${r.added.services} services and ${r.added.knowledge} answers from the website`
              : 'With the example services and answers for its type'}
          </Summary>
          <Summary icon={<Mail />} title={r.emailed ? `Welcome e-mail sent to ${r.owner.email}` : `Owner: ${r.owner.email}`}>
            {r.set_password_link ? (
              <div className="mt-1 space-y-1">
                <span>{r.emailed ? 'Their set-password link, in case they need it again:' : "E-mail isn't sent: send them this link to set their password (valid 7 days):"}</span>
                <div className="flex min-w-0 items-start gap-1 rounded border p-2">
                  <code className="min-w-0 flex-1 break-all text-xs text-foreground">{r.set_password_link}</code>
                  <Button size="icon-xs" variant="ghost" aria-label="Copy" onClick={() => copy(r.set_password_link!)}><Copy /></Button>
                </div>
              </div>
            ) : r.emailed ? 'They already had an account, so they were told they have access.' : 'They already had an account: they can sign in now.'}
          </Summary>
          {order ? <OrderProgress order={order} /> : (
            <Summary icon={<Phone />} title={r.number_error ? "The number couldn't be set up" : 'No number yet'}>
              {r.number_error ?? 'Add one from the business page when you are ready.'}
            </Summary>
          )}
        </CardContent>
        <CardContent className="flex flex-wrap justify-end gap-2 border-t pt-4">
          <Button variant="ghost" onClick={onReset}>Onboard another</Button>
          <Button variant="outline" onClick={() => window.open(impersonateUrl(r.tenant.id), '_blank', 'noopener')}>Open as owner <ExternalLink /></Button>
          <Button nativeButton={false} render={<Link to={`/businesses/${r.tenant.id}`} />}>Open details <ArrowRight /></Button>
        </CardContent>
      </Card>
    </>
  )
}
