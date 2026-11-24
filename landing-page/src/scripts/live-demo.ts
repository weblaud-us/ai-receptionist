/**
 * "Talk to it now" (LiveDemo.astro): Google sign-in → tap → a live call with the demo practice.
 *
 * - Google Identity Services loads only when the panel comes near the viewport; its credential (a
 *   one-hour ID token) is kept in memory only and sent to POST /public/web-demo/start, which records the
 *   lead and returns a LiveKit token for a fresh room.
 * - livekit-client is imported on the first tap, so the page pays nothing for it until then.
 * - States follow the agent's own `lk.agent.state` (listening / thinking / speaking). The transcript is
 *   LiveKit's `lk.transcription` text streams, written with textContent (never HTML).
 * - The orb follows the audio level (Web Audio analyser on the agent's voice and the microphone);
 *   with reduced motion it only changes colour.
 * - If the browser blocks playback (autoplay rules), the orb asks for a tap and that tap turns the sound
 *   on instead of ending the call. If no agent joins the room, the call ends with a message rather than
 *   leaving the visitor in silence.
 */
import type { RemoteTrack, Room } from 'livekit-client';
import { currentRegion } from './region';

type State = 'signed-out' | 'ready' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'ending' | 'ended';

const LABEL: Record<State, string> = {
  'signed-out': 'Sign in to talk',
  ready: 'Tap to talk',
  connecting: 'Connecting…',
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
  ending: 'Ending…',
  ended: 'Talk again',
};
const IN_CALL: State[] = ['connecting', 'listening', 'thinking', 'speaking', 'ending'];
const AGENT_STATES: Record<string, State> = { listening: 'listening', thinking: 'thinking', speaking: 'speaking' };
const CREDENTIAL_MAX_AGE_MS = 50 * 60_000; // Google ID tokens last an hour
const AGENT_JOIN_TIMEOUT_MS = 15_000; // the agent normally joins in 1–3 s
const AGENT_VOICE_TRACK = 'roomio_audio'; // the agent's speech (its other audio track is background sound)
const GIS_SRC = 'https://accounts.google.com/gsi/client';

interface StartResponse {
  token: string;
  url: string;
  room: string;
  max_seconds: number;
  practice_name: string;
  agent_name: string;
  first_name: string;
  lead_practice_name: string;
}

interface GoogleId {
  initialize(opts: Record<string, unknown>): void;
  renderButton(el: HTMLElement, opts: Record<string, unknown>): void;
}
declare global {
  interface Window {
    google?: { accounts: { id: GoogleId } };
  }
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.max(0, Math.floor(s % 60))).padStart(2, '0')}`;

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) return resolve();
    const el = document.createElement('script');
    el.src = src;
    el.async = true;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error('script failed'));
    document.head.appendChild(el);
  });
}

/** The given name from a Google ID token, for a friendlier status line (display only; the server verifies). */
function givenName(credential: string): string {
  try {
    const b64 = credential.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(b64.padEnd(Math.ceil(b64.length / 4) * 4, '=')), (c) => c.charCodeAt(0));
    const json = JSON.parse(new TextDecoder().decode(bytes)) as { given_name?: string; name?: string };
    return (json.given_name || json.name?.split(' ')[0] || '').slice(0, 40);
  } catch {
    return '';
  }
}

function attribution() {
  const q = new URLSearchParams(location.search);
  return {
    page: location.pathname.slice(0, 120),
    referrer: document.referrer.slice(0, 200),
    utm_source: (q.get('utm_source') ?? '').slice(0, 80),
    utm_medium: (q.get('utm_medium') ?? '').slice(0, 80),
    utm_campaign: (q.get('utm_campaign') ?? '').slice(0, 80),
  };
}

export function initLiveDemo() {
  const root = document.querySelector<HTMLElement>('[data-live-demo]');
  if (!root) return;
  const api = root.dataset.api ?? '';
  const clientId = root.dataset.clientId ?? '';
  const $ = <T extends HTMLElement>(sel: string) => root.querySelector<T>(sel)!;
  const orb = $<HTMLButtonElement>('[data-ld-orb]');
  const label = $('[data-ld-label]');
  const status = $('[data-ld-status]');
  const timer = $('[data-ld-timer]');
  const signin = $('[data-ld-signin]');
  const endBtn = $<HTMLButtonElement>('[data-ld-end]');
  const error = $('[data-ld-error]');
  const log = $<HTMLOListElement>('[data-ld-log]');
  const after = $('[data-ld-after]');
  const again = $<HTMLButtonElement>('[data-ld-again]');
  const practiceForm = $<HTMLFormElement>('[data-ld-practice]');
  const saved = $('[data-ld-saved]');
  const audioBox = $('[data-ld-audio]');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let state: State = 'signed-out';
  let credential = '';
  let credentialAt = 0;
  let firstName = '';
  let room: Room | null = null;
  let audioCtx: AudioContext | null = null;
  let agentAnalyser: AnalyserNode | null = null;
  let micAnalyser: AnalyserNode | null = null;
  let raf = 0;
  let countdown = 0;
  let deadline = 0;
  let maxSeconds = 180;
  let soundBlocked = false;
  let joinTimer = 0;
  const segments = new Map<string, HTMLLIElement>();

  const setState = (next: State) => {
    state = next;
    root.dataset.state = next;
    label.textContent = soundBlocked && IN_CALL.includes(next) && next !== 'ending' ? 'Tap for sound' : LABEL[next];
    orb.disabled = next === 'signed-out' || next === 'connecting' || next === 'ending';
    orb.setAttribute('aria-label', next === 'ready' || next === 'ended' ? 'Start the demo call' : IN_CALL.includes(next) ? `Call in progress: ${LABEL[next]}` : LABEL[next]);
    endBtn.hidden = !IN_CALL.includes(next) || next === 'ending';
    signin.hidden = next !== 'signed-out';
    after.hidden = next !== 'ended';
  };

  const say = (text: string) => (status.textContent = text);
  const showError = (text: string) => {
    error.textContent = text;
    error.classList.toggle('hidden', !text);
  };

  // ── Google sign-in ────────────────────────────────────────────────────
  const onCredential = (c: string) => {
    credential = c;
    credentialAt = Date.now();
    firstName = givenName(c);
    showError('');
    setState('ready');
    say(`${firstName ? `Thanks, ${firstName}. ` : ''}Tap the button and say hello.`);
    orb.focus();
  };

  const setupSignIn = async () => {
    try {
      await loadScript(GIS_SRC);
    } catch {
      showError("Google sign-in didn't load. Check your connection or ad blocker, then reload the page.");
      return;
    }
    const gid = window.google!.accounts.id;
    gid.initialize({
      client_id: clientId,
      callback: (r: { credential?: string }) => r.credential && onCredential(r.credential),
      auto_select: false,
      cancel_on_tap_outside: true,
      context: 'signin',
      ux_mode: 'popup',
      itp_support: true,
      use_fedcm_for_button: true,
    });
    gid.renderButton(signin, { type: 'standard', theme: 'filled_black', size: 'large', text: 'continue_with', shape: 'rectangular', width: 260 });
  };

  const near = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        near.disconnect();
        void setupSignIn();
      }
    },
    { rootMargin: '600px 0px' },
  );
  near.observe(root);

  // ── Transcript ────────────────────────────────────────────────────────
  const addLine = (who: 'agent' | 'visitor') => {
    const li = document.createElement('li');
    li.className = 'live-line';
    li.dataset.who = who;
    const name = document.createElement('span');
    name.className = 'live-who';
    name.textContent = who === 'agent' ? 'Ava' : firstName || 'You';
    const text = document.createElement('span');
    text.className = 'live-text';
    li.append(name, text);
    log.append(li);
    log.hidden = false;
    return li;
  };
  const setLine = (li: HTMLLIElement, text: string) => {
    li.querySelector('.live-text')!.textContent = text;
    log.scrollTop = log.scrollHeight;
  };

  // ── Audio level → orb ─────────────────────────────────────────────────
  const analyser = (track: MediaStreamTrack): AnalyserNode | null => {
    if (!audioCtx) return null;
    try {
      const node = audioCtx.createAnalyser();
      node.fftSize = 512;
      audioCtx.createMediaStreamSource(new MediaStream([track])).connect(node);
      return node;
    } catch {
      return null;
    }
  };
  const buf = new Uint8Array(512);
  const level = (node: AnalyserNode | null) => {
    if (!node) return 0;
    node.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sum += v * v;
    }
    return Math.min(1, Math.sqrt(sum / buf.length) * 4);
  };
  let smooth = 0;
  const tick = () => {
    const target = state === 'speaking' ? level(agentAnalyser) : state === 'listening' ? level(micAnalyser) * 0.8 : 0;
    smooth += (target - smooth) * 0.35;
    root.style.setProperty('--lvl', smooth.toFixed(3));
    raf = requestAnimationFrame(tick);
  };

  // ── Countdown ─────────────────────────────────────────────────────────
  const startCountdown = (seconds: number) => {
    deadline = Date.now() + seconds * 1000;
    const step = () => {
      const left = Math.ceil((deadline - Date.now()) / 1000);
      timer.textContent = fmt(left);
      // The server ends the call at the cap; this is the browser's backstop.
      if (left < -5) void room?.disconnect();
    };
    step();
    countdown = window.setInterval(step, 250);
  };

  // ── Call ──────────────────────────────────────────────────────────────
  const cleanup = () => {
    cancelAnimationFrame(raf);
    window.clearInterval(countdown);
    window.clearTimeout(joinTimer);
    soundBlocked = false;
    root.style.setProperty('--lvl', '0');
    audioBox.replaceChildren();
    agentAnalyser = micAnalyser = null;
    segments.clear();
    room = null;
  };

  const ended = () => {
    if (state === 'ended') return;
    cleanup();
    timer.textContent = fmt(maxSeconds);
    setState('ended');
    say('Call ended. Tap the button to talk again.');
  };

  const fail = (text: string) => {
    cleanup();
    setState(credential ? 'ready' : 'signed-out');
    showError(text);
    say('');
  };

  const start = async () => {
    if (!credential || Date.now() - credentialAt > CREDENTIAL_MAX_AGE_MS) {
      credential = '';
      setState('signed-out');
      say('Please sign in with Google again.');
      return;
    }
    showError('');
    setState('connecting');
    say('Connecting you to the practice…');
    log.replaceChildren();
    log.hidden = true;
    saved.textContent = '';
    // Inside the tap: unlock audio before any await, or Safari keeps it muted.
    audioCtx ??= new AudioContext();
    void audioCtx.resume();

    let res: Response;
    try {
      res = await fetch(`${api}/public/web-demo/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential, region: currentRegion(), ...attribution() }),
      });
    } catch {
      return fail("Can't reach the demo right now. Please try again in a minute.");
    }
    const body = (await res.json().catch(() => ({}))) as Partial<StartResponse> & { detail?: string };
    if (res.status === 401) {
      credential = '';
      setState('signed-out');
      showError(body.detail || 'Please sign in with Google again.');
      return;
    }
    if (!res.ok || !body.token || !body.url) return fail(body.detail || "The demo isn't available right now. Try the sample calls below.");
    maxSeconds = body.max_seconds ?? maxSeconds;

    try {
      const { Room: LkRoom, RoomEvent, Track, ParticipantKind } = await import('livekit-client');
      const r = new LkRoom({ audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      room = r;

      r.on(RoomEvent.TrackSubscribed, (track: RemoteTrack, publication) => {
        if (track.kind !== Track.Kind.Audio) return;
        audioBox.append(track.attach());
        // The meter follows the agent's voice, not its background-sound track.
        if (!agentAnalyser || publication.trackName === AGENT_VOICE_TRACK) agentAnalyser = analyser(track.mediaStreamTrack);
      });
      // Autoplay rules can block the agent's audio even after the tap (Safari, iOS, muted sites).
      r.on(RoomEvent.AudioPlaybackStatusChanged, () => {
        if (room !== r) return;
        soundBlocked = !r.canPlaybackAudio;
        setState(state);
        if (soundBlocked) say('Your browser blocked the sound. Tap the button to hear Ava.');
      });
      r.on(RoomEvent.ParticipantAttributesChanged, (_changed, participant) => {
        if (participant === r.localParticipant) return;
        const next = AGENT_STATES[participant.attributes['lk.agent.state'] ?? ''];
        if (next && IN_CALL.includes(state) && state !== 'ending') {
          setState(next);
          say(next === 'speaking' ? 'Ava is speaking. Interrupt any time.' : next === 'thinking' ? 'Ava is thinking…' : 'Go ahead, Ava is listening.');
        }
      });
      r.on(RoomEvent.Disconnected, () => ended());
      r.registerTextStreamHandler('lk.transcription', async (reader, participant) => {
        const attrs = reader.info.attributes ?? {};
        const micSid = r.localParticipant.getTrackPublication(Track.Source.Microphone)?.trackSid;
        const visitor = participant.identity === r.localParticipant.identity
          || (!!micSid && attrs['lk.transcribed_track_id'] === micSid);
        const id = attrs['lk.segment_id'] || reader.info.id;
        const li = segments.get(id) ?? addLine(visitor ? 'visitor' : 'agent');
        segments.set(id, li);
        // Agent speech arrives word by word in one stream; the visitor's arrives as successive
        // streams that each carry the whole sentence so far. Either way: this stream's text wins.
        let text = '';
        for await (const chunk of reader) {
          text += chunk;
          setLine(li, text);
        }
      });

      await r.connect(body.url, body.token);
      try {
        await r.localParticipant.setMicrophoneEnabled(true);
      } catch (e) {
        await r.disconnect();
        const denied = e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
        return fail(denied ? 'Microphone access was blocked. Allow it in your browser’s address bar, then tap again.'
          : 'No microphone found. Plug one in, or play the sample calls below.');
      }
      try {
        await r.startAudio();
      } catch {
        // Not fatal: the orb asks for a tap (AudioPlaybackStatusChanged) and that tap retries.
      }
      soundBlocked = !r.canPlaybackAudio;
      const mic = r.localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.mediaStreamTrack;
      if (mic) micAnalyser = analyser(mic);
      // The agent may already be speaking its greeting; don't overwrite that.
      if (state === 'connecting') setState('listening');
      else setState(state);
      say(soundBlocked
        ? 'Your browser blocked the sound. Tap the button to hear Ava.'
        : `Connected to ${body.practice_name ?? 'the practice'}. Ava will answer in a moment.`);
      startCountdown(maxSeconds);
      // A worker that is down or busy would otherwise leave the visitor in silence for the whole call.
      joinTimer = window.setTimeout(() => {
        if (room !== r) return;
        const agentHere = [...r.remoteParticipants.values()].some((p) => p.kind === ParticipantKind.AGENT);
        if (agentHere) return;
        void r.disconnect();
        showError("The receptionist didn't pick up. Please try again in a minute, or play the sample calls below.");
      }, AGENT_JOIN_TIMEOUT_MS);
      if (!reduced) raf = requestAnimationFrame(tick);
    } catch {
      void room?.disconnect();
      fail("Couldn't start the call. Please try again, or play the sample calls below.");
    }
  };

  const stop = () => {
    if (!room) return ended();
    setState('ending');
    say('Ending the call…');
    void room.disconnect();
  };

  const unblockSound = () => {
    if (!room) return;
    void room.startAudio().then(() => {
      soundBlocked = !room?.canPlaybackAudio;
      setState(state);
      if (!soundBlocked) say('Sound on. Go ahead and talk to Ava.');
    }).catch(() => undefined);
  };

  orb.addEventListener('click', () => {
    if (soundBlocked && room) return unblockSound();
    if (state === 'ready' || state === 'ended') void start();
    else if (state === 'listening' || state === 'thinking' || state === 'speaking') stop();
  });
  endBtn.addEventListener('click', stop);
  again.addEventListener('click', () => void start());
  window.addEventListener('pagehide', () => void room?.disconnect());

  practiceForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = (new FormData(practiceForm).get('practice') ?? '').toString().trim();
    if (!name || !credential) return;
    saved.textContent = 'Saving…';
    try {
      const res = await fetch(`${api}/public/web-demo/lead`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential, practice_name: name }),
      });
      saved.textContent = res.ok ? 'Saved. Thanks!' : 'Couldn’t save that. Your sign-in may have expired.';
    } catch {
      saved.textContent = 'Couldn’t save that right now.';
    }
  });

  timer.textContent = fmt(maxSeconds);
  setState('signed-out');
}
