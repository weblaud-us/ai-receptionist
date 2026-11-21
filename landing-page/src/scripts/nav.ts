/** Mobile menu: disclosure with Esc to close, focus kept inside while open, focus returned on close. */
export function initNav() {
  const button = document.getElementById('menu-button');
  const menu = document.getElementById('mobile-menu');
  if (!button || !menu) return;

  const focusables = () =>
    Array.from(menu.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')).filter((el) => el.offsetParent !== null);

  const open = () => {
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    document.addEventListener('keydown', onKey);
    focusables()[0]?.focus();
  };
  const close = (returnFocus = true) => {
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
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
  window.matchMedia('(min-width: 1024px)').addEventListener('change', (e) => {
    if (e.matches && !menu.hidden) close(false);
  });
}
