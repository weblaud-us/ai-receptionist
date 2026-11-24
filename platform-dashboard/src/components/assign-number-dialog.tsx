import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@receptionist/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@receptionist/shared/components/ui/dialog'
import { Input } from '@receptionist/shared/components/ui/input'
import { usePlatformMutations } from '@/lib/platform'

/** Route an existing SIP-trunk number to a business. Used by the businesses list and the details page. */
export function AssignNumberDialog({ tenant, onClose }: { tenant: { id: string; name: string }; onClose: () => void }) {
  const m = usePlatformMutations()
  const [e164, setE164] = useState('')
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Assign a number to {tenant.name}</DialogTitle>
          <DialogDescription>Calls to this number will be answered as {tenant.name}. It must already route to the LiveKit SIP trunk.</DialogDescription>
        </DialogHeader>
        <Input value={e164} placeholder="+61 2 5550 4120" onChange={(e) => setE164(e.target.value)} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={e164.replace(/\D/g, '').length < 8 || m.assignNumber.isPending}
            onClick={() => m.assignNumber.mutate({ e164, tenant_id: tenant.id }, { onSuccess: () => { toast.success('Assigned'); onClose() } })}>
            Assign
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
