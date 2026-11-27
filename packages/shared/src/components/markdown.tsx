/** Renders a help article (lib/markdown.ts) as React elements: never as raw HTML. */
import { Fragment, useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { parseMarkdown, resolveLink, type Block, type Inline } from '../lib/markdown'
import { cn } from '../lib/utils'

/** Readable article styles; the platform's legal preview uses the same. */
// eslint-disable-next-line react-refresh/only-export-components
export const PROSE = cn(
  'max-w-none text-sm leading-relaxed text-foreground',
  '[&_h1]:mb-3 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-6 [&_h2]:text-base [&_h2]:font-semibold',
  '[&_h3]:mb-1.5 [&_h3]:mt-4 [&_h3]:font-semibold [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5',
  '[&_li]:my-0.5 [&_a]:text-primary [&_a]:underline [&_strong]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
  '[&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs [&_td]:border [&_td]:p-1.5 [&_th]:border [&_th]:bg-muted/50 [&_th]:p-1.5 [&_th]:text-left',
  '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1',
)

function inline(nodes: Inline[], articlePath: (slug: string) => string): ReactNode {
  return nodes.map((n, i) => {
    switch (n.t) {
      case 'text':
        return <Fragment key={i}>{n.v}</Fragment>
      case 'code':
        return <code key={i}>{n.v}</code>
      case 'strong':
        return <strong key={i}>{inline(n.c, articlePath)}</strong>
      case 'em':
        return <em key={i}>{inline(n.c, articlePath)}</em>
      case 'link': {
        const target = resolveLink(n.href)
        const children = inline(n.c, articlePath)
        if (target.kind === 'article') {
          return <Link key={i} to={articlePath(target.slug) + (target.hash ? `#${target.hash}` : '')}>{children}</Link>
        }
        if (target.kind === 'external') {
          const mail = target.href.toLowerCase().startsWith('mailto:')
          return <a key={i} href={target.href} {...(mail ? {} : { target: '_blank', rel: 'noopener noreferrer' })}>{children}</a>
        }
        return <Fragment key={i}>{children}</Fragment>
      }
    }
  })
}

function block(b: Block, i: number, articlePath: (slug: string) => string): ReactNode {
  switch (b.t) {
    case 'h': {
      const H = (`h${b.level}`) as 'h1' | 'h2' | 'h3'
      return <H key={i} id={b.level > 1 ? b.id : undefined}>{inline(b.c, articlePath)}</H>
    }
    case 'p':
      return <p key={i}>{inline(b.c, articlePath)}</p>
    case 'quote':
      return <blockquote key={i}>{inline(b.c, articlePath)}</blockquote>
    case 'ul':
    case 'ol': {
      const L = b.t
      return <L key={i}>{b.items.map((it, j) => <li key={j}>{inline(it, articlePath)}</li>)}</L>
    }
    case 'table':
      return (
        <div key={i} className="overflow-x-auto">
          <table>
            <thead><tr>{b.head.map((c, j) => <th key={j}>{inline(c, articlePath)}</th>)}</tr></thead>
            <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}>{inline(c, articlePath)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )
  }
}

export function Markdown({ source, hideTitle = false, articlePath = (s) => `/help/${s}`, className }: {
  source: string
  /** Leave out the article's own "# Title" (the page shows it in its header). */
  hideTitle?: boolean
  /** Where a link to another article goes. */
  articlePath?: (slug: string) => string
  className?: string
}) {
  const blocks = useMemo(() => parseMarkdown(source), [source])
  const shown = hideTitle && blocks[0]?.t === 'h' && blocks[0].level === 1 ? blocks.slice(1) : blocks
  return <div className={cn(PROSE, className)}>{shown.map((b, i) => block(b, i, articlePath))}</div>
}
