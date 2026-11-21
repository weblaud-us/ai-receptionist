/**
 * Everything that differs between the Australian and UK page lives here, typed so that every
 * regional value must exist for both regions (a missing UK variant is a compile error, which is
 * what keeps the AU | UK switch free of stale values).
 */

export type Region = 'au' | 'uk';
export const REGIONS: readonly Region[] = ['au', 'uk'];
export type Regional<T> = Record<Region, T>;

export interface RegionFacts {
  /** Two-letter country code the dashboard signup expects (`?country=`). */
  country: 'AU' | 'GB';
  label: string;
  short: string;
  currency: 'AUD' | 'GBP';
  locale: 'en-AU' | 'en-GB';
  /** Page path for this region. */
  path: string;
  pms: { primary: string; secondary: string; secondaryNote: string };
  emergency: { number: string; extra: string };
  hosting: { city: string; region: string };
  /** Sample practice used in the console mock-up and demo calls (fictional). */
  practice: { name: string; suburb: string; phone: string; dentist: string };
  /** What the hero headline uses as the typical high-value case. */
  headlineValue: number;
  /** Booked consult shown in the hero console. */
  consoleValue: number;
  consultFee: number;
  funds: string;
}

export const REGION: Regional<RegionFacts> = {
  au: {
    country: 'AU',
    label: 'Australia',
    short: 'AU',
    currency: 'AUD',
    locale: 'en-AU',
    path: '/',
    pms: { primary: 'Cliniko', secondary: 'Dentally', secondaryNote: 'early access' },
    emergency: { number: '000', extra: '' },
    hosting: { city: 'Sydney', region: 'ap-southeast-2' },
    // ACMA reserves (02) 5550 xxxx for fictional use.
    practice: { name: 'Harbourline Dental', suburb: 'Mosman', phone: '(02) 5550 4120', dentist: 'Dr Patel' },
    headlineValue: 5000,
    consoleValue: 4500,
    consultFee: 95,
    funds: 'HICAPS, Bupa, Medibank and other health funds',
  },
  uk: {
    country: 'GB',
    label: 'United Kingdom',
    short: 'UK',
    currency: 'GBP',
    locale: 'en-GB',
    path: '/uk/',
    pms: { primary: 'Dentally', secondary: 'Cliniko', secondaryNote: '' },
    emergency: { number: '999', extra: 'NHS 111' },
    hosting: { city: 'London', region: 'eu-west-2' },
    // Ofcom reserves 020 7946 0000–0999 for fictional use.
    practice: { name: 'Wimpole Dental Rooms', suburb: 'Marylebone', phone: '020 7946 0418', dentist: 'Dr Okafor' },
    headlineValue: 5000,
    consoleValue: 3200,
    consultFee: 75,
    funds: 'Denplan and practice payment plans',
  },
};

/** Whole-unit currency, e.g. "$4,500" (AU) or "£3,200" (UK). */
export function money(region: Region, amount: number, opts: { cents?: boolean } = {}): string {
  const r = REGION[region];
  return new Intl.NumberFormat(r.locale, {
    style: 'currency',
    currency: r.currency,
    minimumFractionDigits: opts.cents ? 2 : 0,
    maximumFractionDigits: opts.cents ? 2 : 0,
  }).format(amount);
}

/** Build a Regional<string> from a function of the region. */
export function both<T>(fn: (r: Region, facts: RegionFacts) => T): Regional<T> {
  return { au: fn('au', REGION.au), uk: fn('uk', REGION.uk) };
}

/** The region a page is prerendered for: `/uk/…` is UK, everything else AU. */
export function regionFromPath(pathname: string): Region {
  return /^\/uk(\/|$)/.test(pathname) ? 'uk' : 'au';
}
