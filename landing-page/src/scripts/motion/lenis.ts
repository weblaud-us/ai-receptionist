/**
 * Smooth scroll (Lenis) driven by GSAP's ticker so ScrollTrigger and the scroll stay in one frame.
 * In-page anchors go through Lenis, which applies the CSS scroll-padding for the fixed header. The mobile menu locks
 * scrolling with a `scrolllock` event (see nav.ts).
 */
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import Lenis from 'lenis';

export function initLenis(): Lenis {
  const lenis = new Lenis({
    duration: 1.15,
    easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
    smoothWheel: true,
  });
  lenis.on('scroll', ScrollTrigger.update);
  gsap.ticker.add((time) => lenis.raf(time * 1000));
  gsap.ticker.lagSmoothing(0);

  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const link = (e.target as Element | null)?.closest<HTMLAnchorElement>('a[href^="#"]');
    if (!link) return;
    const hash = link.getAttribute('href') ?? '';
    // The skip link keeps the browser's own focus behaviour.
    if (hash.length < 2 || hash === '#main') return;
    const target = hash === '#top' ? 0 : document.getElementById(decodeURIComponent(hash.slice(1)));
    if (target === null) return;
    e.preventDefault();
    // Lenis honours the page's scroll-padding-top (the fixed header's height), as native anchors do.
    lenis.scrollTo(target, { duration: 1.4 });
    history.pushState(null, '', hash);
  });

  document.addEventListener('scrolllock', (e) => ((e as CustomEvent<boolean>).detail ? lenis.stop() : lenis.start()));
  return lenis;
}
