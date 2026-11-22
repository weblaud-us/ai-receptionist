/**
 * Sample-call tabs and players.
 * - Tabs follow the ARIA tabs pattern: arrow keys / Home / End move and select; Tab leaves.
 * - Each player plays a real file when `data-audio` is set, otherwise a simulated clock. The
 *   transcript and the agent trace advance in step; the seek bar (a range input over the waveform)
 *   scrubs both. Everything stays readable without playback: it only dims what hasn't happened yet.
 */
interface Player {
  stop: () => void;
}

const players = new Set<Player>();
const stopAll = (except?: Player) => players.forEach((p) => p !== except && p.stop());
const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function setupPlayer(root: HTMLElement): Player {
  const button = root.querySelector<HTMLButtonElement>('[data-play]')!;
  const baseWave = root.querySelector<HTMLElement>('.demo-scrub .wave');
  const time = root.querySelector<HTMLElement>('[data-time]');
  const seek = root.querySelector<HTMLInputElement>('[data-seek]');
  const audio = root.querySelector<HTMLAudioElement>('audio');
  const scroller = root.querySelector<HTMLElement>('[data-transcript]');
  const traceState = root.querySelector<HTMLElement>('[data-trace-state]');
  const lines = Array.from(root.querySelectorAll<HTMLElement>('.line'));
  const rows = Array.from(root.querySelectorAll<HTMLElement>('.trace-row'));
  const duration = Number(root.dataset.duration) || 20;
  const playIcon = root.querySelector('[data-icon-play]');
  const pauseIcon = root.querySelector('[data-icon-pause]');
  const label = button.getAttribute('aria-label') ?? 'Play';

  let raf = 0;
  let startedAt = 0;
  let offset = 0;
  let playing = false;
  let lastCurrent = -1;

  const mark = (els: HTMLElement[], t: number) => {
    let current = -1;
    els.forEach((el, i) => {
      if (Number(el.dataset.at) <= t) current = i;
    });
    els.forEach((el, i) => (el.dataset.state = i < current ? 'past' : i === current ? 'current' : 'future'));
    return current;
  };

  const render = (t: number) => {
    const clamped = Math.min(Math.max(t, 0), duration);
    root.style.setProperty('--p', `${((clamped / duration) * 100).toFixed(2)}%`);
    if (time) time.textContent = `${fmt(clamped)} / ${fmt(duration)}`;
    if (seek) {
      seek.value = String(clamped);
      seek.setAttribute('aria-valuetext', `${Math.round(clamped)} seconds of ${duration}`);
    }
    const current = mark(lines, clamped);
    mark(rows, clamped);
    // Keep the spoken line in view inside the transcript box (never scrolls the page).
    if (scroller && current !== lastCurrent && current >= 0) {
      const line = lines[current];
      const top = line.offsetTop - scroller.offsetTop;
      if (top < scroller.scrollTop || top + line.offsetHeight > scroller.scrollTop + scroller.clientHeight) {
        scroller.scrollTo({ top: Math.max(0, top - 12), behavior: 'smooth' });
      }
    }
    lastCurrent = current;
  };

  const clearStates = () => {
    [...lines, ...rows].forEach((el) => delete el.dataset.state);
    delete root.dataset.playing;
    root.style.setProperty('--p', '0%');
    if (seek) seek.value = '0';
    if (traceState) traceState.textContent = 'idle';
    lastCurrent = -1;
  };

  const setPlaying = (on: boolean) => {
    playing = on;
    if (on) root.dataset.playing = 'true';
    if (baseWave) baseWave.dataset.live = String(on);
    playIcon?.classList.toggle('hidden', on);
    pauseIcon?.classList.toggle('hidden', !on);
    button.setAttribute('aria-label', on ? label.replace(/^Play/, 'Pause') : label);
    if (traceState) traceState.textContent = on ? 'live' : 'paused';
  };

  const elapsed = () => (audio ? audio.currentTime : offset + (playing ? (performance.now() - startedAt) / 1000 : 0));

  const loop = () => {
    const t = elapsed();
    render(t);
    if (t >= duration && !audio) {
      finish();
      return;
    }
    raf = requestAnimationFrame(loop);
  };

  const finish = () => {
    cancelAnimationFrame(raf);
    setPlaying(false);
    offset = 0;
    render(duration);
    if (traceState) traceState.textContent = 'done';
    window.setTimeout(() => !playing && clearStates(), 1600);
  };

  const play = () => {
    stopAll(api);
    if (offset >= duration) offset = 0;
    setPlaying(true);
    if (audio) {
      if (offset) audio.currentTime = offset;
      audio.play().catch(() => setPlaying(false));
      audio.onended = finish;
    } else {
      startedAt = performance.now();
    }
    raf = requestAnimationFrame(loop);
  };

  const pause = () => {
    cancelAnimationFrame(raf);
    offset = elapsed();
    if (audio) audio.pause();
    setPlaying(false);
  };

  seek?.addEventListener('input', () => {
    offset = Number(seek.value);
    root.dataset.playing = 'true';
    if (audio) audio.currentTime = offset;
    if (playing) startedAt = performance.now();
    else if (traceState) traceState.textContent = 'paused';
    render(offset);
  });

  const api: Player = {
    stop: () => {
      if (!playing && offset === 0 && !root.dataset.playing) return;
      cancelAnimationFrame(raf);
      if (audio) {
        audio.pause();
        audio.currentTime = 0;
      }
      offset = 0;
      setPlaying(false);
      clearStates();
      if (time) time.textContent = `${fmt(0)} / ${fmt(duration)}`;
    },
  };
  players.add(api);

  button.addEventListener('click', () => (playing ? pause() : play()));
  return api;
}

export function initDemo() {
  const tablist = document.querySelector<HTMLElement>('[data-demo-tabs]');
  if (!tablist) return;
  const tabs = Array.from(tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]'));

  const select = (tab: HTMLButtonElement, focus = true) => {
    stopAll();
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(t.getAttribute('aria-controls') ?? '');
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
  };

  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => select(tab, false));
    tab.addEventListener('keydown', (e) => {
      const keys: Record<string, number> = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: tabs.length - 1 };
      if (!(e.key in keys)) return;
      e.preventDefault();
      select(tabs[(keys[e.key] + tabs.length) % tabs.length]);
    });
  });

  document.querySelectorAll<HTMLElement>('[data-player]').forEach(setupPlayer);
  document.addEventListener('regionchange', () => stopAll());
}
