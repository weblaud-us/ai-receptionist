/** Live call transcripts from LiveKit's `lk.transcription` text streams (power dialer, test calls). */

/** One line of the live transcript, as LiveKit's `lk.transcription` text streams deliver it. */
export interface TranscriptSegment { id: string; identity: string; text: string; final: boolean }

export interface TextStreamReader extends AsyncIterable<string> { info: { id: string; attributes?: Record<string, string> } }
export type TextStreamRoom = { registerTextStreamHandler?: (topic: string, cb: (reader: TextStreamReader, from: { identity: string }) => void) => void }

/** Text streams arrive per segment; interim versions of a segment replace each other until it is final. */
export function readTranscript(reader: TextStreamReader, identity: string, emit: (s: TranscriptSegment) => void): Promise<void> {
  const attrs = reader.info.attributes ?? {}
  const id = attrs['lk.segment_id'] || reader.info.id
  return (async () => {
    let text = ''
    for await (const chunk of reader) {
      text += chunk
      emit({ id, identity, text, final: false })
    }
    emit({ id, identity, text, final: attrs['lk.transcription_final'] !== 'false' })
  })()
}
