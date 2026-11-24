import type { ReactNode } from 'react'
import { AlertTriangle, Globe, Plus, X } from 'lucide-react'
import { Field } from './blocks'
import { HoursEditor } from './hours-editor'
import { SimpleSelect } from './simple-select'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Textarea } from './ui/textarea'
import { COUNTRIES, CURRENCIES, TIMEZONES } from '../lib/format'
import { priceAmount, type DraftFaq, type DraftProfile, type DraftService, type SiteDraft } from '../lib/site-import'

function Section({ title, hint, action, children }: { title: string; hint?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

const withCurrent = (current: string, list: readonly string[]) => [...new Set([current, ...list].filter(Boolean))]

/**
 * Review and edit a website import draft before anything is saved. Every field is editable; services
 * and FAQs can be removed one by one or added. `hideProfile` keeps only hours / services / FAQs.
 */
export function SiteDraftReview({
  draft,
  onChange,
  verticals,
  hideProfile,
  showServices = true,
}: {
  draft: SiteDraft
  onChange: (next: SiteDraft) => void
  verticals: { key: string; label: string }[]
  hideProfile?: boolean
  /** Hotels book rooms, not services: hide the services list for them. */
  showServices?: boolean
}) {
  const p = draft.profile
  const setProfile = (patch: Partial<DraftProfile>) => onChange({ ...draft, profile: { ...p, ...patch } })
  const setAddress = (patch: Partial<DraftProfile['address']>) => setProfile({ address: { ...p.address, ...patch } })
  const setService = (i: number, patch: Partial<DraftService>) =>
    onChange({ ...draft, services: draft.services.map((s, j) => (j === i ? { ...s, ...patch } : s)) })
  const setFaq = (i: number, patch: Partial<DraftFaq>) =>
    onChange({ ...draft, faqs: draft.faqs.map((f, j) => (j === i ? { ...f, ...patch } : f)) })

  return (
    <div className="space-y-6">
      {(draft.sources.length > 0 || draft.warnings.length > 0) && (
        <div className="space-y-2 rounded-lg border bg-muted/30 p-3 text-sm">
          {draft.sources.length > 0 && (
            <div className="flex items-start gap-2 text-muted-foreground">
              <Globe className="mt-0.5 size-4 shrink-0" />
              <span>Read {draft.sources.length} page{draft.sources.length === 1 ? '' : 's'}: {draft.sources.map((s) => s.replace(/^https?:\/\//, '')).join(', ')}</span>
            </div>
          )}
          {draft.warnings.map((w) => (
            <div key={w} className="flex items-start gap-2 text-amber-700 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" /> <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {!hideProfile && (
        <Section title="Business" hint="Check everything: it came from the website.">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Business name" className="sm:col-span-2"><Input value={p.name} onChange={(e) => setProfile({ name: e.target.value })} /></Field>
            <Field label="Type">
              <SimpleSelect value={p.vertical} onChange={(v) => setProfile({ vertical: v })}
                options={verticals.map((v) => ({ value: v.key, label: v.label }))} />
            </Field>
            <Field label="Public phone" hint="International format, e.g. +61 2 5550 4120 or +44 20 7946 0418">
              <Input value={p.phone} onChange={(e) => setProfile({ phone: e.target.value })} />
            </Field>
            <Field label="One sentence about the business" className="sm:col-span-2" hint="The assistant says this when callers ask what you do.">
              <Input value={p.description} maxLength={300} onChange={(e) => setProfile({ description: e.target.value })} />
            </Field>
            <Field label="Street address"><Input value={p.address.line1} onChange={(e) => setAddress({ line1: e.target.value })} /></Field>
            <Field label="City"><Input value={p.address.city} onChange={(e) => setAddress({ city: e.target.value })} /></Field>
            <Field label="State / region"><Input value={p.address.region} onChange={(e) => setAddress({ region: e.target.value })} /></Field>
            <Field label="Postcode"><Input value={p.address.postal} onChange={(e) => setAddress({ postal: e.target.value })} /></Field>
            <Field label="Country">
              <SimpleSelect value={p.country} placeholder="Pick a country" onChange={(v) => setProfile({ country: v })}
                options={withCurrent(p.country, COUNTRIES.map(([c]) => c)).map((c) => ({
                  value: c, label: COUNTRIES.find(([k]) => k === c)?.[1] ?? c,
                }))} />
            </Field>
            <Field label="Timezone">
              <SimpleSelect value={p.timezone} placeholder="Pick a timezone" onChange={(v) => setProfile({ timezone: v })}
                options={withCurrent(p.timezone, TIMEZONES).map((z) => ({ value: z, label: z }))} />
            </Field>
            <Field label="Currency">
              <SimpleSelect value={p.currency} placeholder="Pick a currency" onChange={(v) => setProfile({ currency: v })}
                options={withCurrent(p.currency, CURRENCIES).map((c) => ({ value: c, label: c }))} />
            </Field>
            <Field label="Website"><Input value={p.website} onChange={(e) => setProfile({ website: e.target.value })} /></Field>
          </div>
        </Section>
      )}

      <Section title="Opening hours" hint={draft.hours.length ? undefined : 'None found. Leave empty to keep the default hours for this type of business.'}>
        <HoursEditor value={draft.hours} onChange={(hours) => onChange({ ...draft, hours })} />
      </Section>

      {showServices && (
        <Section title={`Services (${draft.services.length})`}
          hint={draft.services.length ? 'Replace the example services. Remove any that callers shouldn’t book.' : 'None found: the example services for this type are kept.'}
          action={<Button size="xs" variant="outline" onClick={() => onChange({ ...draft, services: [...draft.services, { name: '', duration_min: 30, description: '', price: null }] })}><Plus /> Add</Button>}>
          {draft.services.length > 0 && (
            <div className="divide-y rounded-lg border">
              {draft.services.map((s, i) => (
                <div key={i} className="grid gap-2 p-2 sm:grid-cols-[1fr_6rem_8rem_auto] sm:items-center">
                  <Input aria-label="Service name" placeholder="Name" value={s.name} onChange={(e) => setService(i, { name: e.target.value })} />
                  <Input aria-label="Minutes" type="number" min={5} max={600} placeholder="Min" value={s.duration_min ?? ''}
                    onChange={(e) => setService(i, { duration_min: e.target.value ? Number(e.target.value) : null })} />
                  <Input aria-label="Price" placeholder="Price, e.g. from $45" value={s.price?.display ?? ''}
                    onChange={(e) => setService(i, { price: e.target.value ? { amount: priceAmount(e.target.value), currency: s.price?.currency ?? (p.currency || null), display: e.target.value } : null })} />
                  <Button size="icon-xs" variant="ghost" aria-label={`Remove ${s.name || 'service'}`}
                    onClick={() => onChange({ ...draft, services: draft.services.filter((_, j) => j !== i) })}><X /></Button>
                </div>
              ))}
            </div>
          )}
        </Section>
      )}

      <Section title={`Answers to common questions (${draft.faqs.length})`}
        hint={draft.faqs.length ? 'The assistant answers callers with these. Edit or remove any.' : 'None found: the example answers for this type are kept.'}
        action={<Button size="xs" variant="outline" onClick={() => onChange({ ...draft, faqs: [...draft.faqs, { question: '', answer: '' }] })}><Plus /> Add</Button>}>
        {draft.faqs.length > 0 && (
          <div className="divide-y rounded-lg border">
            {draft.faqs.map((f, i) => (
              <div key={i} className="flex items-start gap-2 p-2">
                <div className="grid flex-1 gap-2">
                  <Input aria-label="Question" placeholder="Question" value={f.question} onChange={(e) => setFaq(i, { question: e.target.value })} />
                  <Textarea aria-label="Answer" placeholder="Answer" rows={2} value={f.answer} onChange={(e) => setFaq(i, { answer: e.target.value })} />
                </div>
                <Button size="icon-xs" variant="ghost" aria-label="Remove question"
                  onClick={() => onChange({ ...draft, faqs: draft.faqs.filter((_, j) => j !== i) })}><X /></Button>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}
