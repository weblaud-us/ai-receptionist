/** Help → Guides: the list of articles, and one article. Each app passes its own registries. */
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, BookOpen, ChevronRight } from 'lucide-react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { forAudience, type HelpAudience, type HelpEntry, type HelpRegistry } from '../lib/help'
import { EmptyState, ErrorBanner, PageHeader } from './blocks'
import { Markdown } from './markdown'
import { Card, CardContent } from './ui/card'
import { Skeleton } from './ui/skeleton'

export interface HelpSection {
  /** Shown above the section when there's more than one (e.g. "What practice owners see"). */
  title?: string
  registry: HelpRegistry
}

function Row({ e }: { e: HelpEntry }) {
  return (
    <Link to={`/help/${e.slug}`}
      className="flex items-center gap-3 rounded-lg px-3 py-2.5 outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring">
      <BookOpen className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{e.title}</div>
        <div className="truncate text-xs text-muted-foreground">{e.summary}</div>
      </div>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
    </Link>
  )
}

function List({ title, entries }: { title: string; entries: HelpEntry[] }) {
  if (!entries.length) return null
  return (
    <section aria-label={title} className="space-y-1.5">
      <h2 className="px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</h2>
      <Card className="py-1.5"><CardContent className="px-1.5">{entries.map((e) => <Row key={e.slug} e={e} />)}</CardContent></Card>
    </section>
  )
}

export function HelpIndexPage({ sections, audience, description = 'Short guides for every part of the dashboard.' }: {
  sections: HelpSection[]
  /** Articles for these readers are listed first, as "For you". */
  audience: HelpAudience[]
  description?: string
}) {
  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Guides" description={description} />
      <div className="space-y-6">
        {sections.map((s, i) => {
          const { mine, rest } = forAudience(s.registry.entries, audience)
          const prefix = s.title ? `${s.title}: ` : ''
          return (
            <div key={i} className="space-y-4">
              {sections.length > 1 && s.title && <h2 className="text-base font-semibold">{s.title}</h2>}
              <List title={`${prefix}For you`} entries={mine} />
              <List title={mine.length ? `${prefix}Everything else` : `${prefix}All guides`} entries={rest} />
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function HelpArticlePage({ sections }: { sections: HelpSection[] }) {
  const { slug = '' } = useParams()
  const entry = sections.map((s) => s.registry.get(slug)).find(Boolean)
  const body = useQuery({ queryKey: ['help', slug], queryFn: () => entry!.load(), enabled: !!entry, staleTime: Infinity })
  const { hash } = useLocation()
  useEffect(() => {
    // A link to a heading ("call-forwarding.md#test-it"): scroll there once the article is in.
    if (body.data && hash) document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView()
  }, [body.data, hash])
  const back = (
    <Link to="/help" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-4" /> All guides
    </Link>
  )
  if (!entry) {
    return (
      <div className="mx-auto max-w-3xl">
        {back}
        <EmptyState title="We couldn't find that guide" description="It may have moved. Every guide is listed on the Guides page." />
      </div>
    )
  }
  return (
    <article className="mx-auto max-w-3xl">
      {back}
      <PageHeader title={entry.title} description={entry.summary} />
      {body.error && <ErrorBanner error={body.error} onRetry={() => void body.refetch()} />}
      {body.isLoading && <div className="space-y-2"><Skeleton className="h-4 w-3/4" /><Skeleton className="h-4 w-2/3" /><Skeleton className="h-4 w-1/2" /></div>}
      {body.data && <Card><CardContent><Markdown source={body.data} hideTitle /></CardContent></Card>}
    </article>
  )
}
