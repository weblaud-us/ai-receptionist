import {
  VITE_BOOK_DEMO_URL,
  VITE_CLAIM_ZDR,
  VITE_DASHBOARD_LOGIN_URL,
  VITE_DASHBOARD_SIGNUP_URL,
  VITE_DEMO_CALL_URL,
  VITE_DEMO_PHONE_AU,
  VITE_DEMO_PHONE_UK,
  VITE_DPA_URL,
  VITE_PRIVACY_URL,
  VITE_SITE_URL,
  VITE_TERMS_URL,
} from 'astro:env/client';
import type { Region } from './regions';
import { REGION } from './regions';
import type { PlanKey } from './plans';

/** ⚠ Placeholder brand. Change it here; nothing else hard-codes the name. */
export const BRAND = 'Front Desk';
export const YEAR = 2026;

export const NAV = [
  { href: '#how-it-works', label: 'How it works' },
  { href: '#demo', label: 'Live demo' },
  { href: '#roi', label: 'ROI calculator' },
  { href: '#compliance', label: 'Compliance' },
  { href: '#pricing', label: 'Pricing' },
] as const;

export const LINKS = {
  site: VITE_SITE_URL.replace(/\/$/, ''),
  login: VITE_DASHBOARD_LOGIN_URL,
  demoCall: VITE_DEMO_CALL_URL,
  bookDemo: VITE_BOOK_DEMO_URL || VITE_DEMO_CALL_URL,
  demoPhone: { au: VITE_DEMO_PHONE_AU, uk: VITE_DEMO_PHONE_UK } as Record<Region, string>,
  privacy: VITE_PRIVACY_URL,
  terms: VITE_TERMS_URL,
  dpa: VITE_DPA_URL,
};

export const CLAIM_ZDR = VITE_CLAIM_ZDR;

/** Dashboard signup, preselecting the dental vertical, the country and (optionally) a plan. */
export function signupUrl(region: Region, plan: PlanKey = 'dental_essentials'): string {
  const url = new URL(VITE_DASHBOARD_SIGNUP_URL);
  url.searchParams.set('vertical', 'dental');
  url.searchParams.set('country', REGION[region].country);
  url.searchParams.set('plan', plan);
  return url.toString();
}
