import {
  VITE_API_URL,
  VITE_BOOK_DEMO_URL,
  VITE_CLAIM_ZDR,
  VITE_DASHBOARD_LOGIN_URL,
  VITE_DASHBOARD_SIGNUP_URL,
  VITE_DEMO_CALL_URL,
  VITE_DEMO_PHONE_AU,
  VITE_DEMO_PHONE_UK,
  VITE_DPA_URL,
  VITE_GOOGLE_CLIENT_ID,
  VITE_PRIVACY_URL,
  VITE_SITE_URL,
  VITE_TERMS_URL,
  VITE_WEB_DEMO,
} from 'astro:env/client';
import type { Region } from './regions';
import { REGION } from './regions';
import type { PlanKey } from './plans';

/** The product name. Change it here; nothing else on the site hard-codes it. */
export const BRAND = 'LaudLine';
export const YEAR = 2026;

export const NAV = [
  { href: '#how-it-works', label: 'How it works' },
  { href: '#demo', label: 'Live demo' },
  { href: '#roi', label: 'ROI calculator' },
  { href: '#compliance', label: 'Compliance' },
  { href: '#pricing', label: 'Pricing' },
] as const;

/**
 * The legal documents, published at /legal/<key> (pages/legal/[doc].astro). Their text is edited in the
 * platform dashboard (Legal documents) and read from the API; `footer` ones are linked from every page.
 */
export const LEGAL_DOCS = [
  { key: 'terms', label: 'Terms of service', footer: true },
  { key: 'privacy', label: 'Privacy policy', footer: true },
  { key: 'subscription', label: 'Subscription and billing policy', footer: true },
  { key: 'cookies', label: 'Cookie policy', footer: true },
  { key: 'dpa', label: 'Data processing agreement', footer: true },
  { key: 'aup', label: 'Acceptable use policy', footer: false },
  { key: 'subprocessors', label: 'Sub-processors', footer: false },
  { key: 'sla', label: 'Service level agreement', footer: false },
  { key: 'security', label: 'Security overview', footer: false },
] as const;
export type LegalKey = (typeof LEGAL_DOCS)[number]['key'];
export const legalHref = (key: LegalKey) => `/legal/${key}`;

export const LINKS = {
  site: VITE_SITE_URL.replace(/\/$/, ''),
  login: VITE_DASHBOARD_LOGIN_URL,
  demoCall: VITE_DEMO_CALL_URL,
  bookDemo: VITE_BOOK_DEMO_URL || VITE_DEMO_CALL_URL,
  demoPhone: { au: VITE_DEMO_PHONE_AU, uk: VITE_DEMO_PHONE_UK } as Record<Region, string>,
  // Our own /legal pages unless a build points one somewhere else.
  privacy: VITE_PRIVACY_URL || legalHref('privacy'),
  terms: VITE_TERMS_URL || legalHref('terms'),
  dpa: VITE_DPA_URL || legalHref('dpa'),
  subscription: legalHref('subscription'),
  cookies: legalHref('cookies'),
};

export const CLAIM_ZDR = VITE_CLAIM_ZDR;

/** The API (legal documents, live demo). Empty in a build without one. */
export const API_URL = VITE_API_URL.replace(/\/$/, '');

/**
 * The live "Talk to it now" demo (LiveDemo.astro). On only when switched on AND both the API origin and
 * the Google sign-in client id are set, so a half-configured build never shows a broken button.
 */
export const WEB_DEMO = {
  enabled: VITE_WEB_DEMO && Boolean(VITE_API_URL && VITE_GOOGLE_CLIENT_ID),
  api: VITE_API_URL.replace(/\/$/, ''),
  googleClientId: VITE_GOOGLE_CLIENT_ID,
};

/** The fictional demo practices (AI-receptionist-1/scripts/seed_web_demo.py), as on the sample calls. */
export const DEMO_PRACTICE = { au: REGION.au.practice.name, uk: REGION.uk.practice.name } as const;

/** Dashboard signup, preselecting the dental vertical, the country and (optionally) a plan. */
export function signupUrl(region: Region, plan: PlanKey = 'dental_essentials'): string {
  const url = new URL(VITE_DASHBOARD_SIGNUP_URL);
  url.searchParams.set('vertical', 'dental');
  url.searchParams.set('country', REGION[region].country);
  url.searchParams.set('plan', plan);
  return url.toString();
}
