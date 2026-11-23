import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Pause, Play } from 'lucide-react'
import { SimpleSelect } from '@receptionist/shared/components/simple-select'
import { Button } from '@receptionist/shared/components/ui/button'
import { Input } from '@receptionist/shared/components/ui/input'
import { useVoices } from '@/lib/tenant-hooks'
import type { Voice } from '@/lib/tenant-types'

interface Props {
  provider: string
  value: string
  onChange: (voiceId: string) => void
  /** Country of the business: AU puts Australian voices first, GB British ones. */
  country?: string
}

const REGIONAL_ACCENT: Record<string, { match: RegExp; label: string }> = {
  AU: { match: /austral/i, label: 'Australian' },
  GB: { match: /brit|english|uk\b|scottish|irish|welsh/i, label: 'British' },
}

function describe(v: Voice): string {
  const bits = [v.gender, v.accent, v.age].filter(Boolean).map((s) => String(s).replace(/_/g, ' '))
  return bits.length ? `${v.name} · ${bits.join(', ')}` : v.name
}

/**
 * Voice picker with audio previews (Roadmap Task 2.10). ElevenLabs and Deepgram Aura voices come from
 * the server's catalogue (Aura has no previews); any other provider falls back to a plain ID.
 */
export function VoicePicker({ provider, value, onChange, country }: Props) {
  const q = useVoices(provider)
  const [custom, setCustom] = useState(false)
  const audio = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)

  const regional = country ? REGIONAL_ACCENT[country] : undefined
  // Local accents first: callers trust a voice that sounds like their own reception desk.
  const voices = useMemo(() => {
    const all = q.data?.voices ?? []
    if (!regional) return all
    const local = (v: Voice) => regional.match.test(v.accent ?? '')
    return [...all.filter(local), ...all.filter((v) => !local(v))]
  }, [q.data, regional])
  const known = voices.some((v) => v.voice_id === value)
  const selected = voices.find((v) => v.voice_id === value) ?? voices.find((v) => v.voice_id === q.data?.default_voice_id)

  useEffect(() => () => { audio.current?.pause() }, [])

  const toggle = (v: Voice | undefined) => {
    if (!v?.preview_url) return
    if (playing === v.voice_id) {
      audio.current?.pause()
      setPlaying(null)
      return
    }
    audio.current?.pause()
    const a = new Audio(v.preview_url)
    a.onended = () => setPlaying(null)
    a.onerror = () => setPlaying(null)
    audio.current = a
    setPlaying(v.voice_id)
    void a.play().catch(() => setPlaying(null))
  }

  if (q.isLoading) {
    return <div className="flex h-9 items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading voices…</div>
  }

  const available = !!q.data?.available && voices.length > 0
  if (!available || custom || (value && !known && !custom && voices.length === 0)) {
    return (
      <div className="space-y-1.5">
        <Input value={value} placeholder="Voice ID (empty = default voice)" onChange={(e) => onChange(e.target.value)} />
        <p className="text-xs text-muted-foreground">
          {q.data?.note ?? "From your voice provider's library."}
          {available && (
            <> <button type="button" className="underline" onClick={() => setCustom(false)}>Pick from the list instead</button></>
          )}
        </p>
      </div>
    )
  }

  const options = [
    { value: '', label: q.data?.default_voice_id ? 'Default voice' : 'Default voice (provider default)' },
    ...voices.map((v) => ({
      value: v.voice_id,
      label: regional && regional.match.test(v.accent ?? '') ? `★ ${describe(v)} (${regional.label})` : describe(v),
    })),
    ...(value && !known ? [{ value, label: `Custom voice ID: ${value}` }] : []),
  ]
  return (
    <div className="space-y-1.5">
      <div className="flex gap-2">
        <SimpleSelect className="flex-1" value={value} onChange={onChange} options={options} />
        <Button type="button" size="icon" variant="outline" aria-label={playing ? 'Stop preview' : 'Play preview'}
          disabled={!selected?.preview_url} onClick={() => toggle(selected)}>
          {playing && playing === selected?.voice_id ? <Pause /> : <Play />}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        {selected?.description ? `${selected.description}. ` : ''}{selected?.preview_url ? 'Press play to hear a sample. ' : 'Make a test call to hear it. '}
        <button type="button" className="underline" onClick={() => setCustom(true)}>Use a voice ID instead</button>
      </p>
    </div>
  )
}
