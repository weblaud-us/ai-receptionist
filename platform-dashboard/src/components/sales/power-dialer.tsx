import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, PhoneCall, PhoneOutgoing, Timer } from 'lucide-react'
import { ConfirmDialog, Field } from '@receptionist/shared/components/blocks'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@receptionist/shared/components/ui/card'
import { Input } from '@receptionist/shared/components/ui/input'
import { Textarea } from '@receptionist/shared/components/ui/textarea'
import { timeAgo, titleCase } from '@receptionist/shared/lib/format'
import { cn } from '@receptionist/shared/lib/utils'
import { CallAudio, type TranscriptSegment } from '@/components/sales/call-audio'
import { STAGE_LABEL, useAttempt, useCampaigns, useSalesMutations, type CallContext, type JoinInfo, type WrapUpInput } from '@/lib/sales'

const REASON: Record<string, string> = {
  no_leads: 'No leads are due to be called right now.',
  busy: 'This campaign already has its maximum number of calls in progress.',
  daily_cap: "This campaign has reached today's call limit.",
  numbers_resting: 'Every caller ID has reached its daily volume. Try again later.',
  dispatch_failed: 'Could not start the call. Is the sales worker running?',
  gate: 'The next lead did not pass the compliance checks.',
  legal_review: "This campaign's legal review doesn't cover its current settings. Start it again to confirm.",
}

/** Outcomes a person picks after a call they took (never the system-only ones). */
const WRAP_OUTCOMES: { value: string; label: string }[] = [
  { value: 'interested', label: 'Interested' },
  { value: 'demo_booked', label: 'Meeting booked (I booked it)' },
  { value: 'callback', label: 'Call back later' },
  { value: 'not_interested', label: 'Not interested (this offer)' },
  { value: 'gatekeeper', label: 'Reached a gatekeeper' },
  { value: 'no_conversation', label: 'No real conversation' },
  { value: 'voicemail', label: 'Voicemail' },
  { value: 'wrong_number', label: 'Wrong number' },
]

interface LiveCall { attemptId: string; join: JoinInfo; context: CallContext | null; title: string }

/**
 * Human-assisted calling: ring the next lead that passes the checks, talk with their details, research
 * and the offer's cheat sheet in front of you, read the live transcript, then wrap up and dial the next.
 */
export function PowerDialer() {
  const campaigns = (useCampaigns().data?.campaigns ?? []).filter((c) => c.mode === 'human_assisted' && c.status === 'running')
  const m = useSalesMutations()
  const [campaignId, setCampaignId] = useState('')
  const [call, setCall] = useState<LiveCall | null>(null)
  const [segments, setSegments] = useState<TranscriptSegment[]>([])
  const [ended, setEnded] = useState(false)
  const [done, setDone] = useState(0)
  const chosen = campaignId || campaigns[0]?.id || ''

  if (campaigns.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><PhoneOutgoing className="size-4" /> Power dialer</CardTitle>
          <CardDescription>Start a human-assisted campaign (Campaigns tab) to call its leads yourself from here.</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const dial = () =>
    m.dialNext.mutate(chosen, {
      onSuccess: (r) => {
        if (r.dialed && r.join && r.attempt_id) {
          setSegments([])
          setEnded(false)
          setCall({ attemptId: r.attempt_id, join: r.join, context: r.context ?? null, title: `Calling ${r.lead?.business_name || r.lead?.phone}` })
        } else toast.message(REASON[r.reason ?? ''] ?? 'Nothing was dialled.')
      },
    })
  const onTranscript = (seg: TranscriptSegment) =>
    setSegments((prev) => {
      const i = prev.findIndex((s) => s.id === seg.id)
      if (i === -1) return [...prev, seg]
      const next = [...prev]
      next[i] = seg
      return next
    })
  const finished = (andNext: boolean, endCall: boolean) => {
    const current = call
    const next = () => {
      setDone((n) => n + 1)
      setCall(null)
      if (andNext) dial()
    }
    if (current && !ended) {
      // Still connected. A plain save keeps the call going (closing the panel would only drop you, and
      // leave them on the line). Dialling on, or a do-not-call, ends the call first, so the next lead
      // never rings while someone is still on the phone.
      if (andNext || endCall) m.hangUp.mutate(current.attemptId, { onSettled: next })
      return
    }
    next()
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center gap-3 space-y-0">
        <div className="min-w-0 flex-1">
          <CardTitle className="flex items-center gap-2"><PhoneOutgoing className="size-4" /> Power dialer</CardTitle>
          <CardDescription>Rings the next lead that passes the checks and puts you on the call. Answering machines are skipped. Allow the microphone when asked.</CardDescription>
        </div>
        {campaigns.length > 1 && (
          <SimpleSelect size="sm" value={chosen} onChange={setCampaignId} options={campaigns.map((c) => ({ value: c.id, label: c.name }))} />
        )}
        {done > 0 && <span className="text-xs text-muted-foreground">{done} call{done === 1 ? '' : 's'} this session</span>}
        <Button disabled={m.dialNext.isPending || !!call || !chosen} onClick={dial}>
          {m.dialNext.isPending ? <Loader2 className="animate-spin" /> : <PhoneCall />} Dial next ({campaigns.find((c) => c.id === chosen)?.progress?.to_dial ?? 0} left)
        </Button>
      </CardHeader>
      {call && (
        <CardContent className="grid gap-4 lg:grid-cols-[17rem_minmax(0,1fr)_20rem]">
          <LeadPanel context={call.context} />
          <div className="space-y-3">
            <CallAudio key={call.join.token} join={call.join} title={call.title} onLeave={() => setEnded(true)}
              onHangUp={() => m.hangUp.mutate(call.attemptId)} onTranscript={onTranscript} onEnded={() => setEnded(true)} />
            <CallTimer attemptId={call.attemptId} running={!ended} />
            <Transcript segments={segments} />
          </div>
          <div className="space-y-3">
            <CheatSheet context={call.context} />
            <WrapUpForm attemptId={call.attemptId} ended={ended} onDone={finished} />
          </div>
        </CardContent>
      )}
    </Card>
  )
}

function LeadPanel({ context }: { context: CallContext | null }) {
  const lead = context?.lead
  if (!lead) return <div className="text-sm text-muted-foreground">Loading the lead…</div>
  const r = context?.research?.data
  return (
    <div className="space-y-3 text-sm">
      <div>
        <div className="text-base font-medium">{lead.business_name || lead.phone}</div>
        <div className="text-muted-foreground">{[lead.contact_name, lead.phone].filter(Boolean).join(' · ')}</div>
        {lead.website && <div className="truncate text-xs text-muted-foreground">{lead.website}</div>}
        <div className="text-xs text-muted-foreground">{[lead.region_code, lead.country].filter(Boolean).join(', ')}</div>
      </div>
      {r && (
        <div className="space-y-1 rounded-md bg-muted p-2 text-xs">
          <p>{r.summary}</p>
          {r.hook && <p><span className="font-medium">Hook:</span> {r.hook}</p>}
          {r.likely_pains.length > 0 && <p><span className="font-medium">Likely problems:</span> {r.likely_pains.join('; ')}</p>}
        </div>
      )}
      {(context?.memory?.length ?? 0) > 0 && (
        <div className="space-y-1 rounded-md border border-sky-200 bg-sky-50 p-2 text-xs dark:border-sky-500/30 dark:bg-sky-500/10">
          <div className="font-medium">Pick up where we left off</div>
          {context?.memory?.map((line, i) => <p key={i}>{line}</p>)}
        </div>
      )}
      {(context?.earlier_calls.length ?? 0) > 0 && (
        <div className="space-y-1 text-xs">
          <div className="font-medium">Earlier calls</div>
          {context?.earlier_calls.map((c) => <div key={c.id} className="text-muted-foreground">{titleCase(c.outcome)} · {timeAgo(c.created_at)}{c.summary ? ` — ${c.summary}` : ''}</div>)}
        </div>
      )}
    </div>
  )
}

function CallTimer({ attemptId, running }: { attemptId: string; running: boolean }) {
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

function Transcript({ segments }: { segments: TranscriptSegment[] }) {
  return (
    <div className="h-72 space-y-1.5 overflow-y-auto rounded-md border p-2 text-sm">
      {segments.length === 0 ? <p className="text-xs text-muted-foreground">What they say appears here as they speak.</p> : segments.map((s) => (
        <div key={s.id} className={cn(!s.final && 'text-muted-foreground')}>
          <span className="mr-1 text-xs font-medium uppercase">{s.identity === 'lead' ? 'Them' : 'AI'}</span>{s.text}
        </div>
      ))}
    </div>
  )
}

function CheatSheet({ context }: { context: CallContext | null }) {
  const s = context?.cheat_sheet
  if (!s) return null
  return (
    <div className="max-h-80 space-y-2 overflow-y-auto rounded-md border p-2 text-xs">
      <div className="font-medium">{s.offer} · {s.company}</div>
      <p className="rounded bg-muted p-1.5">“{s.opener}”</p>
      {s.first_question && <p><span className="font-medium">Ask first:</span> {s.first_question}</p>}
      {s.discovery_questions.length > 0 && <div><div className="font-medium">Discovery</div><ul className="list-disc pl-4">{s.discovery_questions.map((q) => <li key={q}>{q}</li>)}</ul></div>}
      {s.pitch && <p><span className="font-medium">Pitch:</span> {s.pitch}</p>}
      {s.proof_points.length > 0 && <div><div className="font-medium">Proof</div><ul className="list-disc pl-4">{s.proof_points.map((q) => <li key={q}>{q}</li>)}</ul></div>}
      {s.objections.length > 0 && (
        <div><div className="font-medium">Objections</div>
          {s.objections.map((o) => <p key={o.objection}><span className="italic">“{o.objection}”</span> — {o.response}</p>)}
        </div>
      )}
      <p><span className="font-medium">Price:</span> {s.pricing}</p>
      <p><span className="font-medium">Ask for:</span> a {s.meeting}</p>
    </div>
  )
}

function WrapUpForm({ attemptId, ended, onDone }: { attemptId: string; ended: boolean; onDone: (andNext: boolean, endCall: boolean) => void }) {
  const m = useSalesMutations()
  const [outcome, setOutcome] = useState('')
  const [notes, setNotes] = useState('')
  const [nextStep, setNextStep] = useState('')
  const [callbackAt, setCallbackAt] = useState('')
  const [stage, setStage] = useState('')
  const [task, setTask] = useState('')
  const [confirmDnc, setConfirmDnc] = useState(false)

  const save = (andNext: boolean, body?: Partial<WrapUpInput>) => {
    const payload: WrapUpInput & { id: string } = {
      id: attemptId, outcome, notes, next_step: nextStep, stage: stage || null,
      callback_at: outcome === 'callback' && callbackAt ? new Date(callbackAt).toISOString() : null,
      task: task.trim() ? { title: task.trim() } : null, ...body,
    }
    m.wrapUp.mutate(payload, { onSuccess: () => { toast.success('Saved'); onDone(andNext, payload.outcome === 'dnc') } })
  }
  const ready = !!outcome && (outcome !== 'callback' || !!callbackAt)

  return (
    <div className={cn('space-y-2 rounded-md border p-2', ended && 'border-primary')}>
      <div className="text-sm font-medium">{ended ? 'Wrap up' : 'Notes (save when the call ends)'}</div>
      <SimpleSelect className="w-full" size="sm" placeholder="How did it go?" value={outcome} onChange={setOutcome} options={WRAP_OUTCOMES} />
      {outcome === 'callback' && (
        <Field label="Call back at (your time)"><Input type="datetime-local" value={callbackAt} onChange={(e) => setCallbackAt(e.target.value)} /></Field>
      )}
      <Textarea rows={3} value={notes} placeholder="What they said, what matters" onChange={(e) => setNotes(e.target.value)} />
      <Input value={nextStep} placeholder="Next step, e.g. send proposal by Fri" onChange={(e) => setNextStep(e.target.value)} />
      <Input value={task} placeholder="Add a follow-up task (optional)" onChange={(e) => setTask(e.target.value)} />
      <SimpleSelect className="w-full" size="sm" value={stage || 'auto'} onChange={(v) => setStage(v === 'auto' ? '' : v)}
        options={[{ value: 'auto', label: 'Stage: automatic' }, ...(['interested', 'meeting_booked', 'proposal', 'won', 'lost'] as const).map((s) => ({ value: s, label: `Stage: ${STAGE_LABEL[s]}` }))]} />
      <div className="flex flex-wrap gap-2 pt-1">
        <Button size="sm" disabled={!ready || m.wrapUp.isPending} onClick={() => save(true)}>
          {m.wrapUp.isPending && <Loader2 className="animate-spin" />} {ended ? 'Save & dial next' : 'Hang up, save & dial next'}
        </Button>
        <Button size="sm" variant="outline" disabled={!ready || m.wrapUp.isPending} onClick={() => save(false)}>Save</Button>
        <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setConfirmDnc(true)}>They asked not to be called</Button>
      </div>
      <ConfirmDialog open={confirmDnc} onOpenChange={setConfirmDnc} destructive title="Never call this number again?"
        description="It goes on the do-not-call list for every campaign and offer, for good. For a plain “not interested”, pick that outcome instead."
        confirmLabel="Add to do-not-call" loading={m.wrapUp.isPending}
        onConfirm={() => { setConfirmDnc(false); save(false, { outcome: 'dnc', confirm_dnc: true }) }} />
    </div>
  )
}
