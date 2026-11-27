/**
 * A small Markdown reader for the help articles: just what they use, and nothing that can run code.
 *
 * Blocks: # / ## / ### headings, paragraphs, - and 1. lists (lines indented two or more spaces carry
 * on the item above), > quotes and | tables |. Inline: `code`, **bold**, *italic* and [links](href).
 * The output is a tree the React renderer (components/markdown.tsx) turns into elements, so no HTML
 * from an article ever reaches the page as HTML.
 */

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'code'; v: string }
  | { t: 'strong'; c: Inline[] }
  | { t: 'em'; c: Inline[] }
  | { t: 'link'; href: string; c: Inline[] }

export type Block =
  | { t: 'h'; level: 1 | 2 | 3; id: string; c: Inline[] }
  | { t: 'p'; c: Inline[] }
  | { t: 'ul' | 'ol'; items: Inline[][] }
  | { t: 'quote'; c: Inline[] }
  | { t: 'table'; head: Inline[][]; rows: Inline[][][] }

const HEADING = /^(#{1,3})\s+(.*)$/
const BULLET = /^[-*]\s+(.*)$/
const NUMBERED = /^\d+[.)]\s+(.*)$/
const QUOTE = /^>\s?(.*)$/
const TABLE_SEP = /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/

/** "Mobile phones (most carriers)" → "mobile-phones-most-carriers": the anchor for a heading. */
export function slugify(text: string): string {
  return text.toLowerCase().replace(/[`*_[\]()]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

function cells(line: string): string[] {
  const s = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  // A pipe inside `code` is not a column break.
  const out: string[] = []
  let cur = ''
  let inCode = false
  for (const ch of s) {
    if (ch === '`') inCode = !inCode
    if (ch === '|' && !inCode) {
      out.push(cur.trim())
      cur = ''
    } else cur += ch
  }
  out.push(cur.trim())
  return out
}

function plain(nodes: Inline[]): string {
  return nodes.map((n) => (n.t === 'text' || n.t === 'code' ? n.v : plain(n.c))).join('')
}

export function parseInline(src: string): Inline[] {
  const out: Inline[] = []
  let text = ''
  const flush = () => {
    if (text) out.push({ t: 'text', v: text })
    text = ''
  }
  let i = 0
  while (i < src.length) {
    const ch = src[i]
    if (ch === '`') {
      const end = src.indexOf('`', i + 1)
      if (end > i) {
        flush()
        out.push({ t: 'code', v: src.slice(i + 1, end) })
        i = end + 1
        continue
      }
    }
    if (ch === '*' && src[i + 1] === '*') {
      const end = src.indexOf('**', i + 2)
      if (end > i + 2) {
        flush()
        out.push({ t: 'strong', c: parseInline(src.slice(i + 2, end)) })
        i = end + 2
        continue
      }
    }
    if (ch === '*' && src[i + 1] !== ' ' && src[i + 1] !== '*') {
      const end = src.indexOf('*', i + 1)
      if (end > i + 1 && src[end - 1] !== ' ') {
        flush()
        out.push({ t: 'em', c: parseInline(src.slice(i + 1, end)) })
        i = end + 1
        continue
      }
    }
    if (ch === '[') {
      const close = src.indexOf('](', i + 1)
      const paren = close > i ? src.indexOf(')', close + 2) : -1
      if (close > i && paren > close) {
        flush()
        out.push({ t: 'link', href: src.slice(close + 2, paren).trim(), c: parseInline(src.slice(i + 1, close)) })
        i = paren + 1
        continue
      }
    }
    text += ch
    i += 1
  }
  flush()
  return out
}

export function parseMarkdown(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()
    if (!trimmed) {
      i += 1
      continue
    }

    const h = HEADING.exec(trimmed)
    if (h) {
      const c = parseInline(h[2].trim())
      blocks.push({ t: 'h', level: h[1].length as 1 | 2 | 3, id: slugify(plain(c)), c })
      i += 1
      continue
    }

    if (trimmed.startsWith('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1].trim())) {
      const head = cells(trimmed).map(parseInline)
      const rows: Inline[][][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(cells(lines[i]).map(parseInline))
        i += 1
      }
      blocks.push({ t: 'table', head, rows })
      continue
    }

    if (QUOTE.test(trimmed)) {
      const parts: string[] = []
      while (i < lines.length && QUOTE.test(lines[i].trim())) {
        parts.push(QUOTE.exec(lines[i].trim())![1])
        i += 1
      }
      blocks.push({ t: 'quote', c: parseInline(parts.join(' ').trim()) })
      continue
    }

    const kind = BULLET.test(trimmed) ? 'ul' : NUMBERED.test(trimmed) ? 'ol' : null
    if (kind) {
      const marker = kind === 'ul' ? BULLET : NUMBERED
      const items: string[] = []
      while (i < lines.length) {
        const l = lines[i]
        const m = marker.exec(l.trim())
        if (m && !/^\s{2,}/.test(l)) {
          items.push(m[1])
        } else if (items.length && /^\s{2,}\S/.test(l)) {
          items[items.length - 1] += ' ' + l.trim()
        } else break
        i += 1
      }
      blocks.push({ t: kind, items: items.map((s) => parseInline(s.trim())) })
      continue
    }

    const para: string[] = []
    while (i < lines.length) {
      const l = lines[i].trim()
      if (!l || HEADING.test(l) || QUOTE.test(l) || BULLET.test(l) || NUMBERED.test(l)) break
      if (l.startsWith('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1].trim())) break
      para.push(l)
      i += 1
    }
    blocks.push({ t: 'p', c: parseInline(para.join(' ')) })
  }
  return blocks
}

/** What a link in an article points at: another article, a web or mail address, or nothing safe. */
export type LinkTarget = { kind: 'article'; slug: string; hash?: string } | { kind: 'external'; href: string } | { kind: 'none' }

export function resolveLink(href: string): LinkTarget {
  const article = /^(?:\.\/)?([a-z0-9-]+)\.md(?:#([a-z0-9-]+))?$/i.exec(href)
  if (article) return { kind: 'article', slug: article[1].toLowerCase(), hash: article[2] }
  if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) return { kind: 'external', href }
  return { kind: 'none' }
}

/** Every article slug a parsed article links to (tests check each one exists). */
export function linkedSlugs(blocks: Block[]): string[] {
  const found: string[] = []
  const walk = (nodes: Inline[]) => {
    for (const n of nodes) {
      if (n.t === 'link') {
        const target = resolveLink(n.href)
        if (target.kind === 'article') found.push(target.slug)
      }
      if (n.t === 'strong' || n.t === 'em' || n.t === 'link') walk(n.c)
    }
  }
  for (const b of blocks) {
    if (b.t === 'h' || b.t === 'p' || b.t === 'quote') walk(b.c)
    else if (b.t === 'ul' || b.t === 'ol') b.items.forEach(walk)
    else if (b.t === 'table') [b.head, ...b.rows].forEach((r) => r.forEach(walk))
  }
  return found
}

/** The article's own "# Title", if it starts with one. */
export function articleTitle(blocks: Block[]): string | null {
  const first = blocks[0]
  return first && first.t === 'h' && first.level === 1 ? plain(first.c) : null
}
