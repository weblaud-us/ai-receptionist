/**
 * Headline line reveals (masked lines rising into place) and fade-ups for `[data-reveal]`.
 * Splits happen at reveal time, so the active region's text is the one measured, and are reverted
 * afterwards so the DOM goes back to plain text.
 */
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { SplitText } from 'gsap/SplitText';

function riseLines(el: HTMLElement, delay = 0) {
  const split = SplitText.create(el, { type: 'lines', mask: 'lines', aria: 'none' });
  gsap.set(el, { autoAlpha: 1 });
  gsap.from(split.lines, {
    yPercent: 110,
    duration: 1.2,
    ease: 'expo.out',
    stagger: 0.08,
    delay,
    onComplete: () => split.revert(),
  });
}

/** The hero's entrance, played as the preloader lifts. */
export function heroIntro() {
  const title = document.querySelector<HTMLElement>('.hero-title');
  if (title) riseLines(title, 0.05);
  gsap.from('[data-hero-fade]', { y: 32, autoAlpha: 0, duration: 1.2, ease: 'expo.out', stagger: 0.09, delay: 0.3 });
}

export function initReveals() {
  document.querySelectorAll<HTMLElement>('main .h2, main .display:not(.hero-title)').forEach((el) => {
    gsap.set(el, { autoAlpha: 0 });
    ScrollTrigger.create({ trigger: el, start: 'top 88%', once: true, onEnter: () => riseLines(el) });
  });
  gsap.utils.toArray<HTMLElement>('[data-reveal]').forEach((el) => {
    gsap.from(el, { y: 40, autoAlpha: 0, duration: 1.1, ease: 'expo.out', scrollTrigger: { trigger: el, start: 'top 90%', once: true } });
  });
}
