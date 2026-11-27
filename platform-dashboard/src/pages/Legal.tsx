import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, ExternalLink, FileText, History, Loader2, Save, Send } from 'lucide-react'
import { ConfirmDialog, ErrorBanner, Field, PageHeader, TableSkeleton } from '@receptionist/shared/components/blocks'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Checkbox } from '@receptionist/shared/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { PROSE } from '@receptionist/shared/components/markdown'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { dateTime, timeAgo } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { useLegalDoc, useLegalDocs, useLegalMutations, useLegalPreview, useLegalVersion, type LegalDocSummary } from '@/lib/legal'

function Status({ doc }: { doc: LegalDocSummary }) {
  if (!doc.published_version) return <Badge variant="secondary" className="border-0 bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300">Not published</Badge>
  if (doc.unpublished_changes) return <Badge variant="secondary" className="border-0 bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300">Unpublished changes</Badge>
  return <Badge variant="secondary" className="border-0 bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300">Published</Badge>
}

/** Terms, Privacy, Subscription and the rest: edit the text, preview it, publish it to the website. */
export default function Legal() {
  const docs = useLegalDocs()
  const [selected, setSelected] = useState<string | null>(null)
  const list = docs.data?.documents ?? []
  const key = selected ?? list[0]?.key ?? null

  return (
    <>
      <PageHeader
        title="Legal documents"
        help="legal-documents"
        description={<>The Terms, Privacy Policy, Subscription policy and the rest, as the website shows them at <code className="rounded bg-muted px-1">/legal/…</code>. Write in Markdown; text in [square brackets] must be filled in before a document can be published.</>}
      />
      {docs.isError && <ErrorBanner error={docs.error} onRetry={() => docs.refetch()} />}
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Card className="h-fit">
          <CardContent className="p-2">
            {docs.isPending ? <TableSkeleton rows={6} cols={1} /> : (
              <ul className="space-y-1">
                {list.map((d) => (
                  <li key={d.key}>
                    <button type="button" onClick={() => setSelected(d.key)}
                      className={cn('w-full rounded-md px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted',
                        d.key === key && 'bg-muted')}>
                      <span className="flex items-center gap-2 font-medium"><FileText className="size-4 shrink-0 text-muted-foreground" />{d.title}</span>
                      <span className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        <Status doc={d} />
                        {d.published_version && <span>v{d.published_version}</span>}
                        {d.placeholders > 0 && <span className="text-amber-700 dark:text-amber-400">{d.placeholders} to fill in</span>}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        {key && <Editor key={key} docKey={key} />}
      </div>
    </>
  )
}

function Editor({ docKey }: { docKey: string }) {
  const doc = useLegalDoc(docKey)
  const m = useLegalMutations(docKey)
  const [title, setTitle] = useState('')
  const [md, setMd] = useState('')
  const [loadedAt, setLoadedAt] = useState<string | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [material, setMaterial] = useState(true)
  const [summary, setSummary] = useState('')
  const [viewing, setViewing] = useState<string | null>(null)

  // Load the saved draft once (and again after a save), never over what is being typed.
  // (Adjusting state while rendering, React's pattern for resetting state from new data.)
  const stamp = doc.data ? doc.data.draft_updated_at ?? 'none' : null
  if (doc.data && stamp !== loadedAt) {
    setTitle(doc.data.title)
    setMd(doc.data.draft_md)
    setLoadedAt(stamp)
  }

  const debounced = useDebounced(md, 500)
  const preview = useLegalPreview(debounced, !!doc.data)
  const dirty = !!doc.data && (md !== doc.data.draft_md || title !== doc.data.title)
  const left = preview.data?.placeholders ?? doc.data?.draft_placeholders ?? []
  const firstPublish = !doc.data?.published_version
  const words = useMemo(() => (md.trim() ? md.trim().split(/\s+/).length : 0), [md])

  if (doc.isPending) return <Card><CardContent className="p-6"><TableSkeleton rows={8} cols={1} /></CardContent></Card>
  if (doc.isError) return <ErrorBanner error={doc.error} onRetry={() => doc.refetch()} />
  const d = doc.data

  const save = () =>
    m.save.mutateAsync({ title, markdown: md }).then(() => toast.success('Draft saved'))

  const publish = async () => {
    try {
      if (dirty) await m.save.mutateAsync({ title, markdown: md })
      const r = await m.publish.mutateAsync({ material, summary })
      toast.success(r.material && r.previous_version
        ? `Published as version ${r.version}. Owners will be asked to accept it.`
        : `Published (version ${r.version})`)
      setPublishing(false)
      setSummary('')
    } catch {
      /* the mutation's error is shown in the dialog */
    }
  }

  return (
    <Card className="min-w-0">
      <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2">{d.title} <Status doc={d} /></CardTitle>
          <CardDescription className="mt-1">
            {d.published_version
              ? <>Version {d.published_version}, published {timeAgo(d.published_at)}.</>
              : 'Not on the website yet.'}
            {d.draft_updated_at && <> Draft saved {timeAgo(d.draft_updated_at)}.</>}
          </CardDescription>
        </div>
        <div className="flex flex-wrap gap-2">
          {d.url && d.published_version && (
            <Button size="sm" variant="ghost" nativeButton={false} render={<a href={d.url} target="_blank" rel="noopener noreferrer" />}>
              <ExternalLink /> View on site
            </Button>
          )}
          <Button size="sm" variant="outline" disabled={!dirty || m.save.isPending} onClick={() => void save()}>
            {m.save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save draft
          </Button>
          <Button size="sm" disabled={!md.trim() || left.length > 0 || (!dirty && !d.unpublished_changes && !firstPublish)}
            onClick={() => { setMaterial(!firstPublish); setPublishing(true) }}>
            <Send /> Publish…
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {m.save.isError && <ErrorBanner error={m.save.error} />}
        {left.length > 0 && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
            <p className="flex items-center gap-2 font-medium text-amber-800 dark:text-amber-300">
              <AlertTriangle className="size-4" /> {left.length} thing{left.length === 1 ? '' : 's'} to fill in before this can be published
            </p>
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {left.slice(0, 30).map((p) => <li key={p} className="rounded bg-background px-1.5 py-0.5 font-mono text-xs">{p}</li>)}
            </ul>
          </div>
        )}
        <Field label="Title"><Input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} /></Field>
        <div className="grid gap-4 xl:grid-cols-2">
          <div className="min-w-0 space-y-1.5">
            <div className="flex items-center justify-between text-sm"><span className="font-medium">Text (Markdown)</span>
              <span className="text-xs text-muted-foreground tabular-nums">{words.toLocaleString()} words</span></div>
            <Textarea value={md} onChange={(e) => setMd(e.target.value)} spellCheck
              className="min-h-[560px] font-mono text-xs leading-relaxed" />
            <p className="text-xs text-muted-foreground">## Heading · **bold** · - list · [link text](/legal/privacy) · | tables |</p>
          </div>
          <div className="min-w-0 space-y-1.5">
            <div className="flex items-center gap-2 text-sm font-medium">Preview {preview.isFetching && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}</div>
            <div className="max-h-[600px] min-h-[560px] overflow-y-auto rounded-md border bg-background p-4">
              <h1 className="mb-3 text-xl font-semibold">{title}</h1>
              {/* Rendered by the API with raw HTML escaped (core/compliance/legal_docs.render). */}
              <div className={PROSE} dangerouslySetInnerHTML={{ __html: preview.data?.html ?? '' }} />
            </div>
          </div>
        </div>

        {d.history.length > 0 && (
          <div>
            <p className="mb-2 flex items-center gap-2 text-sm font-medium"><History className="size-4" /> Published versions</p>
            <ul className="divide-y rounded-md border text-sm">
              {d.history.map((h) => (
                <li key={`${h.version}-${h.revision}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                  <button type="button" className="font-mono text-xs text-primary underline-offset-2 hover:underline" onClick={() => setViewing(h.version)}>
                    v{h.version}{h.revision > 1 ? ` (edit ${h.revision})` : ''}
                  </button>
                  {h.material && <Badge variant="outline" className="text-[10px]">re-acceptance</Badge>}
                  <span className="text-muted-foreground">{dateTime(h.published_at)}</span>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">{h.summary}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={publishing}
        onOpenChange={(o) => { if (!o) { setPublishing(false); m.publish.reset() } }}
        title={`Publish ${title}?`}
        description="It goes live on the website straight away."
        confirmLabel="Publish"
        loading={m.save.isPending || m.publish.isPending}
        onConfirm={() => void publish()}
      >
        <div className="space-y-3">
          {firstPublish ? (
            <p className="text-sm text-muted-foreground">This is the first published version. Signups from now on accept it.</p>
          ) : (
            <label className="flex items-start gap-2 text-sm">
              <Checkbox checked={material} onCheckedChange={(v) => setMaterial(v === true)} className="mt-0.5" />
              <span>
                <span className="font-medium">A material change</span>: give it a new version, and ask owners who accepted the old one to
                review and accept again. Leave unticked for a typo or wording fix.
              </span>
            </label>
          )}
          <Field label="What changed (kept with the version)">
            <Textarea value={summary} maxLength={500} rows={2} onChange={(e) => setSummary(e.target.value)} placeholder="e.g. Added the cookie policy link" />
          </Field>
          {m.publish.isError && <ErrorBanner error={m.publish.error} />}
        </div>
      </ConfirmDialog>

      {viewing && <VersionDialog docKey={docKey} version={viewing} onClose={() => setViewing(null)} />}
    </Card>
  )
}

function VersionDialog({ docKey, version, onClose }: { docKey: string; version: string; onClose: () => void }) {
  const v = useLegalVersion(docKey, version)
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Version {version}</DialogTitle>
          <DialogDescription>{v.data ? `Published ${dateTime(v.data.published_at)}${v.data.summary ? ` · ${v.data.summary}` : ''}` : 'Loading…'}</DialogDescription>
        </DialogHeader>
        {v.isError && <ErrorBanner error={v.error} />}
        {v.data && <div className={PROSE} dangerouslySetInnerHTML={{ __html: v.data.html }} />}
      </DialogContent>
    </Dialog>
  )
}
