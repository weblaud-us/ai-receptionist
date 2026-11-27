import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, CheckCircle2, Circle, ExternalLink, Headphones, Loader2, Plus, Rocket, Trash2 } from 'lucide-react'
import { ConfirmDialog } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@receptionist/shared/components/ui/sheet'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { ApiError } from '@receptionist/shared/lib/api'
import { dateTime, titleCase } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { CallAudio } from '@/components/sales/call-audio'
import { useCan } from '@/lib/roles'
import { useLearning } from '@/lib/learning'
import {
  BRAND_LABEL, GOAL_LABEL, previewOffer, startTestCall, useOfferMutations, useSalesStatus,
  type JoinInfo, type Objection, type OfferChannels, type OfferIssue, type OfferPreview, type OfferRoi, type OfferSettings,
  type ScriptVariant, type useOffer,
} from '@/lib/sales'
import { AiAssist, type AssistAction } from './assist'
import { BLANK_OFFER, settingsOf, type EditorStart } from './draft'
import { useMarket } from './market'
import { roiExample, spokenUrl } from './roi'
import { HistoryTab } from './history'
import { TestCallsTab } from './tests'
import { LearningTab } from './learning'
import { ReviewPanel } from './review'
import { completeness, fieldElement, fieldId, issueCounts, shiftUnconfirmed, tabOf, type EditorTab } from './readiness'

const NEW_DRAFT_KEY = 'sales-offer-draft:new'
const AUTOSAVE_MS = 5000

interface LocalDraft { form: OfferSettings; unconfirmed: string[]; aiFields: string[]; sources: Record<string, string>; at: number }

function readLocal(): LocalDraft | null {
  try {
    const raw = localStorage.getItem(NEW_DRAFT_KEY)
    const d = raw ? (JSON.parse(raw) as LocalDraft) : null
    return d && Date.now() - d.at < 7 * 864e5 ? d : null
  } catch {
    return null
  }
}

type OfferData = ReturnType<typeof useOffer>['data'] & { last_review_current?: boolean }

export function OfferForm({ id, data, start, onClose, onCreated, onReload, onCreateCampaign }: {
  id: string | null
  data: OfferData | undefined
  start?: EditorStart
  onClose: () => void
  onCreated: (id: string) => void
  onReload: () => void
  onCreateCampaign?: (offerId: string) => void
}) {
  const m = useOfferMutations()
  const can = useCan()
  const canEdit = can('manager')
  const ai = useSalesStatus().data?.ai_assist
  const [market, setMarket] = useMarket()
  const offer = data?.offer
  const local = useMemo(() => (!id && !start ? readLocal() : null), [id, start])

  const [form, setForm] = useState<OfferSettings>(() => (offer ? settingsOf(offer) : start?.form ?? BLANK_OFFER))
  const [unconfirmed, setUnconfirmed] = useState<string[]>(() => offer?.ai_unconfirmed ?? start?.unconfirmed ?? [])
  // Facts ticked as confirmed since the last save: sent as ai_confirmed, then cleared.
  const [confirmed, setConfirmed] = useState<string[]>([])
  const [aiFields, setAiFields] = useState<Set<string>>(() => new Set(start?.aiFields ?? []))
  const [sources, setSources] = useState<Record<string, string>>(() => start?.sources ?? {})
  const [offerLocal, setOfferLocal] = useState<LocalDraft | null>(local)
  const [variantId, setVariantId] = useState<string>('')
  const [tab, setTab] = useState<EditorTab>('offer')
  const [testing, setTesting] = useState<JoinInfo | null>(null)
  const [startingTest, setStartingTest] = useState(false)
  const [baseUpdatedAt, setBaseUpdatedAt] = useState<string | undefined>(offer?.updated_at)
  const [conflict, setConflict] = useState<{ at: string; by: string } | null>(null)
  const [confirmClose, setConfirmClose] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  const savedJson = useMemo(() => (offer ? JSON.stringify([settingsOf(offer), offer.ai_unconfirmed ?? []]) : ''), [offer])
  const dirty = JSON.stringify([form, unconfirmed]) !== savedJson
  const archived = offer?.status === 'archived'
  const readOnly = archived || !canEdit
  // The lessons the agent uses on calls for this offer (SALES_LEARNING_PLAN.md): preview and test them too.
  const livePlaybook = useLearning(id).data?.live ?? null
  const [withPlaybook, setWithPlaybook] = useState(false)
  const playbookId = withPlaybook && livePlaybook ? livePlaybook.id : undefined
  const preview = usePreview(id ?? 'new', form, variantId, unconfirmed, playbookId)
  const issues: OfferIssue[] = useMemo(() => preview.data?.issues ?? [], [preview.data])
  const problems = preview.data?.problems ?? []
  const counts = issueCounts(issues)
  const ready = completeness(form, unconfirmed)
  const pending = m.create.isPending || m.update.isPending || m.publish.isPending
  const valid = !!preview.data?.prompt && form.name.trim().length >= 2
  const saveNote = id && dirty && !valid && canEdit
    ? (form.name.trim().length < 2 ? 'Not autosaved: name the offer' : 'Not autosaved: fix the items under “Fix before publishing”')
    : ''

  const set = (patch: Partial<OfferSettings>) => {
    setForm((f) => ({ ...f, ...patch }))
    setAiFields((s) => {
      const keys = Object.keys(patch).filter((k) => s.has(k))
      if (!keys.length) return s
      const n = new Set(s)
      keys.forEach((k) => n.delete(k))
      return n
    })
  }
  const addUnconfirmed = (paths: string[]) => setUnconfirmed((u) => [...new Set([...u, ...paths])])
  const confirmFact = (path: string, ok: boolean) => {
    setUnconfirmed((u) => (ok ? u.filter((p) => p !== path) : [...new Set([...u, path])]))
    setConfirmed((c) => (ok ? [...new Set([...c, path])] : c.filter((p) => p !== path)))
  }
  const shiftFacts = (list: string, removed: number) => {
    setUnconfirmed((u) => shiftUnconfirmed(u, list, removed))
    setConfirmed((c) => shiftUnconfirmed(c, list, removed))
  }

  // Keep an unsaved new offer in this browser, so closing the sheet or the tab doesn't lose it.
  useEffect(() => {
    if (id || offerLocal) return
    const t = window.setTimeout(() => {
      try {
        if (JSON.stringify(form) === JSON.stringify(BLANK_OFFER)) return
        localStorage.setItem(NEW_DRAFT_KEY, JSON.stringify({ form, unconfirmed, aiFields: [...aiFields], sources, at: Date.now() } satisfies LocalDraft))
      } catch {
        /* storage full or private mode */
      }
    }, 800)
    return () => window.clearTimeout(t)
  }, [id, form, unconfirmed, aiFields, sources, offerLocal])

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const body = useCallback((extra: { autosave?: boolean; force?: boolean } = {}) => ({
    ...form, ai_unconfirmed: unconfirmed, ai_confirmed: confirmed, autosave: extra.autosave,
    expected_updated_at: extra.force ? undefined : baseUpdatedAt,
  }), [form, unconfirmed, confirmed, baseUpdatedAt])

  const onSaveError = (e: Error) => {
    const detail = e instanceof ApiError ? (e.detail as { code?: string; updated_at?: string; by?: string } | undefined) : undefined
    if (detail?.code === 'stale_draft') setConflict({ at: detail.updated_at ?? '', by: detail.by ?? '' })
    else toast.error(e.message)
  }

  const save = (then?: () => void, opts: { autosave?: boolean; force?: boolean } = {}) => {
    if (!id) {
      m.create.mutate(body(), {
        onSuccess: (r) => {
          toast.success('Draft saved')
          try { localStorage.removeItem(NEW_DRAFT_KEY) } catch { /* ignore */ }
          onCreated(r.offer.id)
          then?.()
        },
      })
      return
    }
    const sent = confirmed
    m.update.mutate({ id, ...body(opts) }, {
      onSuccess: (r) => {
        setConfirmed((c) => c.filter((p) => !sent.includes(p)))
        setUnconfirmed(r.offer.ai_unconfirmed ?? [])          // the server decides what is still unconfirmed
        setBaseUpdatedAt(r.offer.updated_at)
        setSavedAt(Date.now())
        setConflict(null)
        if (!opts.autosave) toast.success('Draft saved')
        then?.()
      },
      onError: onSaveError,
    })
  }

  // Autosave a saved offer a few seconds after the last change, when it would be accepted.
  const debouncedForm = useDebounced(JSON.stringify([form, unconfirmed]), AUTOSAVE_MS)
  const saveRef = useRef(save)
  useEffect(() => {
    saveRef.current = save
  })
  useEffect(() => {
    if (!id || readOnly || !dirty || conflict || m.update.isPending) return
    if (debouncedForm !== JSON.stringify([form, unconfirmed])) return
    if (!valid) return
    saveRef.current(undefined, { autosave: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedForm, valid])

  const testCall = () => {
    setStartingTest(true)
    startTestCall(id ?? 'new', { offer: form, variant_id: variantId || form.variants[0]?.id, playbook_id: playbookId })
      .then((j) => setTesting(j))
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setStartingTest(false))
  }
  const publish = () => {
    if (!id) return
    const go = () => m.publish.mutate(id, {
      onSuccess: (r) => toast.success(`Version ${r.version} published.`, {
        description: 'Run the test calls on it, then create a campaign.',
        action: onCreateCampaign ? { label: 'Create campaign', onClick: () => onCreateCampaign(id) } : undefined,
      }),
    })
    if (dirty) save(go)
    else go()
  }
  const restore = (version: number) => {
    if (!id) return
    m.restore.mutate({ id, version, expected_updated_at: baseUpdatedAt }, {
      onSuccess: (r) => {
        toast.success(`v${version} restored into the draft`)
        setForm(settingsOf(r.offer))
        setUnconfirmed([])
        setConfirmed([])
        setBaseUpdatedAt(r.offer.updated_at)
      },
      onError: onSaveError,
    })
  }
  const goTo = (path: string) => {
    setTab(tabOf(path))
    window.setTimeout(() => {
      const el = fieldElement(path)
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      el?.focus({ preventScroll: true })
    }, 60)
  }
  const applyFix = (path: string, value: string, isFact: boolean) => {
    const next = structuredClone(form) as unknown as Record<string, unknown>
    const parts = path.split('.')
    let cur: Record<string, unknown> | unknown[] = next
    for (const p of parts.slice(0, -1)) cur = (Array.isArray(cur) ? cur[Number(p)] : cur[p]) as Record<string, unknown>
    const last = parts[parts.length - 1]
    if (Array.isArray(cur)) cur[Number(last)] = value
    else cur[last] = value
    set({ [parts[0]]: next[parts[0]] } as Partial<OfferSettings>)
    if (isFact) addUnconfirmed([path])
  }
  const applyAssist = (patch: Partial<OfferSettings>, facts: string[]) => {
    set(patch)
    setAiFields((s) => new Set([...s, ...Object.keys(patch)]))
    addUnconfirmed(facts)
  }
  const requestClose = () => (dirty && !(id && !conflict && valid && canEdit) ? setConfirmClose(true) : onClose())

  const assist = (actions: AssistAction[]) =>
    canEdit && !archived ? <AiAssist actions={actions} form={form} market={market} ai={ai} onApply={applyAssist} /> : null
  const factProps = (path: string) => ({ unconfirmed: unconfirmed.includes(path), source: sources[path], onConfirm: (ok: boolean) => confirmFact(path, ok) })
  const tabLabel = (t: EditorTab, children: ReactNode) => (
    <>
      {children}
      {counts[t].problems > 0 ? <span className="ml-1.5 rounded-full bg-destructive/15 px-1.5 text-xs text-destructive">{counts[t].problems}</span>
        : counts[t].warnings > 0 ? <span className="ml-1.5 rounded-full bg-amber-500/15 px-1.5 text-xs text-amber-700 dark:text-amber-400">{counts[t].warnings}</span> : null}
    </>
  )

  return (
    <Sheet open onOpenChange={(o) => !o && requestClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-6xl">
        <SheetHeader>
          <SheetTitle className="flex flex-wrap items-center gap-3">
            {form.name || 'New offer'}
            <span className="flex items-center gap-2 text-xs font-normal text-muted-foreground" title={ready.checks.filter((c) => !c.done).map((c) => c.label).join('\n')}>
              <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted"><span className="block h-full bg-primary transition-all" style={{ width: `${(ready.done / ready.total) * 100}%` }} /></span>
              Ready {ready.done}/{ready.total}
            </span>
          </SheetTitle>
          <SheetDescription>
            {offer ? <>{titleCase(offer.status)}{offer.published_version ? ` · v${offer.published_version} published` : ' · not published yet'}</> : 'Not saved yet'}
            {m.update.isPending ? ' · saving…' : dirty ? (saveNote ? ` · ${saveNote}` : ' · unsaved changes') : savedAt ? ` · saved ${new Date(savedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
            {' '}· Campaigns keep the version they were signed off on until you move them to a new one.
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-2 px-4">
          {conflict && (
            <Banner tone="danger">
              Someone{conflict.by ? ' else' : ''} saved this offer{conflict.at ? ` at ${dateTime(conflict.at)}` : ''} after you opened it.
              <Button size="xs" variant="outline" onClick={onReload}>Load their version</Button>
              <Button size="xs" variant="outline" onClick={() => save(undefined, { force: true })}>Keep mine</Button>
            </Banner>
          )}
          {offerLocal && (
            <Banner tone="info">
              You have an unsaved new offer from {new Date(offerLocal.at).toLocaleString()}.
              <Button size="xs" variant="outline" onClick={() => { setForm(settingsOf(offerLocal.form)); setUnconfirmed(offerLocal.unconfirmed); setAiFields(new Set(offerLocal.aiFields)); setSources(offerLocal.sources); setOfferLocal(null) }}>Restore it</Button>
              <Button size="xs" variant="ghost" onClick={() => { try { localStorage.removeItem(NEW_DRAFT_KEY) } catch { /* ignore */ } setOfferLocal(null) }}>Discard</Button>
            </Banner>
          )}
          {start && (start.notes || (start.warnings?.length ?? 0) > 0) && aiFields.size > 0 && (
            <Banner tone="info">
              <span>Drafted by AI. Read every tab, confirm the facts marked “AI-written”, then save.{start.notes ? ` ${start.notes}` : ''}{start.warnings?.length ? ` ${start.warnings.join(' ')}` : ''}</span>
            </Banner>
          )}
          {!canEdit && <Banner tone="info">You can look at offers; a sales manager edits and publishes them.</Banner>}
        </div>

        <div className="grid gap-6 px-4 pb-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <Tabs value={tab} onValueChange={(v) => setTab(v as EditorTab)}>
            <TabsList variant="line" className="mb-3 flex-wrap">
              <TabsTrigger value="offer">{tabLabel('offer', <>What you sell</>)}</TabsTrigger>
              <TabsTrigger value="conversation">{tabLabel('conversation', <>Conversation</>)}</TabsTrigger>
              <TabsTrigger value="scripts">{tabLabel('scripts', <>Scripts ({form.variants.length})</>)}</TabsTrigger>
              <TabsTrigger value="follow-up">{tabLabel('follow-up', <>Meeting &amp; emails</>)}</TabsTrigger>
              <TabsTrigger value="consent">{tabLabel('consent', <>Consent &amp; voicemail</>)}</TabsTrigger>
              <TabsTrigger value="prompt">Full prompt</TabsTrigger>
              {id && <TabsTrigger value="tests">Test calls</TabsTrigger>}
              {id && <TabsTrigger value="learning">Learning</TabsTrigger>}
              {id && <TabsTrigger value="history">History ({data?.versions.length ?? 0})</TabsTrigger>}
            </TabsList>
            <fieldset disabled={readOnly} className="space-y-4">
              <TabsContent value="offer" className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <F label="Offer name (only you see this)" path="name" hint="An internal label for this offer, so you can pick it when you create a campaign. Never said on a call." ai={aiFields.has('name')}><Input id={fieldId('name')} value={form.name} placeholder="WebLaud: AI automation for agencies" onChange={(e) => set({ name: e.target.value })} /></F>
                  <F label="Brand" path="brand" hint="Which of your brands this offer is for. Used to sort offers; LaudLine offers are also checked against LaudLine’s wording rules.">
                    <SimpleSelect className="w-full" value={form.brand} onChange={(v) => set({ brand: v as OfferSettings['brand'] })}
                      options={(Object.keys(BRAND_LABEL) as OfferSettings['brand'][]).map((k) => ({ value: k, label: BRAND_LABEL[k] }))} />
                  </F>
                  <F label="Company the agent calls for" path="company_name" hint="Your company: the one making the call, not the business being called. Said in every opener, e.g. “…calling from WebLaud”." ai={aiFields.has('company_name')}><Input id={fieldId('company_name')} value={form.company_name} onChange={(e) => set({ company_name: e.target.value })} /></F>
                  <F label="Agent's name" path="persona_name" hint="The name the AI introduces itself with, e.g. “Hi, this is Lisa, an AI assistant…”. A first name works best."><Input id={fieldId('persona_name')} value={form.persona_name} onChange={(e) => set({ persona_name: e.target.value })} /></F>
                  <F label="Website" path="website" className="sm:col-span-2" hint="Your own website, not the website of the business you’re calling. The AI reads it to draft the offer and find proof points, and it fills {website} in your emails. Each lead’s website goes on the lead instead."><Input id={fieldId('website')} value={form.website} placeholder="weblaud.com" onChange={(e) => set({ website: e.target.value })} /></F>
                </div>
                <F label="What you sell" path="what_we_sell" hint="Two or three plain sentences about your product or service, e.g. “We build AI phone agents that answer and book calls for dental practices.” The agent explains it in its own words." ai={aiFields.has('what_we_sell')}
                  action={assist([{ label: 'Rewrite “what you sell” and “who it’s for”', section: 'basics', action: 'rewrite' }])}>
                  <Textarea id={fieldId('what_we_sell')} rows={3} value={form.what_we_sell} onChange={(e) => set({ what_we_sell: e.target.value })} />
                </F>
                <F label="Who it's for" path="ideal_customer" hint="The kind of business you’re calling and why it suits them, e.g. “Dental practices with 2–10 chairs whose front desk misses calls at busy times.”" ai={aiFields.has('ideal_customer')}><Textarea id={fieldId('ideal_customer')} rows={2} value={form.ideal_customer} onChange={(e) => set({ ideal_customer: e.target.value })} /></F>
                <ListEditor label="Value points" path="value_props" hint="What the customer gets, one short sentence each, e.g. “No missed calls after hours.” Add three or more; the agent picks the one that fits what they said." items={form.value_props} ai={aiFields.has('value_props')}
                  onChange={(value_props) => set({ value_props })}
                  action={assist([{ label: 'Suggest more value points', section: 'value_props' }, { label: 'Reword the value points', section: 'value_props', action: 'rewrite' }])} />
                <ListEditor label="Proof points" path="proof_points" hint="The ONLY facts and results the agent may claim, e.g. “Used by 40 practices in Sydney.” Add real, public ones only: the agent never makes up any others." items={form.proof_points} ai={aiFields.has('proof_points')}
                  onChange={(proof_points, removed) => { set({ proof_points }); if (removed !== undefined) shiftFacts('proof_points', removed) }}
                  fact={(i) => factProps(`proof_points.${i}`)}
                  action={form.proof_points.length > 0 ? assist([{ label: 'Reword the proof points (keeps every fact)', section: 'proof_points', action: 'rewrite' }]) : null} />
              </TabsContent>

              <TabsContent value="conversation" className="space-y-3">
                <ListEditor label="Discovery questions" path="discovery_questions" hint="Open questions about how they work today, asked before any pitch, e.g. “How do you handle calls when the front desk is busy?”" items={form.discovery_questions} ai={aiFields.has('discovery_questions')}
                  onChange={(discovery_questions) => set({ discovery_questions })}
                  action={assist([{ label: 'Suggest discovery questions', section: 'discovery_questions' }, { label: 'Reword them', section: 'discovery_questions', action: 'rewrite' }])} />
                <ListEditor label="Qualification" path="qualification_questions" hint="What the agent finds out naturally: decision maker, size, timing…" items={form.qualification_questions} ai={aiFields.has('qualification_questions')}
                  onChange={(qualification_questions) => set({ qualification_questions })}
                  action={assist([{ label: 'Suggest qualification points', section: 'qualification_questions' }])} />
                <ListEditor label="Problems they're likely to have" path="pain_hypotheses" hint="Guesses the agent checks by asking, never states as fact, e.g. “Missed calls during lunch go to a competitor.”" items={form.pain_hypotheses} ai={aiFields.has('pain_hypotheses')}
                  onChange={(pain_hypotheses) => set({ pain_hypotheses })}
                  action={assist([{ label: 'Suggest likely problems', section: 'pain_hypotheses' }])} />
                <ObjectionsEditor items={form.objections} ai={aiFields.has('objections')} onChange={(objections) => set({ objections })}
                  action={assist([{ label: 'Suggest more objections and answers', section: 'objections' }, { label: 'Make the answers sound natural', section: 'objections', action: 'rewrite' }])} />
                <div className="grid gap-3 sm:grid-cols-2">
                  <F label="Goal of the call" path="call_goal" hint="What counts as a win. The agent steers every call towards this.">
                    <SimpleSelect className="w-full" value={form.call_goal} onChange={(v) => set({ call_goal: v as OfferSettings['call_goal'] })}
                      options={(Object.keys(GOAL_LABEL) as OfferSettings['call_goal'][]).map((k) => ({ value: k, label: GOAL_LABEL[k] }))} />
                  </F>
                  <F label="Price" path="pricing" hint="If asked about price, the agent either says it will be covered in the meeting, or says your one sentence word for word.">
                    <SimpleSelect className="w-full" value={form.pricing.mode} onChange={(v) => set({ pricing: { ...form.pricing, mode: v as 'never' | 'range' } })}
                      options={[{ value: 'never', label: 'Never mention a price' }, { value: 'range', label: 'May say one exact sentence' }]} />
                  </F>
                </div>
                {form.pricing.mode === 'range' && (
                  <F label="The price sentence, word for word" path="pricing.text" hint="Spoken, so write numbers as words." ai={aiFields.has('pricing')}>
                    <Input id={fieldId('pricing.text')} value={form.pricing.text} placeholder="Projects are fixed-price sprints, usually between four and a half and eighteen and a half thousand US dollars." onChange={(e) => set({ pricing: { ...form.pricing, text: e.target.value } })} />
                    <FactConfirm {...factProps('pricing.text')} />
                  </F>
                )}
                <RoiEditor roi={form.roi ?? BLANK_OFFER.roi} currency={form.deal_currency ?? 'USD'} pricingMode={form.pricing.mode}
                  onChange={(roi) => set({ roi })} fact={(i) => factProps(`roi.factors.${i}`)}
                  onRemoveFactor={(i) => shiftFacts('roi.factors', i)} />
              </TabsContent>

              <TabsContent value="scripts" className="space-y-3">
                <div className="flex items-start gap-2">
                  <p className="flex-1 text-sm text-muted-foreground">
                    The opener is spoken word for word: it must say it's an AI, name {form.company_name || 'the company'} and offer a way to opt out. Placeholders: <code>{'{persona}'}</code> <code>{'{company}'}</code> <code>{'{business}'}</code> <code>{'{contact}'}</code>. A campaign can split leads between two scripts.
                  </p>
                  {form.variants.length < 4 && assist([{ label: 'Write another script to A/B test', section: 'variant' }])}
                </div>
                <VariantsEditor items={form.variants} ai={aiFields.has('variants')} onChange={(variants) => set({ variants })}
                  action={(v) => assist([
                    { label: 'Fix this opener so it passes the checks', section: 'fix_opener', target: v.id },
                    { label: 'Sharpen this script', section: 'variant', action: 'rewrite', target: v.id },
                  ])} />
              </TabsContent>

              <TabsContent value="follow-up" className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-3">
                  <F label="Meeting is called" path="meeting.label" hint="How the agent names it, e.g. “discovery call”."><Input id={fieldId('meeting.label')} value={form.meeting.label} onChange={(e) => set({ meeting: { ...form.meeting, label: e.target.value } })} /></F>
                  <F label="Minutes" path="meeting.length_min"><Input id={fieldId('meeting.length_min')} type="number" min={10} max={60} value={form.meeting.length_min} onChange={(e) => set({ meeting: { ...form.meeting, length_min: Number(e.target.value) } })} /></F>
                  <F label="Booked as (service on your calendar)" path="meeting.service_name" hint="The appointment type the booking uses on your calendar."><Input id={fieldId('meeting.service_name')} value={form.meeting.service_name} onChange={(e) => set({ meeting: { ...form.meeting, service_name: e.target.value } })} /></F>
                  <F label="Who they meet" path="meeting.host_name" hint="The person on your side who takes the meeting."><Input id={fieldId('meeting.host_name')} value={form.meeting.host_name} placeholder="Sakib" onChange={(e) => set({ meeting: { ...form.meeting, host_name: e.target.value } })} /></F>
                  <F label="Video link" path="meeting.url" className="sm:col-span-2" hint="Sent in the confirmation email as {meeting_url}."><Input id={fieldId('meeting.url')} value={form.meeting.url} placeholder="https://meet.google.com/…" onChange={(e) => set({ meeting: { ...form.meeting, url: e.target.value } })} /></F>
                </div>
                <ChannelsEditor channels={form.channels ?? BLANK_OFFER.channels} website={form.website} onChange={(channels) => set({ channels })} />
                <div className="flex items-start gap-2">
                  <p className="flex-1 text-xs text-muted-foreground">Email placeholders: {'{first_name} {name} {business} {company} {meeting_label} {meeting_url} {when} {host} {website}'}. Your legal footer and an unsubscribe link are added to every email.</p>
                  {assist([{ label: 'Rewrite both emails', section: 'emails', action: 'rewrite' }])}
                </div>
                <EmailEditor title="Information email" path="info_email" ai={aiFields.has('info_email')} value={form.info_email} onChange={(info_email) => set({ info_email })} />
                <EmailEditor title="Meeting confirmation" path="confirmation_email" ai={aiFields.has('confirmation_email')} value={form.confirmation_email} onChange={(confirmation_email) => set({ confirmation_email })} />
                <div className="flex justify-end">{assist([{ label: 'Write the follow-up emails', section: 'follow_ups', action: 'rewrite' }])}</div>
                <FollowUpsEditor items={form.follow_ups ?? []} onChange={(follow_ups) => set({ follow_ups })} />
                <div className="grid gap-3 sm:grid-cols-3">
                  <F label="A new deal is worth" path="deal_value_default" hint="Used when a call opens a deal; change it on the deal.">
                    <Input id={fieldId('deal_value_default')} type="number" min={0} value={form.deal_value_default ?? ''}
                      onChange={(e) => set({ deal_value_default: e.target.value === '' ? null : Number(e.target.value) })} />
                  </F>
                  <F label="Currency" path="deal_currency">
                    <SimpleSelect className="w-full" value={form.deal_currency ?? 'USD'} onChange={(v) => set({ deal_currency: v })}
                      options={['USD', 'AUD', 'GBP', 'CAD', 'EUR'].map((c) => ({ value: c, label: c }))} />
                  </F>
                </div>
              </TabsContent>

              <TabsContent value="consent" className="space-y-3">
                <F label="Opener for people who asked for a call" path="consented_opener" hint="Only for consented campaigns. Same rules as a cold opener." ai={aiFields.has('consented_opener')}
                  action={assist([{ label: 'Write the consented opener, voicemail and consent wording', section: 'consent_voicemail' }, { label: 'Fix the consented opener', section: 'fix_opener', target: 'consented_opener' }])}>
                  <Textarea id={fieldId('consented_opener')} rows={3} value={form.consented_opener} onChange={(e) => set({ consented_opener: e.target.value })} />
                </F>
                <F label="Opener when they asked us to call back" path="callback_opener"
                  hint="Optional. Said word for word on a call they asked for, e.g. “Hi, it's {persona}, the AI assistant from {company}, calling back as arranged. Is now still a good time? If you'd rather we didn't call, just say so and I'll take you off our list.” Same rules as every opener. Empty = the usual opener.">
                  <Textarea id={fieldId('callback_opener')} rows={3} value={form.callback_opener ?? ''} onChange={(e) => set({ callback_opener: e.target.value })} />
                </F>
                <F label="Voicemail (consented campaigns only)" path="voicemail" hint="Must say it's an AI, name the company and include {callback}." ai={aiFields.has('voicemail')}>
                  <Textarea id={fieldId('voicemail')} rows={3} value={form.voicemail} onChange={(e) => set({ voicemail: e.target.value })} />
                </F>
                <F label="Consent wording next to the “call me” checkbox" path="consent_text" hint="People who agree to this can be called about this offer's company only. Have counsel check it." ai={aiFields.has('consent_text')}>
                  <Textarea id={fieldId('consent_text')} rows={4} value={form.consent_text} onChange={(e) => set({ consent_text: e.target.value })} />
                  <FactConfirm {...factProps('consent_text')} />
                </F>
              </TabsContent>

              <TabsContent value="prompt">
                <p className="mb-2 text-sm text-muted-foreground">What the agent is told, for a sample dental practice in Sydney ({preview.data?.approx_tokens || '…'} tokens). The rules at the top are fixed and can't be changed by an offer.</p>
                {livePlaybook && (
                  <label className="mb-2 flex items-center gap-2 text-sm">
                    <Checkbox checked={withPlaybook} onCheckedChange={(v) => setWithPlaybook(!!v)} />
                    Include the live playbook (v{livePlaybook.n}): its lessons go into the prompt and into browser test calls
                  </label>
                )}
                <pre className="max-h-[60vh] overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap">
                  {!preview.data ? 'Loading…' : preview.data.prompt || 'Fill in the fields listed under “Fix before publishing” to see the prompt.'}
                </pre>
              </TabsContent>
            </fieldset>
            {id && (
              <TabsContent value="learning">
                <LearningTab id={id} form={form} canEdit={canEdit && !archived} onAdd={(patch) => set(patch)} />
              </TabsContent>
            )}
            {id && (
              <TabsContent value="tests">
                <TestCallsTab id={id} form={form} versions={data?.versions ?? []} canRun={canEdit && !archived} problems={problems.length} />
              </TabsContent>
            )}
            {id && (
              <TabsContent value="history">
                <HistoryTab id={id} versions={data?.versions ?? []} campaigns={data?.campaigns ?? []} draft={form}
                  restoring={m.restore.isPending} onRestore={restore} canRestore={canEdit && !archived} />
              </TabsContent>
            )}
          </Tabs>

          <aside className="space-y-4 lg:sticky lg:top-0 lg:self-start">
            <PreviewPanel preview={preview.data} error={preview.error} loading={preview.loading} variants={form.variants}
              variantId={variantId || form.variants[0]?.id} onVariant={setVariantId} onGoTo={goTo} />
            <Checklist checks={ready.checks} onGoTo={goTo} />
            <ReviewPanel id={id} form={form} market={market} ai={ai} disabled={readOnly}
              saved={offer?.last_review?.result} savedCurrent={!dirty && data?.last_review_current}
              onApplyFix={applyFix} onGoTo={goTo} />
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              AI writes for
              <SimpleSelect size="sm" value={market} onChange={(v) => setMarket(v as typeof market)}
                options={[{ value: 'AU', label: 'Australia' }, { value: 'GB', label: 'UK' }, { value: 'US', label: 'US' }, { value: 'CA', label: 'Canada' }]} />
              {ai && <span title={ai.reason}>· ${ai.spent_today_usd.toFixed(2)} of ${ai.daily_cap_usd.toFixed(2)} today</span>}
            </div>
            {(data?.campaigns.length ?? 0) > 0 && (
              <div className="rounded-lg border p-3 text-sm">
                <h3 className="mb-1 font-medium">Used by</h3>
                <ul className="space-y-0.5 text-muted-foreground">{data?.campaigns.map((c) => <li key={c.id}>{c.name} · v{c.offer_version} · {c.status}</li>)}</ul>
              </div>
            )}
          </aside>
        </div>
        <SheetFooter className="flex-row justify-end gap-2 border-t">
          <Button variant="outline" onClick={requestClose}>Close</Button>
          <Button variant="outline" disabled={problems.length > 0 || !!preview.error || startingTest || !!testing} onClick={testCall}
            title="Talk to the agent in your browser, as a prospect would hear it. Nothing is dialled, booked or emailed.">
            {startingTest ? <Loader2 className="animate-spin" /> : <Headphones />} Test call
          </Button>
          {canEdit && (
            <>
              <Button variant="outline" disabled={archived || !dirty || pending || form.name.trim().length < 2} onClick={() => save()}>
                {(m.create.isPending || m.update.isPending) && <Loader2 className="animate-spin" />} Save draft
              </Button>
              <Button disabled={archived || !id || pending || problems.length > 0 || !!preview.error} onClick={publish}
                title={!id ? 'Save the draft first' : problems.length ? 'Fix the problems first' : undefined}>
                {m.publish.isPending ? <Loader2 className="animate-spin" /> : <Rocket />} Publish v{(offer?.published_version ?? 0) + 1}
              </Button>
            </>
          )}
        </SheetFooter>
      </SheetContent>
      <ConfirmDialog open={confirmClose} onOpenChange={setConfirmClose} title="Close without saving?" destructive confirmLabel="Discard changes"
        description={id ? 'Your changes since the last save will be lost.' : 'This offer hasn’t been saved. It stays in this browser for a week, so you can restore it from “New offer”.'}
        onConfirm={() => { setConfirmClose(false); onClose() }} />
      <Dialog open={!!testing} onOpenChange={(o) => !o && setTesting(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Test call: {form.name || 'new offer'}</DialogTitle>
            <DialogDescription>You're the prospect at a sample dental practice in Sydney. Talk as they would: object, ask the price, ask to be called back. Nothing is dialled, booked or emailed; the call ends after 5 minutes.</DialogDescription>
          </DialogHeader>
          {testing && <CallAudio join={testing} title="Test call" onLeave={() => setTesting(null)} />}
        </DialogContent>
      </Dialog>
    </Sheet>
  )
}

/** The opener as it will be spoken, problems and warnings, refreshed as you type. */
function usePreview(id: string, form: OfferSettings, variantId: string, unconfirmed: string[], playbookId?: string) {
  const debounced = useDebounced(form, 600)
  const q = useQuery({
    queryKey: ['s-offer-preview', id, debounced, variantId, unconfirmed, playbookId],
    queryFn: () => previewOffer(id, { offer: debounced, variant_id: variantId || undefined, ai_unconfirmed: unconfirmed, playbook_id: playbookId }),
    placeholderData: keepPreviousData,
    retry: false,
  })
  return { data: q.data, error: q.error ? q.error.message : undefined, loading: q.isFetching }
}

function Banner({ tone, children }: { tone: 'danger' | 'info'; children: ReactNode }) {
  return (
    <div className={cn('flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm',
      tone === 'danger' ? 'border-destructive/40 bg-destructive/5 text-destructive' : 'border-primary/30 bg-primary/5')}>
      {tone === 'danger' && <AlertTriangle className="size-4 shrink-0" />}
      {children}
    </div>
  )
}

/** A labelled field with an "AI" chip while it holds AI text, and room for a ✨ action. */
function F({ label, hint, className, children, ai, action, path }: {
  label: string; hint?: string; className?: string; children: ReactNode; ai?: boolean; action?: ReactNode; path: string
}) {
  return (
    <div className={cn('space-y-1.5', className)} data-field={path}>
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium" htmlFor={fieldId(path)}>{label}</label>
        {ai && <span className="rounded bg-primary/10 px-1.5 text-[10px] font-semibold uppercase tracking-wide text-primary" title="Written by AI. Read it before you save.">AI</span>}
        <span className="flex-1" />
        {action}
      </div>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** An AI-written fact must be confirmed before the offer can be published. */
function FactConfirm({ unconfirmed, source, onConfirm }: { unconfirmed: boolean; source?: string; onConfirm: (ok: boolean) => void }) {
  if (!unconfirmed && !source) return null
  return (
    <div className={cn('flex flex-wrap items-center gap-2 text-xs', unconfirmed ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground')}>
      {unconfirmed && (
        <label className="flex cursor-pointer items-center gap-1.5">
          <Checkbox checked={false} onCheckedChange={(v) => v && onConfirm(true)} />
          AI-written: tick to confirm it's true
        </label>
      )}
      {source && <a className="inline-flex items-center gap-1 underline" href={source} target="_blank" rel="noopener noreferrer">source <ExternalLink className="size-3" /></a>}
    </div>
  )
}

function PreviewPanel({ preview, error, loading, variants, variantId, onVariant, onGoTo }: {
  preview?: OfferPreview; error?: string; loading: boolean; variants: ScriptVariant[]; variantId?: string
  onVariant: (id: string) => void; onGoTo: (path: string) => void
}) {
  const problems = (preview?.issues ?? []).filter((i) => i.level === 'problem')
  const warnings = (preview?.issues ?? []).filter((i) => i.level === 'warning')
  return (
    <>
      <div className="space-y-2 rounded-lg border p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium">Opening line</h3>
          {loading && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
        </div>
        {variants.length > 1 && (
          <SimpleSelect size="sm" className="w-full" value={variantId ?? ''} onChange={onVariant}
            options={variants.map((v) => ({ value: v.id, label: v.label || v.id }))} />
        )}
        <p className="rounded-md bg-muted p-2 text-sm">“{preview?.opener || '…'}”</p>
      </div>
      {error ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>
      ) : problems.length > 0 ? (
        <div className="space-y-1 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <div className="flex items-center gap-1.5 font-medium text-destructive"><AlertTriangle className="size-4" /> Fix before publishing</div>
          <ul className="list-disc pl-4 text-destructive">
            {problems.map((p) => <li key={p.message}><button type="button" className="text-left hover:underline" onClick={() => onGoTo(p.field)}>{p.message}</button></li>)}
          </ul>
        </div>
      ) : preview && (
        <div className="flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 className="size-4" /> Ready to publish
        </div>
      )}
      {warnings.length > 0 && (
        <div className="space-y-1 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <div className="font-medium">Worth a look</div>
          <ul className="list-disc pl-4 text-muted-foreground">
            {warnings.map((w) => <li key={w.message}><button type="button" className="text-left hover:underline" onClick={() => onGoTo(w.field)}>{w.message}</button></li>)}
          </ul>
        </div>
      )}
    </>
  )
}

function Checklist({ checks, onGoTo }: { checks: ReturnType<typeof completeness>['checks']; onGoTo: (path: string) => void }) {
  const open = checks.filter((c) => !c.done)
  if (open.length === 0) return null
  return (
    <div className="space-y-1 rounded-lg border p-3 text-sm">
      <h3 className="font-medium">To make it better</h3>
      <ul className="space-y-0.5">
        {open.map((c) => (
          <li key={c.id}>
            <button type="button" className="flex items-center gap-1.5 text-left text-muted-foreground hover:text-foreground" onClick={() => onGoTo(c.field)}>
              <Circle className={cn('size-3', c.required && 'text-destructive')} /> {c.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ListEditor({ label, hint, items, onChange, max = 6, path, ai, action, fact }: {
  label: string; hint?: string; items: string[]; onChange: (v: string[], removed?: number) => void; max?: number; path: string
  ai?: boolean; action?: ReactNode; fact?: (i: number) => { unconfirmed: boolean; source?: string; onConfirm: (ok: boolean) => void }
}) {
  return (
    <F label={label} hint={hint} path={path} ai={ai} action={action}>
      <div className="space-y-1.5" id={fieldId(path)} tabIndex={-1}>
        {items.map((item, i) => (
          <div key={i} className="space-y-1">
            <div className="flex gap-1.5">
              <Input id={fieldId(`${path}.${i}`)} value={item} maxLength={240} onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))} />
              <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i), i)}><Trash2 /></Button>
            </div>
            {fact && <FactConfirm {...fact(i)} />}
          </div>
        ))}
        {items.length < max && <Button type="button" size="sm" variant="outline" onClick={() => onChange([...items, ''])}><Plus /> Add</Button>}
      </div>
    </F>
  )
}

function ObjectionsEditor({ items, onChange, ai, action }: { items: Objection[]; onChange: (v: Objection[]) => void; ai?: boolean; action?: ReactNode }) {
  const edit = (i: number, patch: Partial<Objection>) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  return (
    <F label="Objections and answers" path="objections" ai={ai} action={action} hint="“Is this a robot?”, “Send me an email” and “Call me later” are always handled.">
      <div className="space-y-2" id={fieldId('objections')} tabIndex={-1}>
        {items.map((o, i) => (
          <div key={i} className="grid gap-1.5 rounded-md border p-2 sm:grid-cols-[12rem_1fr_auto]">
            <Input id={fieldId(`objections.${i}.objection`)} value={o.objection} placeholder="We already have a developer" maxLength={120} onChange={(e) => edit(i, { objection: e.target.value })} />
            <Textarea id={fieldId(`objections.${i}.response`)} rows={2} value={o.response} placeholder="How the agent answers, in a sentence or two" maxLength={400} onChange={(e) => edit(i, { response: e.target.value })} />
            <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 /></Button>
          </div>
        ))}
        {items.length < 12 && <Button type="button" size="sm" variant="outline" onClick={() => onChange([...items, { objection: '', response: '' }])}><Plus /> Add objection</Button>}
      </div>
    </F>
  )
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 32) || 'script'

function VariantsEditor({ items, onChange, ai, action }: { items: ScriptVariant[]; onChange: (v: ScriptVariant[]) => void; ai?: boolean; action: (v: ScriptVariant) => ReactNode }) {
  const edit = (i: number, patch: Partial<ScriptVariant>) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const add = () => {
    const base = items[0]
    let id = `script_${items.length + 1}`
    while (items.some((v) => v.id === id)) id = `${id}_b`
    onChange([...items, { ...base, id, label: `Script ${items.length + 1}` }])
  }
  return (
    <div className="space-y-3" id={fieldId('variants')} tabIndex={-1}>
      {items.map((v, i) => (
        <div key={v.id} className="space-y-2 rounded-lg border p-3">
          <div className="flex items-end gap-2">
            <F label="Script name" path={`variants.${i}.label`} className="flex-1" ai={ai} action={action(v)}>
              <Input id={fieldId(`variants.${i}.label`)} value={v.label} onChange={(e) => edit(i, { label: e.target.value })}
                onBlur={() => !v.id && edit(i, { id: slug(v.label) })} />
            </F>
            <span className="pb-2 font-mono text-xs text-muted-foreground">{v.id}</span>
            {items.length > 1 && <Button type="button" size="icon" variant="ghost" aria-label="Remove script" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 /></Button>}
          </div>
          <F label="AI opener (spoken word for word)" path={`variants.${i}.cold_opener`}><Textarea id={fieldId(`variants.${i}.cold_opener`)} rows={3} maxLength={360} value={v.cold_opener} onChange={(e) => edit(i, { cold_opener: e.target.value })} /></F>
          <F label="First question" path={`variants.${i}.first_question`} hint="Asked right after the opener, if they’re happy to talk. Keep it easy to answer."><Input id={fieldId(`variants.${i}.first_question`)} maxLength={200} value={v.first_question} onChange={(e) => edit(i, { first_question: e.target.value })} /></F>
          <F label="Pitch" path={`variants.${i}.pitch`} hint="One or two sentences; the agent tailors it to what they said."><Textarea id={fieldId(`variants.${i}.pitch`)} rows={2} maxLength={400} value={v.pitch} onChange={(e) => edit(i, { pitch: e.target.value })} /></F>
          <F label="Opener for a person (human-assisted calls)" path={`variants.${i}.human_opener`} hint="Shown to you on the power dialer."><Textarea id={fieldId(`variants.${i}.human_opener`)} rows={2} maxLength={360} value={v.human_opener} onChange={(e) => edit(i, { human_opener: e.target.value })} /></F>
        </div>
      ))}
      {items.length < 4 && <Button type="button" size="sm" variant="outline" onClick={add}><Plus /> Add a script to A/B test</Button>}
    </div>
  )
}

function FollowUpsEditor({ items, onChange }: { items: { after_days: number; subject: string; body: string }[]; onChange: (v: { after_days: number; subject: string; body: string }[]) => void }) {
  const edit = (i: number, patch: Partial<{ after_days: number; subject: string; body: string }>) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  return (
    <div className="space-y-2 rounded-lg border p-3" id={fieldId('follow_ups')} tabIndex={-1}>
      <h3 className="text-sm font-medium">Follow-up emails</h3>
      <p className="text-xs text-muted-foreground">
        Sent only to people who agreed to email (they asked for the information on a call, or booked a meeting) and stopped when they reply, unsubscribe, book, or the deal closes. Never to people who didn't answer.
      </p>
      {items.map((f, i) => (
        <div key={i} className="space-y-1.5 rounded-md border p-2">
          <div className="flex items-end gap-2">
            <F label="Days after" path={`follow_ups.${i}.after_days`} className="w-24">
              <Input type="number" min={1} max={30} value={f.after_days} onChange={(e) => edit(i, { after_days: Number(e.target.value) || 1 })} />
            </F>
            <F label="Subject" path={`follow_ups.${i}.subject`} className="flex-1">
              <Input id={fieldId(`follow_ups.${i}.subject`)} value={f.subject} maxLength={150} onChange={(e) => edit(i, { subject: e.target.value })} />
            </F>
            <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 /></Button>
          </div>
          <Textarea id={fieldId(`follow_ups.${i}.body`)} rows={4} value={f.body} maxLength={4000} onChange={(e) => edit(i, { body: e.target.value })} />
        </div>
      ))}
      {items.length < 3 && (
        <Button type="button" size="sm" variant="outline"
          onClick={() => onChange([...items, { after_days: items.length ? Math.min(30, items[items.length - 1].after_days + 3) : 2, subject: 'Following up from {company}', body: 'Hi {first_name},\n\nJust checking whether you had any questions after our call.' }])}>
          <Plus /> Add a follow-up email
        </Button>
      )}
    </div>
  )
}

function EmailEditor({ title, value, onChange, path, ai }: { title: string; value: { subject: string; body: string }; onChange: (v: { subject: string; body: string }) => void; path: string; ai?: boolean }) {
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <h3 className="flex items-center gap-2 text-sm font-medium">{title}{ai && <span className="rounded bg-primary/10 px-1.5 text-[10px] font-semibold uppercase text-primary">AI</span>}</h3>
      <F label="Subject" path={`${path}.subject`}><Input id={fieldId(`${path}.subject`)} value={value.subject} maxLength={150} onChange={(e) => onChange({ ...value, subject: e.target.value })} /></F>
      <F label="Body" path={`${path}.body`}><Textarea id={fieldId(`${path}.body`)} rows={6} value={value.body} maxLength={4000} onChange={(e) => onChange({ ...value, body: e.target.value })} /></F>
    </div>
  )
}


const PER_OPTIONS = [{ value: 'week', label: 'per week' }, { value: 'month', label: 'per month' }, { value: 'once', label: 'a plain number' }]

function RoiEditor({ roi, currency, pricingMode, onChange, fact, onRemoveFactor }: {
  roi: OfferRoi; currency: string; pricingMode: 'never' | 'range'; onChange: (r: OfferRoi) => void
  fact: (i: number) => { unconfirmed: boolean; source?: string; onConfirm: (ok: boolean) => void }; onRemoveFactor: (i: number) => void
}) {
  const [examples, setExamples] = useState<string[]>([])
  const edit = (patch: Partial<OfferRoi>) => onChange({ ...roi, ...patch })
  const editInput = (i: number, patch: Partial<OfferRoi['inputs'][number]>) => edit({ inputs: roi.inputs.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
  const editFactor = (i: number, patch: Partial<OfferRoi['factors'][number]>) => edit({ factors: roi.factors.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
  const example = roiExample(roi, roi.inputs.map((_, i) => ((examples[i] ?? '') === '' ? NaN : Number(examples[i]))))
  return (
    <div className="space-y-3 rounded-lg border p-3" id={fieldId('roi')} tabIndex={-1} data-field="roi">
      <div className="flex items-center gap-2">
        <h3 className="flex-1 text-sm font-medium">What it’s worth to them (ROI)</h3>
        <label className="flex items-center gap-2 text-sm">Use on calls <Switch checked={roi.enabled} onCheckedChange={(v) => edit({ enabled: !!v })} /></label>
      </div>
      <p className="text-xs text-muted-foreground">
        The agent asks for a few of their numbers, a calculator multiplies them by your figures, and the agent says the result as a rough
        estimate, never a promise. The agent never works a number out itself. Example: missed calls a week × value of a new patient ×
        share of calls you’d answer × share that book.
      </p>
      {roi.enabled && (
        <>
          <F label="Questions the agent asks them" path="roi.inputs"
            hint="Asked out loud, one at a time, in this order. Only one can be per week or per month; a weekly answer is turned into a monthly figure. “Try” is an example answer for the preview below; it isn't saved.">
            <div className="space-y-1.5">
              {roi.inputs.map((x, i) => (
                <div key={i} className="grid gap-1.5 rounded-md border p-2 sm:grid-cols-[1fr_11rem_9rem_5rem_auto]">
                  <Input id={fieldId(`roi.inputs.${i}.question`)} value={x.question} maxLength={160} placeholder="Roughly how many calls go unanswered in a week?"
                    onChange={(e) => editInput(i, { question: e.target.value })} />
                  <Input value={x.label} maxLength={40} placeholder="missed calls a week" title="A short name for the answer" onChange={(e) => editInput(i, { label: e.target.value })} />
                  <SimpleSelect className="w-full" value={x.per} onChange={(v) => editInput(i, { per: v as 'week' | 'month' | 'once' })} options={PER_OPTIONS} />
                  <Input type="number" min={0} value={examples[i] ?? ''} placeholder="Try" title="An example answer, for the preview only"
                    onChange={(e) => setExamples((ex) => { const n = [...ex]; n[i] = e.target.value; return n })} />
                  <Button type="button" size="icon" variant="ghost" aria-label="Remove"
                    onClick={() => { edit({ inputs: roi.inputs.filter((_, j) => j !== i) }); setExamples((ex) => ex.filter((_, j) => j !== i)) }}><Trash2 /></Button>
                </div>
              ))}
              {roi.inputs.length < 4 && (
                <Button type="button" size="sm" variant="outline"
                  onClick={() => edit({ inputs: [...roi.inputs, { question: '', label: '', per: roi.inputs.some((y) => y.per !== 'once') ? 'once' : 'week', max: 1_000_000 }] })}>
                  <Plus /> Add a question
                </Button>
              )}
            </div>
          </F>
          <F label="Your figures" path="roi.factors"
            hint="Your own numbers, multiplied in, e.g. “share of those calls we answer: 80 %”. They're claims, like proof points: only use ones you can back up.">
            <div className="space-y-1.5">
              {roi.factors.map((f, i) => (
                <div key={i} className="space-y-1">
                  <div className="grid gap-1.5 sm:grid-cols-[1fr_7rem_6rem_auto]">
                    <Input id={fieldId(`roi.factors.${i}`)} value={f.label} maxLength={60} placeholder="share of those calls we answer" onChange={(e) => editFactor(i, { label: e.target.value })} />
                    <Input type="number" min={0} step="any" value={f.value} onChange={(e) => editFactor(i, { value: Number(e.target.value) })} />
                    <SimpleSelect className="w-full" value={f.percent ? 'pct' : 'num'} onChange={(v) => editFactor(i, { percent: v === 'pct' })}
                      options={[{ value: 'pct', label: '%' }, { value: 'num', label: 'number' }]} />
                    <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => { edit({ factors: roi.factors.filter((_, j) => j !== i) }); onRemoveFactor(i) }}><Trash2 /></Button>
                  </div>
                  <FactConfirm {...fact(i)} />
                </div>
              ))}
              {roi.factors.length < 4 && <Button type="button" size="sm" variant="outline" onClick={() => edit({ factors: [...roi.factors, { label: '', value: 50, percent: true }] })}><Plus /> Add a figure</Button>}
            </div>
          </F>
          <div className="grid gap-3 sm:grid-cols-2">
            <F label="What the result is" path="roi.result_label" hint="Said after the amount: “…roughly four thousand dollars of extra revenue a month.”">
              <Input id={fieldId('roi.result_label')} value={roi.result_label} maxLength={60} placeholder="extra revenue a month" onChange={(e) => edit({ result_label: e.target.value })} />
            </F>
            <F label={`What it costs them a month (${currency}, optional)`} path="roi.monthly_cost"
              hint={pricingMode === 'range' ? 'When the estimate is well above it, the agent adds “that’s about N times what it costs”.' : 'Only used when the agent may mention a price (Price, above).'}>
              <Input id={fieldId('roi.monthly_cost')} type="number" min={0} value={roi.monthly_cost ?? ''}
                onChange={(e) => edit({ monthly_cost: e.target.value === '' ? null : Number(e.target.value) })} />
            </F>
          </div>
          <p className={cn('rounded-md p-2', example != null ? 'bg-muted text-sm' : 'text-xs text-muted-foreground')}>
            {example != null
              ? <>The agent would say: “Using your numbers, that’s roughly {example.toLocaleString()} {currency} of {roi.result_label || 'value'}. It’s only an estimate, not a promise.” (spoken in words)</>
              : 'Type an example answer next to each question (“Try”) to preview what the agent would say.'}
          </p>
        </>
      )}
    </div>
  )
}

function ChannelsEditor({ channels, website, onChange }: { channels: OfferChannels; website: string; onChange: (c: OfferChannels) => void }) {
  const edit = (patch: Partial<OfferChannels>) => onChange({ ...channels, ...patch })
  const page = channels.booking_url || website
  return (
    <div className="space-y-3 rounded-lg border p-3" id={fieldId('channels')} tabIndex={-1} data-field="channels">
      <h3 className="text-sm font-medium">If they won’t book on the call</h3>
      <p className="text-xs text-muted-foreground">
        The agent first asks to send the details by email{channels.whatsapp ? ' or WhatsApp' : ''}. If they don’t want anything sent, it tells
        them where they can book a time themselves. It never offers SMS.
      </p>
      <F label="Page they can book on themselves" path="channels.booking_url"
        hint={`Said out loud as the last option${page ? `: “${spokenUrl(page)}”` : ''}. Leave empty to use your website${website ? ` (${website})` : ''}. Pick a short address that opens a page where they can choose a time.`}>
        <Input id={fieldId('channels.booking_url')} value={channels.booking_url} maxLength={200} placeholder={website || 'weblaud.com/book'}
          onChange={(e) => edit({ booking_url: e.target.value.trim() })} />
      </F>
      <label className="flex items-center gap-2 text-sm">
        <Switch checked={channels.whatsapp} onCheckedChange={(v) => edit({ whatsapp: !!v })} /> Offer to send the details on WhatsApp
      </label>
      <p className="text-xs text-muted-foreground">
        The agent asks whether the number it called is on WhatsApp, or takes another one and reads it back. Their OK is saved on the lead,
        your approved WhatsApp template is sent with the booking page, and a follow-up task is created for your team. Nothing is sent during test calls.
      </p>
      {channels.whatsapp && (
        <div className="grid gap-3 sm:grid-cols-2">
          <F label="WhatsApp template name" path="channels.whatsapp_template"
            hint="Create it in Meta's WhatsApp Manager and wait for approval. Its text can use {{1}} their first name, {{2}} your company and {{3}} the booking page.">
            <Input id={fieldId('channels.whatsapp_template')} value={channels.whatsapp_template} maxLength={80} placeholder="weblaud_meeting_link"
              onChange={(e) => edit({ whatsapp_template: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') })} />
          </F>
          <F label="Template language" path="channels.whatsapp_language" hint="The language code the template was approved in, e.g. en, en_AU or en_GB.">
            <Input id={fieldId('channels.whatsapp_language')} value={channels.whatsapp_language} maxLength={10} placeholder="en_AU"
              onChange={(e) => edit({ whatsapp_language: e.target.value.trim() })} />
          </F>
        </div>
      )}
    </div>
  )
}
