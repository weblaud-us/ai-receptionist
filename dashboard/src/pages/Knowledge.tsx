import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { BookOpen, Check, Globe, HelpCircle, Loader2, Pencil, Plus, Trash2, X } from 'lucide-react'
import { EmptyState, ErrorBanner, Field, PageHeader } from '@/components/app/blocks'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { timeAgo } from '@/lib/format'
import { useTenant } from '@/lib/tenant'
import { useImportKnowledge, useKnowledge, useKnowledgeMutations, useUnanswered, useUnansweredMutations } from '@/lib/tenant-hooks'
import type { KnowledgeItem, UnansweredItem } from '@/lib/tenant-types'

export default function Knowledge() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'faqs'
  const open = useUnanswered()
  return (
    <>
      <PageHeader title="Knowledge" description="The answers your assistant gives. Small FAQ lists are read on every call — no delay for the caller." />
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: String(v) })}>
        <TabsList>
          <TabsTrigger value="faqs">Questions & answers</TabsTrigger>
          <TabsTrigger value="unanswered">
            Couldn't answer {!!open.data?.items.length && <Badge variant="secondary" className="ml-1">{open.data.items.length}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="import">Import from website</TabsTrigger>
        </TabsList>
        <TabsContent value="faqs"><FaqTab /></TabsContent>
        <TabsContent value="unanswered"><UnansweredTab /></TabsContent>
        <TabsContent value="import"><ImportTab /></TabsContent>
      </Tabs>
    </>
  )
}

function FaqTab() {
  const { can } = useTenant()
  const q = useKnowledge()
  const m = useKnowledgeMutations()
  const [editing, setEditing] = useState<Partial<KnowledgeItem> | null>(null)
  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {q.data && (q.data.inline
            ? `${q.data.count} answers — all included in every call.`
            : `${q.data.count} answers — the assistant looks them up when asked (large list).`)}
        </p>
        {can('admin') && <Button size="sm" onClick={() => setEditing({ active: true, tags: [] })}><Plus /> Add answer</Button>}
      </div>
      {q.error && <ErrorBanner error={q.error} />}
      {(q.data?.items.length ?? 0) === 0 && !q.isLoading && (
        <Card><EmptyState title="No answers yet" icon={<BookOpen className="size-8" />} description="Add your hours exceptions, parking, payment, policies — anything callers ask." /></Card>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        {q.data?.items.map((item) => (
          <Card key={item.id} className={item.active ? undefined : 'opacity-50'}>
            <CardContent className="flex gap-3">
              <div className="flex-1 space-y-1">
                <div className="font-medium">{item.question}</div>
                <div className="text-sm text-muted-foreground">{item.answer}</div>
              </div>
              {can('admin') && (
                <div className="flex shrink-0 gap-1">
                  <Button size="icon-xs" variant="ghost" aria-label="Edit" onClick={() => setEditing(item)}><Pencil /></Button>
                  <Button size="icon-xs" variant="ghost" aria-label="Delete" onClick={() => m.remove.mutate(item.id)}><Trash2 /></Button>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      {editing && (
        <AnswerDialog value={editing} saving={m.create.isPending || m.update.isPending} onClose={() => setEditing(null)}
          onSave={(body) => {
            const done = { onSuccess: () => { toast.success('Saved'); setEditing(null) } }
            if (editing.id) m.update.mutate({ id: editing.id, ...body }, done)
            else m.create.mutate(body, done)
          }} />
      )}
    </div>
  )
}

function AnswerDialog({ value, saving, onClose, onSave }: {
  value: Partial<KnowledgeItem>
  saving: boolean
  onClose: () => void
  onSave: (body: { question: string; answer: string; active: boolean }) => void
}) {
  const [question, setQuestion] = useState(value.question ?? '')
  const [answer, setAnswer] = useState(value.answer ?? '')
  const [active, setActive] = useState(value.active ?? true)
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{value.id ? 'Edit answer' : 'New answer'}</DialogTitle></DialogHeader>
        <Field label="When a caller asks…"><Input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Is there parking?" /></Field>
        <Field label="…the assistant says" hint="One or two plain sentences — it will be spoken aloud.">
          <Textarea rows={3} value={answer} onChange={(e) => setAnswer(e.target.value)} />
        </Field>
        <label className="flex items-center gap-2 text-sm"><Switch checked={active} onCheckedChange={setActive} /> Active</label>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={question.trim().length < 2 || !answer.trim() || saving} onClick={() => onSave({ question: question.trim(), answer: answer.trim(), active })}>
            {saving && <Loader2 className="animate-spin" />} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function UnansweredTab() {
  const { can } = useTenant()
  const q = useUnanswered()
  return (
    <div className="mt-3 space-y-3">
      <p className="text-sm text-muted-foreground">Questions your assistant couldn't answer. Add an answer once and it knows it from the next call.</p>
      {(q.data?.items.length ?? 0) === 0 && !q.isLoading && (
        <Card><EmptyState title="Nothing unanswered" icon={<HelpCircle className="size-8" />} description="Nice — your assistant had an answer for everything." /></Card>
      )}
      {q.data?.items.map((item) => <UnansweredRow key={item.id} item={item} canEdit={can('admin')} />)}
    </div>
  )
}

function UnansweredRow({ item, canEdit }: { item: UnansweredItem; canEdit: boolean }) {
  const m = useUnansweredMutations()
  const [answer, setAnswer] = useState('')
  return (
    <Card>
      <CardContent className="space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="font-medium">“{item.question}”</div>
          <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(item.created_at)} · {item.channel}</span>
        </div>
        {canEdit && (
          <div className="flex gap-2">
            <Input placeholder="Type the answer…" value={answer} onChange={(e) => setAnswer(e.target.value)} />
            <Button size="sm" disabled={!answer.trim() || m.answer.isPending}
              onClick={() => m.answer.mutate({ id: item.id, answer: answer.trim() }, { onSuccess: () => toast.success('Added to knowledge') })}>
              <Check /> Add answer
            </Button>
            <Button size="sm" variant="ghost" onClick={() => m.dismiss.mutate(item.id)}><X /> Dismiss</Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ImportTab() {
  const { can } = useTenant()
  const [url, setUrl] = useState('')
  const imp = useImportKnowledge()
  const m = useKnowledgeMutations()
  const [drafts, setDrafts] = useState<{ question: string; answer: string }[]>([])
  if (!can('admin')) return <p className="mt-3 text-sm text-muted-foreground">Ask an admin to import answers.</p>
  return (
    <Card className="mt-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Globe className="size-4" /> Draft answers from your website</CardTitle>
        <CardDescription>We read the page and draft questions and answers. Nothing is used until you approve it.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <Input placeholder="https://your-business.com" value={url} onChange={(e) => setUrl(e.target.value)} />
          <Button disabled={!url.trim() || imp.isPending} onClick={() => imp.mutate(url.trim(), { onSuccess: (r) => setDrafts(r.drafts) })}>
            {imp.isPending && <Loader2 className="animate-spin" />} Draft answers
          </Button>
        </div>
        {drafts.length > 0 && (
          <div className="space-y-2">
            {drafts.map((d, i) => (
              <div key={i} className="flex gap-3 rounded-lg border p-3">
                <div className="flex-1 space-y-1">
                  <Input value={d.question} onChange={(e) => setDrafts(drafts.map((x, j) => (j === i ? { ...x, question: e.target.value } : x)))} />
                  <Textarea rows={2} value={d.answer} onChange={(e) => setDrafts(drafts.map((x, j) => (j === i ? { ...x, answer: e.target.value } : x)))} />
                </div>
                <div className="flex flex-col gap-1">
                  <Button size="sm" onClick={() => m.create.mutate({ question: d.question, answer: d.answer, active: true },
                    { onSuccess: () => { setDrafts(drafts.filter((_, j) => j !== i)); toast.success('Approved') } })}><Check /> Approve</Button>
                  <Button size="sm" variant="ghost" onClick={() => setDrafts(drafts.filter((_, j) => j !== i))}><X /> Skip</Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
