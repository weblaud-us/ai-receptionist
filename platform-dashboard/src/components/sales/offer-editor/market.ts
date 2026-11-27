import { useState } from 'react'
import type { Market } from '@/lib/sales'

const MARKET_KEY = 'sales-market'

/** The market the copilot writes for (spelling, AU purpose-of-call rules). Remembered per browser. */
export function useMarket(): [Market, (m: Market) => void] {
  const [market, setMarket] = useState<Market>(() => {
    const v = typeof localStorage !== 'undefined' ? localStorage.getItem(MARKET_KEY) : null
    return v === 'GB' || v === 'US' || v === 'CA' ? v : 'AU'
  })
  return [market, (m) => {
    setMarket(m)
    try {
      localStorage.setItem(MARKET_KEY, m)
    } catch {
      /* private mode */
    }
  }]
}
