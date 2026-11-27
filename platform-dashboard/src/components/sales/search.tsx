import { useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { Button } from '@receptionist/shared/components/ui/button'
import { useDebounced } from '@receptionist/shared/hooks/use-debounced'
import { LeadSheet } from '@/components/sales/leads'
import { searchSales, type Deal, type SalesLead } from '@/lib/sales'
import { money } from './crm-logic'

/** ⌘K / Ctrl+K: find a lead or deal by business, person, email or number (SALES_SUITE_PLAN.md Phase 16). */
export function SalesSearch() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<{ leads: SalesLead[]; deals: Deal[] } | null>(null)
  const [failed, setFailed] = useState(false)
  const [lead, setLead] = useState<string | null>(null)
  const term = useDebounced(q, 250)

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [])

  useEffect(() => {
    if (term.trim().length < 2) return
    let live = true
    searchSales(term.trim())
      .then((r) => { if (live) { setFailed(false); setResults(r) } })
      .catch(() => { if (live) { setFailed(true); setResults({ leads: [], deals: [] }) } })
    return () => { live = false }
  }, [term])

  const pick = (id: string) => {
    setOpen(false)
    setLead(id)
  }
  const shown = term.trim().length >= 2 ? results : null
  return (
    <>
      <Button size="sm" variant="outline" className="text-muted-foreground" onClick={() => setOpen(true)}>
        <Search /> Search <kbd className="ml-2 rounded border px-1 text-[10px]">Ctrl K</kbd>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader><DialogTitle>Find a lead or deal</DialogTitle></DialogHeader>
          <Input autoFocus value={q} placeholder="Business, person, email or phone number" onChange={(e) => setQ(e.target.value)} />
          <div className="max-h-80 space-y-1 overflow-y-auto">
            {shown?.leads.map((l) => (
              <button key={l.id} type="button" onClick={() => pick(l.id)} className="block w-full rounded-md p-2 text-left text-sm hover:bg-muted">
                <span className="font-medium">{l.business_name || l.phone}</span>
                <span className="block text-xs text-muted-foreground">{[l.contact_name, l.phone, l.status].filter(Boolean).join(' · ')}</span>
              </button>
            ))}
            {shown?.deals.map((d) => (
              <button key={d.id} type="button" onClick={() => pick(d.lead_id)} className="block w-full rounded-md p-2 text-left text-sm hover:bg-muted">
                <span className="font-medium">{d.title}</span>
                <span className="block text-xs text-muted-foreground">Deal · {d.stage.replace('_', ' ')} · {money(d.amount, d.currency)}</span>
              </button>
            ))}
            {failed && <p className="p-2 text-sm text-destructive" role="alert">Search isn't working right now. Try again in a moment.</p>}
            {!failed && shown && shown.leads.length + shown.deals.length === 0 && <p className="p-2 text-sm text-muted-foreground">Nothing found.</p>}
          </div>
        </DialogContent>
      </Dialog>
      <LeadSheet id={lead} onClose={() => setLead(null)} />
    </>
  )
}
