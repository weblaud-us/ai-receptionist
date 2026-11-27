import { useEffect, useRef, useState } from 'react'
import { Ear, Loader2, Mic, MicOff, PhoneOff } from 'lucide-react'
import { Button } from '@receptionist/shared/components/ui/button'
import type { JoinInfo } from '@/lib/sales'
import { readTranscript, type TextStreamRoom, type TranscriptSegment } from '@/lib/transcript'

export type { TranscriptSegment }

/**
 * Joins a sales call's LiveKit room from the browser.
 * - listen: hidden, receive-only. Nobody on the call can hear or see you.
 * - takeover: your microphone is live and the AI goes silent for the rest of the call.
 * The LiveKit client is loaded only when a call is joined.
 */
export function CallAudio({ join, title, onLeave, onHangUp, onTranscript, onEnded }: {
  join: JoinInfo
  title: string
  onLeave: () => void
  onHangUp?: () => void
  /** Live transcript of the lead (and the AI, when it speaks). */
  onTranscript?: (segment: TranscriptSegment) => void
  onEnded?: () => void
}) {
  const transcriptRef = useRef(onTranscript)
  const endedRef = useRef(onEnded)
  useEffect(() => {
    transcriptRef.current = onTranscript
    endedRef.current = onEnded
  })
  const sink = useRef<HTMLDivElement>(null)
  const room = useRef<import('livekit-client').Room | null>(null)
  const [state, setState] = useState<'connecting' | 'connected' | 'ended' | 'error'>('connecting')
  const [error, setError] = useState('')
  const [muted, setMuted] = useState(false)
  const takeover = join.mode === 'takeover'

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const { Room, RoomEvent, Track } = await import('livekit-client')
        const r = new Room()
        room.current = r
        r.on(RoomEvent.TrackSubscribed, (track) => {
          if (track.kind === Track.Kind.Audio) sink.current?.appendChild(track.attach())
        })
        r.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((el) => el.remove()))
        r.on(RoomEvent.Disconnected, () => {
          if (cancelled) return
          setState('ended')
          endedRef.current?.()
        })
        ;(r as unknown as TextStreamRoom).registerTextStreamHandler?.('lk.transcription', (reader, from) => {
          void readTranscript(reader, from.identity, (seg) => transcriptRef.current?.(seg)).catch(() => {})
        })
        await r.connect(join.url, join.token)
        if (cancelled) return void r.disconnect()
        if (takeover) await r.localParticipant.setMicrophoneEnabled(true)
        setState('connected')
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Could not join the call')
          setState('error')
        }
      }
    })()
    return () => {
      cancelled = true
      void room.current?.disconnect()
    }
  }, [join.token, join.url, takeover])

  const toggleMute = () => {
    const next = !muted
    void room.current?.localParticipant.setMicrophoneEnabled(!next)
    setMuted(next)
  }

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
      <div ref={sink} className="hidden" />
      {state === 'connecting' ? <Loader2 className="size-4 animate-spin" /> : takeover ? <Mic className="size-4 text-emerald-600" /> : <Ear className="size-4 text-emerald-600" />}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{title}</div>
        <div className="text-xs text-muted-foreground">
          {state === 'connecting' && 'Joining…'}
          {state === 'connected' && (takeover ? 'You are on the call. The AI is silent and will not speak again.' : 'Listening. Nobody on the call can hear you.')}
          {state === 'ended' && 'The call has ended.'}
          {state === 'error' && <span className="text-destructive">{error}</span>}
        </div>
      </div>
      {takeover && state === 'connected' && (
        <Button size="sm" variant="outline" onClick={toggleMute}>{muted ? <MicOff /> : <Mic />} {muted ? 'Unmute' : 'Mute'}</Button>
      )}
      {onHangUp && state === 'connected' && (
        <Button size="sm" variant="destructive" onClick={onHangUp}><PhoneOff /> End call</Button>
      )}
      <Button size="sm" variant="outline" onClick={onLeave}>{state === 'connected' ? 'Leave' : 'Close'}</Button>
    </div>
  )
}
