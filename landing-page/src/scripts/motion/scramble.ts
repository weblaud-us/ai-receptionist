/**
 * Ordin's text scramble on hover/focus for `[data-scramble]` labels (nav links, buttons).
 * Screen readers get the real text from a visually hidden copy; the scrambling copy is aria-hidden.
 * Labels with nested markup (e.g. regional spans) are left alone.
 */
import { gsap } from 'gsap';

const CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789';

export function initScramble() {
  const targets: HTMLElement[] = [];
  document.querySelectorAll<HTMLElement>('[data-scramble]').forEach((el) => {
    if (el.children.length) return;
    const text = el.textContent?.trim() ?? '';
    if (!text) return;
    const sr = document.createElement('span');
    sr.className = 'sr-only';
    sr.textContent = text;
    const shown = document.createElement('span');
    shown.setAttribute('aria-hidden', 'true');
    shown.style.display = 'inline-block';
    shown.style.whiteSpace = 'nowrap';
    shown.textContent = text;
    el.textContent = '';
    el.append(sr, shown);
    targets.push(shown);

    const host = el.closest<HTMLElement>('a, button') ?? el;
    let tween: gsap.core.Tween | undefined;
    const run = () => {
      tween?.kill();
      tween = gsap.to(shown, {
        duration: Math.min(0.9, 0.3 + text.length * 0.025),
        ease: 'none',
        scrambleText: { text, chars: CHARS, speed: 0.8, revealDelay: 0.05 },
      });
    };
    host.addEventListener('pointerenter', run);
    host.addEventListener('focus', run);
  });

  // Hold each label's width so scrambled glyphs don't make buttons jitter.
  const lock = () => targets.forEach((s) => {
    s.style.minWidth = '';
    s.style.minWidth = `${s.offsetWidth}px`;
  });
  document.fonts?.ready.then(lock);
}
