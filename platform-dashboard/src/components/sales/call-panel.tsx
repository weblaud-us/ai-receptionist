/** Pieces of a live sales call shown in the browser: the timer and the live transcript (power dialer, single calls). */
import { useEffect, useRef, useState } from 'react'
import { Timer } from 'lucide-react'
import { cn } from '@receptionist/shared/lib/utils'
import type { TranscriptSegment } from '@/components/sales/call-audio'
import { useAttempt } from '@/lib/sales'

export function CallTimer({ attemptId, running }: { attemptId: string; running: boolean }) {
  const q = useAttempt(attemptId, running ? 2000 : false)
  const answered = (q.data?.attempt as { answered_at?: string | null } | undefined)?.answered_at
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [running])
  const status = q.data?.attempt.status
  const secs = answered ? Math.max(0, Math.round((now - new Date(answered).getTime()) / 1000)) : 0
  return (
    <div className="flex items-center gap-2 text-sm">
      <Timer className="size-4 text-muted-foreground" />
      {answered ? <span className="font-medium tabular-nums">{Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</span>
        : <span className="text-muted-foreground">{status === 'dialing' ? 'Ringing…' : status === 'done' ? 'Ended' : 'Connecting…'}</span>}
      {q.data?.attempt.amd && q.data.attempt.amd !== 'human' && <span className="text-xs text-amber-600">({q.data.attempt.amd.replace('machine-', 'machine: ')})</span>}
    </div>
  )
}

export function Transcript({ segments, empty = 'What they say appears here as they speak.' }: { segments: TranscriptSegment[]; empty?: string }) {
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = box.current
    if (el) el.scrollTop = el.scrollHeight                          // follow the conversation
  }, [segments])
  return (
    <div ref={box} role="log" aria-live="polite" aria-label="Live transcript" className="h-72 space-y-1.5 overflow-y-auto rounded-md border p-2 text-sm">
      {segments.length === 0 ? <p className="text-xs text-muted-foreground">{empty}</p> : segments.map((s) => (
        <div key={s.id} className={cn(!s.final && 'text-muted-foreground')}>
          <span className="mr-1 text-xs font-medium uppercase">{s.identity === 'lead' ? 'Them' : 'AI'}</span>{s.text}
        </div>
      ))}
    </div>
  )
}
