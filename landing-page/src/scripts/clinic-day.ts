/**
 * 24-hour scrubber for "Built around real clinic hours".
 * The tabs are the real control (ARIA tabs: arrows / Home / End). Pointing at, dragging across or
 * tapping the bar selects whichever phase that hour falls in and moves the needle.
 */
const DAY_START = 7;

const hhmm = (h: number) => {
  const total = Math.round(((h % 24) + 24) % 24 * 60);
  return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
};

export function initClinicDay() {
  const root = document.querySelector<HTMLElement>('[data-clinic-day]');
  if (!root) return;
  const tabs = Array.from(root.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
  const bar = root.querySelector<HTMLElement>('[data-scrub-bar]');
  const needle = root.querySelector<HTMLElement>('[data-scrub-needle]');
  const readout = root.querySelector<HTMLElement>('[data-scrub-time]');
  const segs = Array.from(root.querySelectorAll<HTMLElement>('[data-seg]'));
  let active = tabs.find((t) => t.getAttribute('aria-selected') === 'true') ?? tabs[0];

  const hourAt = (fraction: number) => DAY_START + fraction * 24;
  const fractionOf = (h: number) => (((h - DAY_START + 24) % 24) / 24);

  const placeNeedle = (h: number) => {
    if (needle) needle.style.left = `${(fractionOf(h) * 100).toFixed(2)}%`;
    if (readout) readout.textContent = hhmm(h);
  };

  const select = (tab: HTMLButtonElement, { focus = false, needleAt }: { focus?: boolean; needleAt?: number } = {}) => {
    if (tab !== active) {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(t.getAttribute('aria-controls') ?? '');
        if (panel) panel.hidden = !on;
      });
      segs.forEach((s) => (s.dataset.active = String(s.dataset.seg === tab.dataset.phase)));
      active = tab;
    }
    placeNeedle(needleAt ?? Number(tab.dataset.start));
    if (focus) tab.focus();
  };

  const tabForHour = (h: number) => {
    // Phases are stored with end > start; the overnight phase runs past 24.
    const hh = h < 9 ? h + 24 : h;
    return tabs.find((t) => hh >= Number(t.dataset.start) && hh < Number(t.dataset.end)) ?? tabs[tabs.length - 1];
  };

  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', (e) => {
      const keys: Record<string, number> = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: tabs.length - 1 };
      if (!(e.key in keys)) return;
      e.preventDefault();
      select(tabs[(keys[e.key] + tabs.length) % tabs.length], { focus: true });
    });
  });

  if (bar) {
    let dragging = false;
    const fromEvent = (e: PointerEvent) => {
      const r = bar.getBoundingClientRect();
      const f = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 0.9999);
      const h = hourAt(f);
      select(tabForHour(h % 24), { needleAt: h });
    };
    bar.addEventListener('pointerdown', (e) => {
      dragging = true;
      bar.setPointerCapture(e.pointerId);
      fromEvent(e);
    });
    bar.addEventListener('pointermove', (e) => {
      if (dragging || e.pointerType === 'mouse') fromEvent(e);
    });
    const end = () => (dragging = false);
    bar.addEventListener('pointerup', end);
    bar.addEventListener('pointercancel', end);
  }

  segs.forEach((s) => (s.dataset.active = String(s.dataset.seg === active.dataset.phase)));
  placeNeedle(Number(active.dataset.start));
}
