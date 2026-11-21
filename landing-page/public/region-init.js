// Runs in <head> before first paint. On the AU page (/) a visitor who chose UK last time sees UK
// straight away; on /uk/ the URL always wins. Storage can be blocked, so every access is guarded.
(function () {
  document.documentElement.classList.add('js');
  try {
    var path = location.pathname;
    if (/^\/uk(\/|$)/.test(path)) return;
    var saved = localStorage.getItem('region');
    if (saved === 'uk') {
      document.documentElement.setAttribute('data-region', 'uk');
      document.documentElement.setAttribute('lang', 'en-GB');
    }
  } catch {
    /* storage unavailable: keep the page's own region */
  }
})();
