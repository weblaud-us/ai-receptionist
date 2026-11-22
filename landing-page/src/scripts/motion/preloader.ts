/**
 * Short preloader (about 1.2 s): the wordmark rises, a 000→100 counter runs, then the panel lifts.
 * `onLift` fires as the panel starts moving so the hero can enter underneath it. Skipped (and
 * `onLift` called at once) when region-init.js set `no-preload`.
 */
import { gsap } from 'gsap';

export function runPreloader(onLift: () => void) {
  const root = document.documentElement;
  const el = document.querySelector<HTMLElement>('.preloader');
  if (!el || root.classList.contains('no-preload') || root.classList.contains('loaded')) {
    onLift();
    root.classList.add('ready', 'loaded');
    return;
  }
  const count = el.querySelector<HTMLElement>('[data-loader-count]');
  const bar = el.querySelector<HTMLElement>('[data-loader-bar]');
  const words = el.querySelectorAll<HTMLElement>('[data-loader-text]');
  const o = { v: 0 };

  gsap
    .timeline({
      onComplete: () => {
        root.classList.add('loaded');
        try {
          sessionStorage.setItem('fd-seen', '1');
        } catch {
          /* storage blocked: the preloader shows again next time */
        }
      },
    })
    .to(words, { y: 0, duration: 0.6, ease: 'expo.out', stagger: 0.05 }, 0)
    .to(
      o,
      {
        v: 100,
        duration: 0.95,
        ease: 'power2.inOut',
        onUpdate: () => {
          if (count) count.textContent = String(Math.round(o.v)).padStart(3, '0');
          if (bar) bar.style.transform = `scaleX(${o.v / 100})`;
        },
      },
      0.05,
    )
    .to(words, { y: '-110%', duration: 0.4, ease: 'expo.in' }, '>-0.05')
    .call(() => {
      onLift();
      root.classList.add('ready');
    })
    .to(el, { yPercent: -100, duration: 0.85, ease: 'expo.inOut' }, '<');
}
