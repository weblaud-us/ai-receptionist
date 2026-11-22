/**
 * Runs `cb` once the page has handed over from the preloader (`html.ready`). Without JS there is no
 * preloader; with `no-preload` (reduced motion, or a repeat visit this session) it is ready at once.
 * region-init.js adds `ready` after 3.5 s regardless, so nothing waits forever.
 */
export function onReady(cb: () => void) {
  const root = document.documentElement;
  const isReady = () => root.classList.contains('ready') || root.classList.contains('no-preload');
  if (isReady()) {
    cb();
    return;
  }
  const mo = new MutationObserver(() => {
    if (!isReady()) return;
    mo.disconnect();
    cb();
  });
  mo.observe(root, { attributes: true, attributeFilter: ['class'] });
}
