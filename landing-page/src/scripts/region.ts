/**
 * AU | UK switch. Content for both regions is already in the HTML (see R.astro / Regional.astro);
 * this only flips `data-region`, keeps the URL, `lang`, title and signup links in step, remembers
 * the choice and tells interactive widgets (`regionchange`).
 */
export type Region = 'au' | 'uk';

const LANG: Record<Region, string> = { au: 'en-AU', uk: 'en-GB' };
const PATH: Record<Region, string> = { au: '/', uk: '/uk/' };

export function currentRegion(): Region {
  return document.documentElement.dataset.region === 'uk' ? 'uk' : 'au';
}

function save(region: Region) {
  try {
    localStorage.setItem('region', region);
  } catch {
    /* storage blocked: the choice lasts for this page view only */
  }
}

function apply(region: Region, { announce }: { announce: boolean }) {
  const root = document.documentElement;
  root.dataset.region = region;
  root.lang = LANG[region];

  document.querySelectorAll<HTMLAnchorElement>('a[data-signup-au]').forEach((a) => {
    const href = region === 'uk' ? a.dataset.signupUk : a.dataset.signupAu;
    if (href) a.href = href;
  });

  document.querySelectorAll<HTMLButtonElement>('[data-region-option]').forEach((btn) => {
    btn.setAttribute('aria-pressed', String(btn.dataset.regionOption === region));
  });

  const title = document.querySelector<HTMLMetaElement>(`meta[name="title-${region}"]`)?.content;
  if (title) document.title = title;

  const target = PATH[region] + location.search + location.hash;
  if (location.pathname !== PATH[region]) history.replaceState(null, '', target);

  if (announce) {
    const live = document.getElementById('region-status');
    if (live) live.textContent = region === 'uk' ? 'Showing United Kingdom prices and details' : 'Showing Australian prices and details';
  }
  document.dispatchEvent(new CustomEvent<Region>('regionchange', { detail: region }));
}

export function setRegion(region: Region) {
  save(region);
  apply(region, { announce: true });
}

export function initRegion() {
  document.querySelectorAll<HTMLButtonElement>('[data-region-option]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const r = btn.dataset.regionOption === 'uk' ? 'uk' : 'au';
      if (r !== currentRegion()) setRegion(r);
    });
  });
  // region-init.js may already have switched the page; bring links and URL in line.
  apply(currentRegion(), { announce: false });
}
