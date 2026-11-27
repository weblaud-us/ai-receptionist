/** A checklist that ticks itself off: "Getting started" (dashboard) and "Launch this business" (platform). */
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { BookOpen, CheckCircle2, Circle, PartyPopper } from 'lucide-react'
import { cn } from '../lib/utils'
import { Button } from './ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Progress } from './ui/progress'

export interface ChecklistItem {
  id: string
  label: string
  body?: ReactNode
  done: boolean
  /** Where to do it. A route, or a click (e.g. switch a tab). */
  action?: { label: string; to?: string; onClick?: () => void }
  /** A help article's slug. */
  learnMore?: string
}

export function ChecklistCard({ title, description, items, onHide, hideLabel = 'Hide for now', doneTitle = "You're all set", doneBody, className, ...rest }: {
  title: string
  description?: ReactNode
  items: ChecklistItem[]
  onHide?: () => void
  hideLabel?: string
  doneTitle?: string
  doneBody?: ReactNode
  className?: string
  'data-tour'?: string
}) {
  const done = items.filter((i) => i.done).length
  const complete = items.length > 0 && done === items.length
  const pct = items.length ? Math.round((done / items.length) * 100) : 0
  return (
    <Card className={cn('border-primary/30', className)} data-tour={rest['data-tour']}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {complete && <PartyPopper className="size-4 text-primary" />} {complete ? doneTitle : title}
        </CardTitle>
        <CardDescription>{complete ? (doneBody ?? 'Every step is done.') : description}</CardDescription>
        {onHide && (
          <CardAction>
            <Button size="sm" variant="ghost" onClick={onHide}>{complete ? 'Close' : hideLabel}</Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">{done} of {items.length} done</div>
          <Progress value={pct} aria-label={`${done} of ${items.length} done`} />
        </div>
        {!complete && (
          <ul className="divide-y">
            {items.map((i) => (
              <li key={i.id} className="flex flex-wrap items-start gap-3 py-2.5 first:pt-0 last:pb-0" data-done={i.done || undefined}>
                {i.done
                  ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="Done" />
                  : <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="To do" />}
                <div className="min-w-0 flex-1">
                  <div className={cn('text-sm font-medium', i.done && 'text-muted-foreground line-through decoration-muted-foreground/40')}>{i.label}</div>
                  {i.body && !i.done && <div className="text-xs text-muted-foreground">{i.body}</div>}
                  {i.learnMore && !i.done && (
                    <Link to={`/help/${i.learnMore}`} className="mt-0.5 inline-flex items-center gap-1 text-xs text-primary hover:underline">
                      <BookOpen className="size-3" /> Learn more
                    </Link>
                  )}
                </div>
                {i.action && !i.done && (
                  i.action.to
                    ? <Button size="sm" variant="outline" nativeButton={false} render={<Link to={i.action.to} />}>{i.action.label}</Button>
                    : <Button size="sm" variant="outline" onClick={i.action.onClick}>{i.action.label}</Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
