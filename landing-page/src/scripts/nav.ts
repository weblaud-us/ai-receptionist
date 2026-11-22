/**
 * Header behaviour.
 * - Mobile menu: disclosure with Esc to close, focus kept inside while open, focus returned on close.
 * - Tone: the header matches the band under it (`data-tone` on each <section>), and gains its glass
 *   background once the page has scrolled.
 */
function initTone(header: HTMLElement) {
  const bands = Array.from(document.querySelectorAll<HTMLElement>('[data-tone]')).filter((el) => el !== header);
  let queued = false;
  const update = () => {
    queued = false;
    const probe = header.offsetHeight / 2;
    // Later bands paint over earlier ones (a band can slide up over the one before), so the last match wins.
    const band = [...bands].reverse().find((b) => {
      const r = b.getBoundingClientRect();
      return r.top <= probe && r.bottom > probe;
    });
    header.dataset.tone = band?.dataset.tone ?? 'dark';
    header.dataset.scrolled = String(window.scrollY > 8);
  };
  const onScroll = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(update);
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
  update();
}

export function initNav() {
  const header = document.getElementById('site-header');
  if (header) initTone(header);

  const button = document.getElementById('menu-button');
  const menu = document.getElementById('mobile-menu');
  if (!button || !menu) return;

  const focusables = () =>
    Array.from(menu.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')).filter((el) => el.offsetParent !== null);

  const lockScroll = (on: boolean) => document.dispatchEvent(new CustomEvent<boolean>('scrolllock', { detail: on }));
  const open = () => {
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    lockScroll(true);
    document.addEventListener('keydown', onKey);
    focusables()[0]?.focus();
  };
  const close = (returnFocus = true) => {
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    lockScroll(false);
    document.removeEventListener('keydown', onKey);
    if (returnFocus) button.focus();
  };
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      close();
      return;
    }
    if (e.key !== 'Tab') return;
    const items = [button as HTMLElement, ...focusables()];
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  button.addEventListener('click', () => (menu.hidden ? open() : close()));
  menu.querySelectorAll('[data-menu-link]').forEach((a) => a.addEventListener('click', () => close(false)));
  window.matchMedia('(min-width: 1280px)').addEventListener('change', (e) => {
    if (e.matches && !menu.hidden) close(false);
  });
}
