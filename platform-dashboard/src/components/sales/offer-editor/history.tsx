import { useMemo, useState } from 'react'
import { History, Loader2, RotateCcw } from 'lucide-react'
import { ConfirmDialog } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Badge } from '@receptionist/shared/components/ui/badge'
import { Button } from '@receptionist/shared/components/ui/button'
import { dateTime } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { diffOffers, type DiffRow } from '@/lib/offer-diff'
import { useOfferVersion, type OfferSettings } from '@/lib/sales'
import { DiffText } from './assist'
import { tabOf, type EditorTab } from './readiness'

const TAB_LABEL: Record<EditorTab, string> = {
  offer: 'What you sell', conversation: 'Conversation', scripts: 'Scripts', 'follow-up': 'Meeting & emails',
  consent: 'Consent & voicemail', prompt: 'Prompt', tests: 'Test calls', learning: 'Learning', history: 'History',
}
const KIND_STYLE: Record<DiffRow['kind'], string> = {
  added: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300',
  removed: 'bg-destructive/10 text-destructive',
  changed: 'bg-sky-500/15 text-sky-800 dark:text-sky-300',
}

export interface VersionRow {
  version: number
  published_at: string
  published_by: string
  published_by_name?: string
}

/**
 * Every published version: who published it and when, which campaigns use it, what changed, and
 * "restore into draft" (SALES_SUITE_PLAN.md Phase 7). Published versions themselves never change.
 */
export function HistoryTab({ id, versions, campaigns, draft, restoring, onRestore, canRestore }: {
  id: string
  versions: VersionRow[]
  campaigns: { id: string; name: string; status: string; offer_version: number }[]
  draft: OfferSettings
  restoring: boolean
  onRestore: (version: number) => void
  canRestore: boolean
}) {
  const latest = versions[0]?.version ?? null
  const [left, setLeft] = useState<string>(latest ? String(latest) : '')
  const [right, setRight] = useState<string>('draft')
  const [confirm, setConfirm] = useState<number | null>(null)
  const a = useOfferVersion(id, left && left !== 'draft' ? Number(left) : null)
  const b = useOfferVersion(id, right && right !== 'draft' ? Number(right) : null)
  const before = left === 'draft' ? draft : a.data?.version.settings
  const after = right === 'draft' ? draft : b.data?.version.settings
  const rows = useMemo(() => (before && after ? diffOffers(before, after) : []), [before, after])
  const grouped = useMemo(() => {
    const g = new Map<EditorTab, DiffRow[]>()
    for (const r of rows) {
      const t = tabOf(r.path)
      g.set(t, [...(g.get(t) ?? []), r])
    }
    return [...g.entries()]
  }, [rows])
  const options = [{ value: 'draft', label: 'Current draft' }, ...versions.map((v) => ({ value: String(v.version), label: `v${v.version}` }))]
  const loading = a.isFetching || b.isFetching

  if (versions.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing published yet. Each time you publish, the version is kept here for good, so every call can be traced to the exact words it used.</p>
  }
  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
            <tr><th className="px-3 py-2">Version</th><th className="px-3 py-2">Published</th><th className="px-3 py-2">Campaigns on it</th><th /></tr>
          </thead>
          <tbody>
            {versions.map((v) => {
              const pinned = campaigns.filter((c) => c.offer_version === v.version)
              return (
                <tr key={v.version} className="border-t">
                  <td className="px-3 py-2 font-medium">v{v.version}{v.version === latest && <Badge variant="secondary" className="ml-2">latest</Badge>}</td>
                  <td className="px-3 py-2 text-muted-foreground">{dateTime(v.published_at)} · {v.published_by_name || v.published_by}</td>
                  <td className="px-3 py-2 text-muted-foreground">{pinned.length ? pinned.map((c) => `${c.name} (${c.status})`).join(', ') : '—'}</td>
                  <td className="space-x-1 px-3 py-2 text-right whitespace-nowrap">
                    <Button size="xs" variant="ghost" onClick={() => { setLeft(String(v.version)); setRight('draft') }}><History /> Compare with draft</Button>
                    {canRestore && <Button size="xs" variant="ghost" onClick={() => setConfirm(v.version)}><RotateCcw /> Restore</Button>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        Compare <SimpleSelect size="sm" value={left} onChange={setLeft} options={options} /> with
        <SimpleSelect size="sm" value={right} onChange={setRight} options={options} />
        {loading && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
        <span className="text-muted-foreground">{before && after ? `${rows.length} change${rows.length === 1 ? '' : 's'}` : ''}</span>
      </div>
      {before && after && rows.length === 0 && <p className="text-sm text-muted-foreground">No differences.</p>}
      {grouped.map(([tab, items]) => (
        <section key={tab} className="space-y-2">
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{TAB_LABEL[tab]}</h4>
          <ul className="space-y-1.5">
            {items.map((r) => (
              <li key={r.path} className="rounded-md border p-2 text-sm">
                <div className="mb-1 flex items-center gap-2">
                  <Badge variant="secondary" className={cn('border-0', KIND_STYLE[r.kind])}>{r.kind}</Badge>
                  <span className="font-medium">{r.label}</span>
                </div>
                <DiffText before={r.before} after={r.after} />
              </li>
            ))}
          </ul>
        </section>
      ))}

      <ConfirmDialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)} title={`Restore v${confirm} into the draft?`}
        confirmLabel={`Restore v${confirm}`} loading={restoring}
        onConfirm={() => { if (confirm !== null) onRestore(confirm); setConfirm(null) }}
        description="The draft is replaced with that version’s words. Published versions and the campaigns using them don’t change; publish again to make it the newest version." />
    </div>
  )
}
