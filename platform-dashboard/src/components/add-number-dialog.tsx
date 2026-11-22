import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'
import { Button } from '@receptionist/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { AssignNumberDialog } from '@/components/assign-number-dialog'
import { NumberChooser } from '@/components/number-chooser'
import { useOrderTenantNumber, type NumberChoice } from '@/lib/platform'

/**
 * Give a business a number, end to end: pick one you own or buy one, and it's put on the Twilio trunk,
 * added to LiveKit, routed and test-called. "Assign only" keeps the old routing-table-only option for
 * numbers you've already wired up by hand.
 */
export function AddNumberDialog({ tenant, onClose, onOrdered }: {
  tenant: { id: string; name: string; country?: string }
  onClose: () => void
  onOrdered?: () => void
}) {
  const order = useOrderTenantNumber(tenant.id)
  const [choice, setChoice] = useState<NumberChoice>({ source: 'none', e164: '' })
  const [manual, setManual] = useState(false)
  if (manual) return <AssignNumberDialog tenant={tenant} onClose={onClose} />
  const ready = choice.source !== 'none' && !!choice.e164
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add a number to {tenant.name}</DialogTitle>
          <DialogDescription>It's put on the SIP trunk, added to LiveKit, routed to {tenant.name} and test-called automatically.</DialogDescription>
        </DialogHeader>
        <NumberChooser value={choice} onChange={setChoice} defaultCountry={tenant.country} />
        <DialogFooter className="sm:justify-between">
          <Button variant="link" className="px-0 text-muted-foreground" onClick={() => setManual(true)}>Assign only (already wired up)</Button>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>Cancel</Button>
            <Button disabled={!ready || order.isPending}
              onClick={() => choice.source !== 'none' && order.mutate({ e164: choice.e164, source: choice.source }, {
                onSuccess: (r) => {
                  toast.success(r.order.status === 'needs_documents' ? 'Ordered: waiting for the regulatory bundle' : `Setting up ${r.order.e164}…`)
                  onOrdered?.()
                  onClose()
                },
              })}>
              {order.isPending && <Loader2 className="animate-spin" />}
              {choice.source === 'buy' ? 'Buy and connect' : 'Connect number'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
