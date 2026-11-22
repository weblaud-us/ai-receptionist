/**
 * The hero console plays its call once (about 6 s), starting as the preloader lifts: steps light in
 * order and the progress readout climbs 0 → 100 %. Reduced motion → the finished state at once.
 */
import { onReady } from './ready';

const STEPS: [step: string, at: number][] = [
  ['1', 500],
  ['2', 2100],
  ['3', 4300],
  ['4', 5400],
];

export function initConsole() {
  const root = document.getElementById('hero-stage');
  if (!root) return;
  const wave = document.getElementById('console-wave');
  const timer = document.getElementById('console-timer');
  const progress = document.getElementById('console-progress');
  const bar = document.getElementById('console-bar');
  const show = (step: string) =>
    root.querySelectorAll<HTMLElement>(`[data-step="${step}"]`).forEach((el) => (el.dataset.shown = 'true'));

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    STEPS.forEach(([s]) => show(s));
    return;
  }

  // Progress eases towards a target percentage set by each step.
  let shownPct = 0;
  let targetPct = 0;
  const setPct = (v: number) => {
    if (progress) progress.textContent = String(Math.round(v)).padStart(2, '0');
    if (bar) bar.style.transform = `scaleX(${v / 100})`;
  };
  setPct(0);

  onReady(() => {
    const start = performance.now();
    const startSeconds = 31;
    let raf = 0;
    const tick = (now: number) => {
      const s = startSeconds + Math.floor((now - start) / 1000);
      if (timer) timer.textContent = `00:${String(Math.min(s, 59)).padStart(2, '0')}`;
      shownPct += (targetPct - shownPct) * 0.06;
      setPct(targetPct - shownPct < 0.4 ? targetPct : shownPct);
      raf = requestAnimationFrame(tick);
    };

    if (wave) wave.dataset.live = 'true';
    raf = requestAnimationFrame(tick);
    STEPS.forEach(([s, at], i) =>
      window.setTimeout(() => {
        show(s);
        targetPct = (i + 1) * 25;
      }, at),
    );
    window.setTimeout(() => {
      if (wave) wave.dataset.live = 'false';
    }, 4300);
    window.setTimeout(() => {
      cancelAnimationFrame(raf);
      setPct(100);
    }, 7500);
  });
}
