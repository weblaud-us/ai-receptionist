import { useState, type ReactNode } from 'react'
import { ConfirmDialog } from '@receptionist/shared/components/blocks'

export interface ConfirmRequest {
  title: string
  description?: ReactNode
  confirmLabel?: string
  destructive?: boolean
  run: () => void
}

/**
 * One confirmation dialog for a component's risky actions (dialling real people, changing what the
 * agent says on live calls, removing access). Render `dialog` once; call `ask({...})` instead of the
 * action itself.
 */
export function useConfirm() {
  const [req, setReq] = useState<ConfirmRequest | null>(null)
  const dialog = (
    <ConfirmDialog open={!!req} onOpenChange={(o) => !o && setReq(null)} title={req?.title ?? ''}
      description={req?.description} confirmLabel={req?.confirmLabel} destructive={req?.destructive}
      onConfirm={() => { const run = req?.run; setReq(null); run?.() }} />
  )
  return { dialog, ask: setReq }
}
