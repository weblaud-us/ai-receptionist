/** Pure helpers for the built-in CRM screens (unit-tested). */
import type { Board, DealStage } from '@/lib/sales'

export const money = (amount: number, currency = 'USD') => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount || 0)
  } catch {
    return `${currency} ${Math.round(amount || 0).toLocaleString()}`
  }
}

/** The board after moving one deal, with every column's totals worked out again (for an instant drag). */
export function moveDeal(board: Board, dealId: string, to: DealStage): Board {
  const all = Object.values(board.columns).flat()
  const deal = all.find((d) => d.id === dealId)
  if (!deal || deal.stage === to) return board
  const moved = { ...deal, stage: to, probability: board.probability[to] ?? deal.probability }
  const columns = Object.fromEntries(
    (Object.keys(board.columns) as DealStage[]).map((s) => [s, s === to ? [moved, ...board.columns[s]] : board.columns[s].filter((d) => d.id !== dealId)]),
  ) as Board['columns']
  const totals = Object.fromEntries(
    (Object.keys(columns) as DealStage[]).map((s) => {
      const keep = board.totals[s]
      const delta = s === to ? 1 : s === deal.stage ? -1 : 0
      const value = keep.value + delta * deal.amount
      const weighted = s === to ? keep.weighted + deal.amount * moved.probability : s === deal.stage ? keep.weighted - deal.amount * deal.probability : keep.weighted
      return [s, { count: keep.count + delta, value, weighted }]
    }),
  ) as Board['totals']
  return { ...board, columns, totals }
}

/** "proof_points.0" style unconfirmed paths aside, the next free id for a new script, a view key… */
export function filtersLabel(filters: Record<string, string | number>): string {
  return Object.entries(filters)
    .filter(([, v]) => v !== '' && v !== undefined)
    .map(([k, v]) => `${k.replace('_', ' ')}: ${v}`)
    .join(' · ')
}
