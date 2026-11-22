// Runs in <head> before first paint.
// 1. Marks JS as available (html.js) and decides whether the preloader plays: not with reduced motion
//    and only once per session (html.no-preload otherwise). A safety net hands over after 3.5 s even
//    if the page script never runs.
// 2. On the AU page (/) a visitor who chose UK last time sees UK straight away; on /uk/ the URL
//    always wins. Storage can be blocked, so every access is guarded.
(function () {
  var root = document.documentElement;
  root.classList.add('js');
  try {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || sessionStorage.getItem('fd-seen')) {
      root.classList.add('no-preload');
    }
  } catch {
    root.classList.add('no-preload');
  }
  setTimeout(function () {
    root.classList.add('ready', 'loaded');
  }, 3500);

  try {
    var path = location.pathname;
    if (/^\/uk(\/|$)/.test(path)) return;
    var saved = localStorage.getItem('region');
    if (saved === 'uk') {
      root.setAttribute('data-region', 'uk');
      root.setAttribute('lang', 'en-GB');
    }
  } catch {
    /* storage unavailable: keep the page's own region */
  }
})();
