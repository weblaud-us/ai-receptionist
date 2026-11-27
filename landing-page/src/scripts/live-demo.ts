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
 * - "What Ava is doing" is the agent's `laudline.activity` text stream (AI-receptionist-1/agent/activity.py):
 *   one JSON event per tool step, updated in place by id (running → done / failed / alert).
 * - "Play the patient" cards put the line to say under the orb; before a call they also start one
 *   (or, signed out, point at the Google button).
 * - The microphone opens before the demo session starts (mic.ts), so a busy or blocked microphone never
 *   spends a session or sends Ava into an empty room.
 */
import type { LocalAudioTrack, RemoteTrack, Room } from 'livekit-client';
import { acquireMic, micMessage, stopTrack } from './mic';
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
  prompt(): void;
}

/** One step from agent/activity.py. */
interface Activity {
  id: string;
  label: string;
  status: 'running' | 'done' | 'failed' | 'alert';
  detail?: string;
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
  const cue = $('[data-ld-cue]');
  const panes = $('[data-ld-panes]');
  const acts = $<HTMLOListElement>('[data-ld-acts]');
  const roles = Array.from(root.querySelectorAll<HTMLButtonElement>('[data-ld-role]'));
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let state: State = 'signed-out';
  let credential = '';
  let credentialAt = 0;
  let firstName = '';
  let room: Room | null = null;
  let mic: LocalAudioTrack | MediaStreamTrack | null = null;
  let audioCtx: AudioContext | null = null;
  let agentAnalyser: AnalyserNode | null = null;
  let micAnalyser: AnalyserNode | null = null;
  let raf = 0;
  let countdown = 0;
  let deadline = 0;
  let maxSeconds = 180;
  let soundBlocked = false;
  let joinTimer = 0;
  let signInReady = false;
  const segments = new Map<string, HTMLLIElement>();
  const steps = new Map<string, HTMLLIElement>();

  const setState = (next: State) => {
    state = next;
    root.dataset.state = next;
    label.textContent = soundBlocked && IN_CALL.includes(next) && next !== 'ending' ? 'Tap for sound' : LABEL[next];
    // Signed out, the orb still works: it points at the Google button rather than doing nothing.
    orb.disabled = next === 'connecting' || next === 'ending';
    orb.setAttribute('aria-label', next === 'ready' || next === 'ended' ? 'Start the demo call'
      : next === 'signed-out' ? 'Sign in with Google to talk' : IN_CALL.includes(next) ? `Call in progress: ${LABEL[next]}` : LABEL[next]);
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
    signInReady = true;
  };

  /** Signed out and the visitor tapped the orb or a role: show where to sign in (and offer One Tap). */
  const nudgeSignIn = () => {
    say('Continue with Google just below to start talking.');
    signin.dataset.nudge = 'false';
    void signin.offsetWidth; // restart the animation
    signin.dataset.nudge = 'true';
    signin.scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' });
    if (signInReady) {
      try {
        window.google?.accounts.id.prompt();
      } catch {
        /* the button is still there */
      }
    } else void setupSignIn();
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

  // ── What Ava is doing ─────────────────────────────────────────────────
  const resetSteps = () => {
    steps.clear();
    const li = document.createElement('li');
    li.className = 'live-act';
    li.dataset.status = 'idle';
    const icon = document.createElement('span');
    icon.className = 'live-act-icon';
    icon.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span');
    text.textContent = 'Listening for what you need…';
    li.append(icon, text);
    acts.replaceChildren(li);
  };
  const onActivity = (ev: Activity) => {
    if (!ev?.id || !ev.label) return;
    acts.querySelector('[data-status="idle"]')?.remove();
    let li = steps.get(ev.id);
    if (!li) {
      li = document.createElement('li');
      li.className = 'live-act';
      const icon = document.createElement('span');
      icon.className = 'live-act-icon';
      icon.setAttribute('aria-hidden', 'true');
      li.append(icon, document.createElement('span'));
      acts.append(li);
      steps.set(ev.id, li);
    }
    li.dataset.status = ev.status || 'running';
    const body = li.lastElementChild!;
    body.textContent = ev.label;
    if (ev.detail) {
      const d = document.createElement('span');
      d.className = 'live-act-detail';
      d.textContent = ev.detail;
      body.append(d);
    }
    acts.scrollTop = acts.scrollHeight;
  };

  // ── Play the patient ──────────────────────────────────────────────────
  const pickRole = (btn: HTMLButtonElement | null) => {
    roles.forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
    const line = btn?.querySelector(`.live-role-say .r-${currentRegion()}`)?.textContent?.trim()
      ?? btn?.querySelector('.live-role-say')?.textContent?.trim() ?? '';
    cue.textContent = line.replace(/^“|”$/g, '');
    cue.hidden = !btn || !line;
  };
  roles.forEach((b) => b.setAttribute('aria-pressed', 'false'));

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
    stopTrack(mic);
    mic = null;
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
    resetSteps();
    saved.textContent = '';
    // Inside the tap: unlock audio before any await, or Safari keeps it muted.
    audioCtx ??= new AudioContext();
    void audioCtx.resume();

    // The microphone first: if it can't open, don't spend a demo session (or send Ava into an empty room).
    say('Opening your microphone…');
    try {
      mic = await acquireMic();
    } catch (e) {
      return fail(micMessage(e));
    }
    say('Connecting you to the practice…');

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
      r.registerTextStreamHandler('laudline.activity', async (reader) => {
        try {
          onActivity(JSON.parse(await reader.readAll()) as Activity);
        } catch {
          /* a malformed event is skipped, never fatal */
        }
      });
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
      await r.localParticipant.publishTrack(mic!, { source: Track.Source.Microphone });
      try {
        await r.startAudio();
      } catch {
        // Not fatal: the orb asks for a tap (AudioPlaybackStatusChanged) and that tap retries.
      }
      soundBlocked = !r.canPlaybackAudio;
      const micMst = r.localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.mediaStreamTrack;
      if (micMst) micAnalyser = analyser(micMst);
      // The agent may already be speaking its greeting; don't overwrite that.
      if (state === 'connecting') setState('listening');
      else setState(state);
      panes.hidden = false;
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
      const r = room;
      room = null;   // so its Disconnected event doesn't show "Call ended" over the error
      r?.removeAllListeners();
      void r?.disconnect();
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
    if (state === 'signed-out') return nudgeSignIn();
    if (soundBlocked && room) return unblockSound();
    if (state === 'ready' || state === 'ended') void start();
    else if (state === 'listening' || state === 'thinking' || state === 'speaking') stop();
  });
  endBtn.addEventListener('click', stop);
  roles.forEach((b) =>
    b.addEventListener('click', () => {
      pickRole(b);
      if (state === 'signed-out') nudgeSignIn();
      else if (state === 'ready' || state === 'ended') void start();
    }),
  );
  document.addEventListener('regionchange', () => pickRole(roles.find((b) => b.getAttribute('aria-pressed') === 'true') ?? null));
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
