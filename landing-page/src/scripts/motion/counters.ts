/**
 * Rolling numbers: every number inside a `[data-count]` element counts up from zero the first time
 * it scrolls into view, keeping its prefix, suffix, separators and decimals. The original text is
 * restored at the end.
 */
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

const NUM = /^([\s\S]*?)(\d[\d,]*(?:\.\d+)?)([\s\S]*)$/;

export function initCounters() {
  document.querySelectorAll<HTMLElement>('[data-count]').forEach((el) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    while (walker.nextNode()) if (/\d/.test(walker.currentNode.nodeValue ?? '')) nodes.push(walker.currentNode as Text);

    const runs = nodes.flatMap((node) => {
      const original = node.nodeValue ?? '';
      const m = original.match(NUM);
      if (!m) return [];
      const [, pre, num, post] = m;
      const target = parseFloat(num.replace(/,/g, ''));
      const decimals = (num.split('.')[1] ?? '').length;
      const grouped = num.includes(',');
      const fmt = (v: number) => {
        const s = grouped
          ? v.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
          : v.toFixed(decimals);
        return pre + s + post;
      };
      node.nodeValue = fmt(0);
      return [{ node, original, target, fmt }];
    });
    if (!runs.length) return;

    ScrollTrigger.create({
      trigger: el,
      start: 'top 88%',
      once: true,
      onEnter: () =>
        runs.forEach(({ node, original, target, fmt }) => {
          const o = { v: 0 };
          gsap.to(o, {
            v: target,
            duration: 1.6,
            ease: 'power3.out',
            onUpdate: () => (node.nodeValue = fmt(o.v)),
            onComplete: () => (node.nodeValue = original),
          });
        }),
    });
  });
}
