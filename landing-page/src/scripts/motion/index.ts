/**
 * The motion layer, modelled on the Ordin template: Lenis smooth scroll, GSAP ScrollTrigger scenes,
 * the WebGL orb, text scramble, rolling numbers, headline reveals and a short preloader.
 * Reduced motion → none of it (the footer reveal still applies; it is layout, not motion).
 */
import { gsap } from 'gsap';
import { ScrambleTextPlugin } from 'gsap/ScrambleTextPlugin';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { SplitText } from 'gsap/SplitText';
import { initCounters } from './counters';
import { initLenis } from './lenis';
import { initOrb } from './orb';
import { runPreloader } from './preloader';
import { heroIntro, initReveals } from './reveal';
import { initFooterReveal, initScenes } from './scenes';
import { initScramble } from './scramble';

export function initMotion() {
  const root = document.documentElement;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (reduce) {
    initFooterReveal();
    root.classList.add('ready', 'loaded');
    return;
  }

  gsap.registerPlugin(ScrollTrigger, SplitText, ScrambleTextPlugin);
  root.classList.add('motion');
  initLenis();
  initFooterReveal(() => ScrollTrigger.refresh());
  initScenes();
  initScramble();
  initCounters();
  initReveals();
  runPreloader(heroIntro);

  const orb = () => initOrb();
  if (document.readyState === 'complete') orb();
  else window.addEventListener('load', orb, { once: true });
}
