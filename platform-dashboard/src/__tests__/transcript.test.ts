import { describe, expect, it } from 'vitest'
import { readTranscript, type TextStreamReader, type TranscriptSegment } from '@/lib/transcript'

function stream(chunks: string[], attributes: Record<string, string>): TextStreamReader {
  return {
    info: { id: 'stream-1', attributes },
    async *[Symbol.asyncIterator]() {
      for (const c of chunks) yield c
    },
  }
}

describe('live transcript', () => {
  it('builds a segment from its chunks and marks it final at the end', async () => {
    const seen: TranscriptSegment[] = []
    await readTranscript(stream(['We use ', 'spreadsheets ', 'for bookings'], { 'lk.segment_id': 'seg-9', 'lk.transcription_final': 'true' }),
      'lead', (s) => seen.push(s))
    expect(seen.map((s) => s.text)).toEqual(['We use ', 'We use spreadsheets ', 'We use spreadsheets for bookings', 'We use spreadsheets for bookings'])
    expect(seen.at(-1)).toEqual({ id: 'seg-9', identity: 'lead', text: 'We use spreadsheets for bookings', final: true })
  })

  it('keeps an interim segment open', async () => {
    const seen: TranscriptSegment[] = []
    await readTranscript(stream(['Hel'], { 'lk.segment_id': 'seg-1', 'lk.transcription_final': 'false' }), 'lead', (s) => seen.push(s))
    expect(seen.at(-1)?.final).toBe(false)
  })
})
