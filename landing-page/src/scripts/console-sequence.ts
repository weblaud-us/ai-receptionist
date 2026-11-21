/**
 * The page's one automatic motion: the hero console plays its call once (about 6 s).
 * Reduced motion → the finished state appears at once.
 */
const STEPS: [step: string, at: number][] = [
  ['1', 300],
  ['2', 1900],
  ['3', 4200],
  ['4', 5300],
];

export function initConsole() {
  const root = document.getElementById('call-console');
  if (!root) return;
  const wave = document.getElementById('console-wave');
  const timer = document.getElementById('console-timer');
  const show = (step: string) =>
    root.querySelectorAll<HTMLElement>(`[data-step="${step}"]`).forEach((el) => (el.dataset.shown = 'true'));

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    STEPS.forEach(([s]) => show(s));
    return;
  }

  const start = performance.now();
  const startSeconds = 31;
  let raf = 0;
  const tick = (now: number) => {
    const s = startSeconds + Math.floor((now - start) / 1000);
    if (timer) timer.textContent = `00:${String(Math.min(s, 59)).padStart(2, '0')}`;
    raf = requestAnimationFrame(tick);
  };

  if (wave) wave.dataset.live = 'true';
  raf = requestAnimationFrame(tick);
  STEPS.forEach(([s, at]) => window.setTimeout(() => show(s), at));
  window.setTimeout(() => {
    if (wave) wave.dataset.live = 'false';
    cancelAnimationFrame(raf);
  }, 4200);
}
