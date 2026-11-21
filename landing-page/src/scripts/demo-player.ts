/**
 * Sample-call tabs and players.
 * - Tabs follow the ARIA tabs pattern: arrow keys / Home / End move and select; Tab leaves.
 * - Each player plays a real file when `data-audio` is set, otherwise a simulated clock that
 *   reveals the transcript in step. The full transcript is always readable; playback only dims
 *   lines that haven't been "spoken" yet.
 */
interface Player {
  stop: () => void;
}

const players = new Set<Player>();
const stopAll = (except?: Player) => players.forEach((p) => p !== except && p.stop());
const fmt = (s: number) => `00:${String(Math.floor(s)).padStart(2, '0')}`;

function setupPlayer(root: HTMLElement): Player {
  const button = root.querySelector<HTMLButtonElement>('[data-play]')!;
  const wave = root.querySelector<HTMLElement>('.wave');
  const time = root.querySelector<HTMLElement>('[data-time]');
  const audio = root.querySelector<HTMLAudioElement>('audio');
  const lines = Array.from(root.querySelectorAll<HTMLElement>('.line'));
  const duration = Number(root.dataset.duration) || 20;
  const playIcon = root.querySelector('[data-icon-play]');
  const pauseIcon = root.querySelector('[data-icon-pause]');
  const label = button.getAttribute('aria-label') ?? 'Play';

  let raf = 0;
  let startedAt = 0;
  let offset = 0;
  let playing = false;

  const render = (t: number) => {
    if (time) time.textContent = `${fmt(Math.min(t, duration))} / ${fmt(duration)}`;
    let current = -1;
    lines.forEach((l, i) => {
      if (Number(l.dataset.at) <= t) current = i;
    });
    lines.forEach((l, i) => (l.dataset.state = i < current ? 'past' : i === current ? 'current' : 'future'));
  };
  const clearStates = () => lines.forEach((l) => delete l.dataset.state);

  const setPlaying = (on: boolean) => {
    playing = on;
    if (wave) wave.dataset.live = String(on);
    playIcon?.classList.toggle('hidden', on);
    pauseIcon?.classList.toggle('hidden', !on);
    button.setAttribute('aria-label', on ? label.replace(/^Play/, 'Pause') : label);
  };

  const elapsed = () => (audio ? audio.currentTime : offset + (performance.now() - startedAt) / 1000);

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
    window.setTimeout(() => !playing && clearStates(), 1200);
  };

  const play = () => {
    stopAll(api);
    setPlaying(true);
    if (audio) {
      audio.play().catch(() => setPlaying(false));
      audio.onended = finish;
    } else {
      startedAt = performance.now();
    }
    raf = requestAnimationFrame(loop);
  };

  const pause = () => {
    cancelAnimationFrame(raf);
    if (audio) audio.pause();
    else offset = elapsed();
    setPlaying(false);
  };

  const api: Player = {
    stop: () => {
      if (!playing && offset === 0) return;
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
      const keys: Record<string, number> = { ArrowDown: i + 1, ArrowRight: i + 1, ArrowUp: i - 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 };
      if (!(e.key in keys)) return;
      e.preventDefault();
      select(tabs[(keys[e.key] + tabs.length) % tabs.length]);
    });
  });

  document.querySelectorAll<HTMLElement>('[data-player]').forEach(setupPlayer);
  document.addEventListener('regionchange', () => stopAll());
}
