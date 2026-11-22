/**
 * Scroll scenes (desktop):
 * - `[data-scene]` bands are tall (`--scene-h`) with a sticky `[data-pin]` child (CSS, html.scenes).
 * - `[data-steps]` lights its `[data-scene-item]` children in order as the band scrolls (`data-on`).
 * - `[data-hold]` bands stick at their end while the next `[data-slide-over]` band rises over them.
 * Each band also gets `--p` (0–1) for any CSS that wants the progress.
 */
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

const DESKTOP = '(min-width: 1025px) and (min-height: 680px)';

function lightSteps(group: HTMLElement, desktop: boolean) {
  const items = Array.from(group.querySelectorAll<HTMLElement>('[data-scene-item]'));
  if (!items.length) return;
  const pinned = desktop && group.closest('[data-scene]');
  const set = (p: number) => {
    group.style.setProperty('--p', p.toFixed(3));
    const lit = Math.max(1, Math.ceil(p * 1.15 * items.length));
    items.forEach((el, i) => (el.dataset.on = String(i < lit)));
  };
  set(0);
  return ScrollTrigger.create({
    trigger: pinned ? group.closest<HTMLElement>('[data-scene]')! : group,
    start: pinned ? 'top top' : 'top 78%',
    end: pinned ? 'bottom bottom' : 'bottom 45%',
    onUpdate: (s) => set(s.progress),
    onRefresh: (s) => set(s.progress),
  });
}

export function initScenes() {
  document.documentElement.classList.add('scenes');
  const mm = gsap.matchMedia();

  mm.add({ desktop: DESKTOP, mobile: `not all and ${DESKTOP}` }, (ctx) => {
    const desktop = Boolean(ctx.conditions?.desktop);
    document.querySelectorAll<HTMLElement>('[data-steps]').forEach((g) => lightSteps(g, desktop));

    if (desktop) {
      // A holding band sticks once its bottom meets the viewport bottom.
      const holds = Array.from(document.querySelectorAll<HTMLElement>('[data-hold] > [data-pin]'));
      const place = () => holds.forEach((el) => (el.style.top = `${Math.min(0, window.innerHeight - el.offsetHeight)}px`));
      place();
      ScrollTrigger.addEventListener('refreshInit', place);
      return () => {
        ScrollTrigger.removeEventListener('refreshInit', place);
        holds.forEach((el) => (el.style.top = ''));
      };
    }
    return undefined;
  });

  document.fonts?.ready.then(() => ScrollTrigger.refresh());
  window.addEventListener('load', () => ScrollTrigger.refresh(), { once: true });
}

/** Desktop footer reveal: the footer is fixed underneath and the page scrolls off it. */
export function initFooterReveal(onChange?: () => void) {
  const footer = document.querySelector<HTMLElement>('.site-footer');
  if (!footer) return;
  const root = document.documentElement;
  const mq = window.matchMedia('(min-width: 768px)');
  let last = '';
  const update = () => {
    const h = footer.offsetHeight;
    const on = mq.matches && h < window.innerHeight * 0.95;
    const key = `${on}:${h}`;
    if (key === last) return;
    last = key;
    root.classList.toggle('footer-fixed', on);
    root.style.setProperty('--footer-h', on ? `${h}px` : '0px');
    onChange?.();
  };
  update();
  new ResizeObserver(update).observe(footer);
  window.addEventListener('resize', update, { passive: true });
}
