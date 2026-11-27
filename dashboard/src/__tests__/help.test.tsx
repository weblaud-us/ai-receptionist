// Help articles (packages/shared/src/help): every one renders cleanly in-app, links resolve, and the
// Markdown reader never turns article text into something that can run.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HelpArticlePage, HelpIndexPage } from '@receptionist/shared/components/help-pages'
import { Markdown } from '@receptionist/shared/components/markdown'
import { PUBLIC_HELP_ROWS, publicHelp } from '@receptionist/shared/lib/help'
import { articleTitle, linkedSlugs, parseMarkdown } from '@receptionist/shared/lib/markdown'

vi.mock('@receptionist/shared/auth/auth', () => ({
  useAuth: () => ({ status: 'authenticated', tenantId: 'ten_1', user: { id: 'usr_1', is_platform_admin: false } }),
}))

afterEach(cleanup)

const files = import.meta.glob<string>('../../../packages/shared/src/help/*.md', { query: '?raw', import: 'default', eager: true })
const articles = Object.entries(files)
  .map(([path, body]) => ({ slug: /([^/]+)\.md$/.exec(path)![1], body }))
  .filter((a) => a.slug !== 'README')

function inRouter(node: React.ReactNode, path = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('help articles', () => {
  it('every file is in the manifest, and every manifest row has a file', () => {
    expect(articles.map((a) => a.slug).sort()).toEqual(PUBLIC_HELP_ROWS.map((r) => r.slug).sort())
    expect(publicHelp.entries).toHaveLength(PUBLIC_HELP_ROWS.length)
  })

  it.each(articles)('$slug: title matches the manifest, links resolve, plain Markdown only', ({ slug, body }) => {
    const blocks = parseMarkdown(body)
    expect(articleTitle(blocks)).toBe(publicHelp.get(slug)!.title)
    for (const target of linkedSlugs(blocks)) expect(publicHelp.get(target), `${slug} links to ${target}`).toBeTruthy()
    expect(body.startsWith('---')).toBe(false)               // no front-matter: the help centre publishes these
    expect(/<\/?[a-z][^>]*>/i.test(body)).toBe(false)         // no raw HTML
  })

  it.each(articles)('$slug renders with no Markdown left showing', ({ body }) => {
    const { container } = inRouter(<Markdown source={body} />)
    container.querySelectorAll('code').forEach((c) => c.remove())     // dialling codes really are **…#
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/\*\*|\]\(|\|---/)
  })
})

describe('markdown reader', () => {
  it('reads tables, quotes, lists that run onto the next line, code and links', () => {
    const { container } = inRouter(<Markdown source={[
      '# Title', '', '> A quote', '> over two lines', '',
      '1. **First** step that', '   carries on', '2. Second with `**004*NUMBER#`', '',
      '| What | Dial |', '|---|---|', '| Busy | `**67*N#` |', '',
      'See [calendar](calendar.md#good) and [us](https://example.com).',
    ].join('\n')} />)
    expect(container.querySelector('blockquote')?.textContent).toBe('A quote over two lines')
    expect(container.querySelectorAll('ol li')).toHaveLength(2)
    expect(container.querySelector('ol li')?.textContent).toBe('First step that carries on')
    expect(container.querySelector('td code')?.textContent).toBe('**67*N#')
    expect(container.querySelector('a[href="/help/calendar#good"]')).toBeTruthy()
    expect(container.querySelector('a[href="https://example.com"]')?.getAttribute('rel')).toContain('noopener')
  })

  it('never makes a link that runs script, and never renders HTML', () => {
    const { container } = inRouter(<Markdown source={'[click](javascript:alert(1)) and <img src=x onerror=alert(1)>'} />)
    expect(container.querySelector('a')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('click')
  })
})

describe('help pages', () => {
  it('lists the guides for this role first', () => {
    inRouter(<HelpIndexPage sections={[{ registry: publicHelp }]} audience={['dentist']} />)
    const sections = screen.getAllByRole('region')
    expect(sections[0].getAttribute('aria-label')).toBe('For you')
    expect(sections[0].textContent).toContain('Your first day: dentists')
    expect(sections[0].textContent).not.toContain('Forward your calls')
  })

  it('opens an article, and says so when one is missing', async () => {
    inRouter(<Routes><Route path="/help/:slug" element={<HelpArticlePage sections={[{ registry: publicHelp }]} />} /></Routes>, '/help/inbox')
    expect(await screen.findByText(/Clear it once or twice a day/, {}, { timeout: 4000 })).toBeTruthy()
    cleanup()
    inRouter(<Routes><Route path="/help/:slug" element={<HelpArticlePage sections={[{ registry: publicHelp }]} />} /></Routes>, '/help/nope')
    expect(screen.getByText("We couldn't find that guide")).toBeTruthy()
  })
})
