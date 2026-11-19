import { Link } from 'react-router-dom'
import { AlertTriangle, Clock } from 'lucide-react'
import { Button } from '@receptionist/shared/components/ui/button'
import { dateOnly } from '@receptionist/shared/lib/format'
import { useTenant } from '@/lib/tenant'
import { cn } from '@receptionist/shared/lib/utils'

/** One line above every page when the plan needs attention (Phase 5: past due, paused, trial, cap). */
export function BillingBanner() {
  const { ctx } = useTenant()
  const b = ctx?.billing
  if (!ctx || !b || ctx.legacy) return null

  let tone: 'warn' | 'info' = 'warn'
  let text: string | null = null
  if (b.status === 'past_due') text = 'Your last payment failed. Update your card to keep your assistant answering.'
  else if (b.status === 'suspended') text = "Your assistant is paused — calls are being forwarded. Update your card to switch it back on."
  else if (b.status === 'cancelled') text = `Your subscription has ended. Your data is kept until ${dateOnly(b.delete_after)}.`
  else if (b.status === 'incomplete') text = 'Choose a plan to switch your assistant on.'
  else if (b.gate === 'cap') text = 'All included minutes are used — calls are being forwarded.'
  else if (b.status === 'trial' && b.self_serve && b.trial_ends_at) {
    tone = 'info'
    text = `Free trial until ${dateOnly(b.trial_ends_at)}.`
  }
  if (!text) return null
  const ownerOnly = ctx.platform_admin || ctx.role === 'owner'
  return (
    <div className={cn('mb-4 flex flex-wrap items-center gap-3 rounded-lg border p-3 text-sm',
      tone === 'warn' ? 'border-amber-500/40 bg-amber-500/10' : 'border-border bg-muted/50')}>
      {tone === 'warn' ? <AlertTriangle className="size-4 shrink-0 text-amber-600" /> : <Clock className="size-4 shrink-0 text-muted-foreground" />}
      <span className="flex-1">{text}</span>
      {ownerOnly && (
        <Button size="xs" variant="outline" nativeButton={false} render={<Link to="/billing" />}>Plan & billing</Button>
      )}
    </div>
  )
}
