import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import {
  BookOpen, Check, CheckCheck, Globe, HelpCircle, Lightbulb, Loader2, MessageCircleQuestion, Pencil, Phone, Plus, Search,
  Sparkles, Trash2, X,
} from 'lucide-react'
import { ConfirmDialog, EmptyState, ErrorBanner, Field, PageHeader } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Switch } from '@receptionist/shared/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@receptionist/shared/components/ui/tabs'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { timeAgo } from '@receptionist/shared/lib/format'
import { useCtx, useTenant } from '@/lib/tenant'
import {
  useImportKnowledge, useKnowledge, useKnowledgeMutations, useKnowledgeSuggestions, useTestKnowledge, useUnanswered,
  useUnansweredMutations,
} from '@/lib/tenant-hooks'
import type { KnowledgeItem, KnowledgeTestResult, UnansweredItem } from '@/lib/tenant-types'

/** What the answer dialog is editing: an existing answer, or a new one (optionally pre-filled). */
type Draft = Partial<KnowledgeItem> & { hint?: string }

const SOURCE_LABEL: Record<string, string> = { unanswered: 'From a caller', website: 'From website', template: 'Starter' }

const splitTags = (s: string) => s.split(',').map((t) => t.trim()).filter(Boolean)

export default function Knowledge() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'faqs'
  const open = useUnanswered()
  const kb = useKnowledge()
  const [editing, setEditing] = useState<Draft | null>(null)
  const openCount = open.data?.items.length ?? 0

  return (
    <>
      <PageHeader title="Knowledge" description="What your assistant knows about your business. Callers get these answers instead of “someone will call you back”." />
      <TryItCard onAdd={(question) => setEditing({ question, active: true, tags: [] })} />
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: String(v) })} className="mt-6">
        <TabsList>
          <TabsTrigger value="faqs">
            Answers {kb.data && <Badge variant="secondary" className="ml-1">{kb.data.count}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="unanswered">
            Couldn't answer {openCount > 0 && <Badge variant="destructive" className="ml-1">{openCount}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="import">Import from website</TabsTrigger>
        </TabsList>
        <TabsContent value="faqs"><AnswersTab onEdit={setEditing} /></TabsContent>
        <TabsContent value="unanswered"><UnansweredTab onSuggestions={() => setParams({ tab: 'faqs' })} /></TabsContent>
        <TabsContent value="import"><ImportTab /></TabsContent>
      </Tabs>
      {editing && <AnswerDialog value={editing} onClose={() => setEditing(null)} />}
    </>
  )
}

// ── Try it ───────────────────────────────────────────────

function TryItCard({ onAdd }: { onAdd: (question: string) => void }) {
  const { can } = useTenant()
  const [question, setQuestion] = useState('')
  const test = useTestKnowledge()
  const [result, setResult] = useState<{ q: string; r: KnowledgeTestResult } | null>(null)
  const run = () => {
    const q = question.trim()
    if (q.length < 2) return
    test.mutate(q, { onSuccess: (r) => setResult({ q, r }) })
  }
  const best = result?.r.matches[0]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><MessageCircleQuestion className="size-4" /> Try it — ask like a caller</CardTitle>
        <CardDescription>Check what your assistant would answer before a real caller asks.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); run() }}>
          <Input placeholder="e.g. Do you take my health fund?" value={question} onChange={(e) => setQuestion(e.target.value)} />
          <Button type="submit" disabled={question.trim().length < 2 || test.isPending}>
            {test.isPending ? <Loader2 className="animate-spin" /> : <Search />} Ask
          </Button>
        </form>
        {test.error && <ErrorBanner error={test.error} />}
        {result && (best ? (
          <div className="space-y-2 rounded-lg border bg-muted/40 p-3 text-sm">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Check className="size-3.5 text-emerald-600" />
              {best.strength === 'strong' ? 'Your assistant would answer:' : 'Closest saved answer (weak match — consider adding a direct one):'}
            </div>
            <div className="font-medium">{best.question}</div>
            <div className="text-muted-foreground">{best.answer}</div>
            {result.r.matches.length > 1 && (
              <div className="text-xs text-muted-foreground">Also related: {result.r.matches.slice(1).map((m) => `“${m.question}”`).join(', ')}</div>
            )}
            {best.strength === 'weak' && can('admin') && (
              <Button size="sm" variant="outline" onClick={() => onAdd(result.q)}><Plus /> Add a direct answer</Button>
            )}
          </div>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
            <span>Nothing saved for “{result.q}”. Your assistant would offer to take a message.</span>
            {can('admin') && <Button size="sm" onClick={() => onAdd(result.q)}><Plus /> Add an answer</Button>}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

// ── Answers ──────────────────────────────────────────────

function AnswersTab({ onEdit }: { onEdit: (d: Draft) => void }) {
  const { can } = useTenant()
  const q = useKnowledge()
  const m = useKnowledgeMutations()
  const [filter, setFilter] = useState('')
  const [deleting, setDeleting] = useState<KnowledgeItem | null>(null)
  const items = useMemo(() => {
    const f = filter.trim().toLowerCase()
    const all = q.data?.items ?? []
    return f ? all.filter((i) => `${i.question} ${i.answer} ${i.tags.join(' ')}`.toLowerCase().includes(f)) : all
  }, [q.data, filter])

  return (
    <div className="mt-3 space-y-4">
      {q.data && <Capacity data={q.data} />}
      <Suggestions onAnswer={(question, hint) => onEdit({ question, hint, active: true, tags: [] })} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="relative w-full max-w-sm">
          <Search className="absolute top-2 left-2.5 size-4 text-muted-foreground" />
          <Input className="pl-8" placeholder="Search answers" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        {can('admin') && <Button size="sm" onClick={() => onEdit({ active: true, tags: [] })}><Plus /> Add answer</Button>}
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      {(q.data?.items.length ?? 0) === 0 && !q.isLoading && (
        <Card><EmptyState title="No answers yet" icon={<BookOpen className="size-8" />} description="Start with the suggested questions above, or import from your website." /></Card>
      )}
      {filter && items.length === 0 && <p className="text-sm text-muted-foreground">No answers match “{filter}”.</p>}
      <div className="grid gap-3 lg:grid-cols-2">
        {items.map((item) => (
          <Card key={item.id} className={item.active ? undefined : 'opacity-60'}>
            <CardContent className="flex gap-3">
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="font-medium">{item.question}</div>
                <div className="text-sm whitespace-pre-line text-muted-foreground">{item.answer}</div>
                <div className="flex flex-wrap items-center gap-1 pt-0.5">
                  {!item.active && <Badge variant="outline">Off</Badge>}
                  {item.source && SOURCE_LABEL[item.source] && <Badge variant="secondary">{SOURCE_LABEL[item.source]}</Badge>}
                  {item.tags.map((t) => <Badge key={t} variant="outline" className="font-normal">{t}</Badge>)}
                  {item.updated_at && <span className="text-xs text-muted-foreground">· updated {timeAgo(item.updated_at)}</span>}
                </div>
              </div>
              {can('admin') && (
                <div className="flex shrink-0 flex-col items-end gap-2">
                  <Switch aria-label="Active" checked={item.active}
                    onCheckedChange={(active) => m.update.mutate({ id: item.id, active })} />
                  <div className="flex gap-1">
                    <Button size="icon-xs" variant="ghost" aria-label="Edit" onClick={() => onEdit(item)}><Pencil /></Button>
                    <Button size="icon-xs" variant="ghost" aria-label="Delete" onClick={() => setDeleting(item)}><Trash2 /></Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} title="Delete this answer?"
        description={deleting ? `“${deleting.question}” — your assistant will stop giving this answer. You can switch it off instead to keep it for later.` : undefined}
        confirmLabel="Delete" destructive loading={m.remove.isPending}
        onConfirm={() => deleting && m.remove.mutate(deleting.id, { onSuccess: () => { setDeleting(null); toast.success('Deleted') } })} />
    </div>
  )
}

function Capacity({ data }: { data: { inline: boolean; count: number; chars: number; max_items: number; max_chars: number } }) {
  const pct = Math.min(100, Math.round(Math.max(data.count / data.max_items, data.chars / data.max_chars) * 100))
  return (
    <div className="rounded-lg border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>
          {data.inline
            ? <><strong>{data.count}</strong> active answers — all read on every call, so replies are instant.</>
            : <><strong>{data.count}</strong> active answers — too many to read on every call, so the assistant looks them up when asked (adds a moment).</>}
        </span>
        <span className="text-xs text-muted-foreground">{data.chars.toLocaleString()} / {data.max_chars.toLocaleString()} characters</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
        <div className={`h-full rounded-full ${data.inline ? 'bg-primary' : 'bg-amber-500'}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function Suggestions({ onAnswer }: { onAnswer: (question: string, hint: string) => void }) {
  const { can } = useTenant()
  const ctx = useCtx()
  const q = useKnowledgeSuggestions()
  const [all, setAll] = useState(false)
  const list = q.data?.suggestions ?? []
  if (!can('admin') || list.length === 0) return null
  const shown = all ? list : list.slice(0, 4)
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Lightbulb className="size-4 text-amber-500" /> Questions callers often ask</CardTitle>
        <CardDescription>{list.length} common question{list.length === 1 ? '' : 's'} for a {ctx.vertical.label.split(/[,(]/)[0].trim().toLowerCase()} that you haven't answered yet. Answer them now so callers don't get a callback promise.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {shown.map((s) => (
          <div key={s.question} className="flex items-center justify-between gap-3 rounded-md px-2 py-1.5 hover:bg-muted/50">
            <div className="min-w-0">
              <div className="text-sm font-medium">{s.question}</div>
              <div className="truncate text-xs text-muted-foreground">{s.hint}</div>
            </div>
            <Button size="sm" variant="outline" onClick={() => onAnswer(s.question, s.hint)}><Plus /> Answer</Button>
          </div>
        ))}
        {list.length > 4 && (
          <Button size="sm" variant="ghost" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${list.length}`}</Button>
        )}
      </CardContent>
    </Card>
  )
}

function AnswerDialog({ value, onClose }: { value: Draft; onClose: () => void }) {
  const m = useKnowledgeMutations()
  const [question, setQuestion] = useState(value.question ?? '')
  const [answer, setAnswer] = useState(value.answer ?? '')
  const [tags, setTags] = useState((value.tags ?? []).join(', '))
  const [active, setActive] = useState(value.active ?? true)
  const saving = m.create.isPending || m.update.isPending
  const save = () => {
    const body = { question: question.trim(), answer: answer.trim(), tags: splitTags(tags), active }
    const done = { onSuccess: () => { toast.success('Saved — your assistant knows this from the next call'); onClose() } }
    if (value.id) m.update.mutate({ id: value.id, ...body }, done)
    else m.create.mutate(body, done)
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{value.id ? 'Edit answer' : 'New answer'}</DialogTitle>
          {value.hint && <DialogDescription>Worth covering: {value.hint}</DialogDescription>}
        </DialogHeader>
        <Field label="When a caller asks…"><Input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Is there parking?" /></Field>
        <Field label="…the assistant says" hint={`One or two plain sentences — it's spoken aloud. ${answer.length}/1500`}>
          <Textarea rows={4} autoFocus={!!question} value={answer} maxLength={1500} onChange={(e) => setAnswer(e.target.value)} placeholder="Yes — there's free parking behind the building, and two disabled bays by the door." />
        </Field>
        <Field label="Other words callers use (optional)" hint="Comma-separated, e.g. car park, parking. Helps the assistant find this answer.">
          <Input value={tags} onChange={(e) => setTags(e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 text-sm"><Switch checked={active} onCheckedChange={setActive} /> Active</label>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={question.trim().length < 2 || !answer.trim() || saving} onClick={save}>
            {saving && <Loader2 className="animate-spin" />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Couldn't answer ──────────────────────────────────────

const STATUS_VIEWS = [
  { value: 'open', label: 'Needs an answer' },
  { value: 'answered', label: 'Answered' },
  { value: 'dismissed', label: 'Dismissed' },
] as const

function UnansweredTab({ onSuggestions }: { onSuggestions: () => void }) {
  const { can } = useTenant()
  const [status, setStatus] = useState<'open' | 'answered' | 'dismissed'>('open')
  const q = useUnanswered(status)
  const items = q.data?.items ?? []
  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">Questions callers asked that your assistant had no answer for. Similar wordings are grouped; the most-asked are first.</p>
        <div className="flex gap-1 rounded-lg bg-muted p-0.5">
          {STATUS_VIEWS.map((v) => (
            <Button key={v.value} size="xs" variant={status === v.value ? 'outline' : 'ghost'} onClick={() => setStatus(v.value)}>{v.label}</Button>
          ))}
        </div>
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      {items.length === 0 && !q.isLoading && (
        <Card>
          {status === 'open' ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <HelpCircle className="size-8 text-muted-foreground" />
              <div className="font-medium">Nothing waiting for an answer</div>
              <p className="max-w-md text-sm text-muted-foreground">
                When a caller asks something your assistant doesn't know, it lands here so you can answer it once. Get ahead of it by answering the questions callers usually ask.
              </p>
              {can('admin') && <Button size="sm" variant="outline" onClick={onSuggestions}><Lightbulb /> See suggested questions</Button>}
            </div>
          ) : (
            <EmptyState title={status === 'answered' ? 'Nothing answered yet' : 'Nothing dismissed'} icon={<HelpCircle className="size-8" />} />
          )}
        </Card>
      )}
      {items.map((item) => <UnansweredRow key={item.id} item={item} canEdit={can('admin') && status === 'open'} />)}
    </div>
  )
}

function UnansweredRow({ item, canEdit }: { item: UnansweredItem; canEdit: boolean }) {
  const m = useUnansweredMutations()
  const [question, setQuestion] = useState(item.question)
  const [answer, setAnswer] = useState('')
  const [open, setOpen] = useState(false)
  const count = item.count ?? 1
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="space-y-1">
            <div className="font-medium">“{item.question}”</div>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              {count > 1 && <Badge variant={count >= 3 ? 'destructive' : 'secondary'}>Asked {count}×</Badge>}
              <span>last {timeAgo(item.created_at)}</span>
              {count > 1 && item.first_asked_at && <span>· first {timeAgo(item.first_asked_at)}</span>}
              <span>· {item.channel === 'browser' ? 'web call' : item.channel}</span>
              {(item.call_ids ?? []).slice(0, 3).map((id, i) => (
                <Link key={id} to={`/calls?call=${encodeURIComponent(id)}`} className="inline-flex items-center gap-0.5 underline-offset-2 hover:text-foreground hover:underline">
                  <Phone className="size-3" /> call {i + 1}
                </Link>
              ))}
            </div>
          </div>
          {canEdit && !open && (
            <div className="flex gap-1">
              <Button size="sm" onClick={() => setOpen(true)}><Plus /> Answer</Button>
              <Button size="sm" variant="ghost" disabled={m.dismiss.isPending}
                onClick={() => m.dismiss.mutate(item.id, { onSuccess: () => toast.success('Dismissed') })}><X /> Dismiss</Button>
            </div>
          )}
        </div>
        {canEdit && open && (
          <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
            <Field label="Save as question" hint="Tidy up the wording — this is what the assistant matches against.">
              <Input value={question} onChange={(e) => setQuestion(e.target.value)} />
            </Field>
            <Field label="Answer">
              <Textarea rows={2} autoFocus value={answer} maxLength={1500} onChange={(e) => setAnswer(e.target.value)} placeholder="One or two plain sentences." />
            </Field>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button size="sm" disabled={!answer.trim() || question.trim().length < 2 || m.answer.isPending}
                onClick={() => m.answer.mutate({ id: item.id, answer: answer.trim(), question: question.trim() },
                  { onSuccess: () => toast.success(count > 1 ? `Added — clears all ${count} asks` : 'Added to knowledge') })}>
                {m.answer.isPending ? <Loader2 className="animate-spin" /> : <Check />} Save answer
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Import ───────────────────────────────────────────────

function ImportTab() {
  const { can } = useTenant()
  const ctx = useCtx()
  const [url, setUrl] = useState(ctx.tenant.website ?? '')
  const imp = useImportKnowledge()
  const m = useKnowledgeMutations()
  const [drafts, setDrafts] = useState<{ question: string; answer: string }[]>([])
  const [approvingAll, setApprovingAll] = useState(false)
  if (!can('admin')) return <p className="mt-3 text-sm text-muted-foreground">Ask an admin to import answers.</p>

  const approveAll = async () => {
    setApprovingAll(true)
    let ok = 0
    for (const d of drafts) {
      try { await m.create.mutateAsync({ question: d.question, answer: d.answer, active: true, source: 'website' }); ok++ } catch { /* keep going */ }
    }
    setApprovingAll(false)
    setDrafts([])
    toast.success(`Added ${ok} answer${ok === 1 ? '' : 's'}`)
  }

  return (
    <Card className="mt-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Globe className="size-4" /> Draft answers from your website</CardTitle>
        <CardDescription>We read your homepage and its FAQ, contact and services pages, then draft questions and answers. Nothing is used until you approve it.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (url.trim()) imp.mutate(url.trim(), { onSuccess: (r) => setDrafts(r.drafts) }) }}>
          <Input placeholder="https://your-business.com" value={url} onChange={(e) => setUrl(e.target.value)} />
          <Button type="submit" disabled={!url.trim() || imp.isPending}>
            {imp.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Draft answers
          </Button>
        </form>
        {imp.error && <ErrorBanner error={imp.error} />}
        {imp.isSuccess && drafts.length === 0 && !approvingAll && <p className="text-sm text-muted-foreground">No more drafts. Run it again on another page if you like.</p>}
        {drafts.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">{drafts.length} drafts — edit anything that's off, then approve.</span>
              <Button size="sm" variant="outline" disabled={approvingAll} onClick={approveAll}>
                {approvingAll ? <Loader2 className="animate-spin" /> : <CheckCheck />} Approve all
              </Button>
            </div>
            {drafts.map((d, i) => (
              <div key={i} className="flex gap-3 rounded-lg border p-3">
                <div className="flex-1 space-y-1">
                  <Input value={d.question} onChange={(e) => setDrafts(drafts.map((x, j) => (j === i ? { ...x, question: e.target.value } : x)))} />
                  <Textarea rows={2} value={d.answer} onChange={(e) => setDrafts(drafts.map((x, j) => (j === i ? { ...x, answer: e.target.value } : x)))} />
                </div>
                <div className="flex flex-col gap-1">
                  <Button size="sm" disabled={approvingAll} onClick={() => m.create.mutate({ question: d.question, answer: d.answer, active: true, source: 'website' },
                    { onSuccess: () => { setDrafts((cur) => cur.filter((x) => x !== d)); toast.success('Approved') } })}><Check /> Approve</Button>
                  <Button size="sm" variant="ghost" disabled={approvingAll} onClick={() => setDrafts(drafts.filter((_, j) => j !== i))}><X /> Skip</Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
