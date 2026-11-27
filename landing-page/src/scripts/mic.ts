/**
 * Opening the microphone for a browser call, with fallbacks (the same ladder as the API's call page,
 * AI-receptionist-1/ui/call.html).
 *
 * Chrome's "Could not start audio source" is a NotReadableError: permission was granted but the
 * operating system couldn't open the device (another app holds it, Windows privacy settings, a broken
 * or virtual default device). So before giving up we try without voice isolation, then every other
 * microphone, then the browser's plain default with no processing asked for.
 */
import type { LocalAudioTrack } from 'livekit-client';

const RETRYABLE = ['NotReadableError', 'TrackStartError', 'OverconstrainedError', 'ConstraintNotSatisfiedError', 'AbortError'];
const BASE = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };

const nameOf = (e: unknown) => (e instanceof Error || e instanceof DOMException ? e.name : '') || '';
const retryable = (e: unknown) => RETRYABLE.includes(nameOf(e));

/** A LiveKit microphone track, or a plain MediaStreamTrack from the last-resort attempt. */
export async function acquireMic(): Promise<LocalAudioTrack | MediaStreamTrack> {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    throw Object.assign(new Error('insecure'), { name: 'InsecureContext' });
  }
  const { createLocalAudioTrack } = await import('livekit-client');
  let last: unknown = null;
  for (const opts of [BASE, { ...BASE, voiceIsolation: false }]) {
    try {
      return await createLocalAudioTrack(opts);
    } catch (e) {
      last = e;
      if (!retryable(e)) throw e;
    }
  }
  const devices = await navigator.mediaDevices.enumerateDevices().catch(() => [] as MediaDeviceInfo[]);
  for (const d of devices) {
    if (d.kind !== 'audioinput' || !d.deviceId || d.deviceId === 'default' || d.deviceId === 'communications') continue;
    try {
      return await createLocalAudioTrack({ ...BASE, voiceIsolation: false, deviceId: { exact: d.deviceId } });
    } catch (e) {
      last = e;
      if (!retryable(e)) throw e;
    }
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return stream.getAudioTracks()[0];
  } catch (e) {
    throw retryable(e) && last ? last : e;
  }
}

/** What to tell the visitor, by the browser's error name. */
export function micMessage(e: unknown): string {
  const n = nameOf(e);
  if (n === 'InsecureContext') return 'The microphone needs a secure (https) page. Open the site over https and try again.';
  if (n === 'NotAllowedError' || n === 'SecurityError' || n === 'PermissionDeniedError') {
    return 'Microphone access was blocked. Allow it from the icon in your address bar, then tap again.';
  }
  if (n === 'NotFoundError' || n === 'DevicesNotFoundError') return 'No microphone found. Plug one in, or play the sample calls below.';
  if (RETRYABLE.includes(n)) {
    return 'Your microphone is busy or blocked. Close Teams, Zoom or other apps using it, check Windows Settings › Privacy & security › Microphone, then tap again.';
  }
  return 'Couldn’t start your microphone. Check it’s plugged in and not in use, then tap again.';
}

export function stopTrack(t: LocalAudioTrack | MediaStreamTrack | null) {
  try {
    t?.stop();
  } catch {
    /* already stopped */
  }
}
