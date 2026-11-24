/**
 * Dental plans and prices: a mirror of AI-receptionist-1/core/billing/plans.py
 * (PRICE_BOOK, DENTAL_SETUP_FEES, OVERAGE_PER_MIN, PLANS["dental_*"], ANNUAL_MONTHS_CHARGED) and of
 * config.py (TRIAL_DAYS, TRIAL_MINUTES).
 *
 * Keep the two in step: `npm run check:prices` parses plans.py and fails on any difference.
 * plans.py itself marks these numbers as placeholders until real Stripe Prices exist.
 */
import type { Regional } from './regions';

export type PlanKey = 'dental_essentials' | 'dental_pro';

export interface DentalPlan {
  key: PlanKey;
  name: string;
  tagline: string;
  monthly: Regional<number>;
  setupFee: Regional<number>;
  includedMinutes: number;
  maxNumbers: number;
  maxUsers: number;
  recommended: boolean;
  features: string[];
}

export const PLANS: DentalPlan[] = [
  {
    key: 'dental_essentials',
    name: 'Dental Essentials',
    tagline: 'Lunch-break, after-hours and busy-line cover for one practice',
    monthly: { au: 549, uk: 279 },
    setupFee: { au: 990, uk: 499 },
    includedMinutes: 600,
    maxNumbers: 1,
    maxUsers: 3,
    recommended: false,
    features: [
      '600 minutes a month',
      'Books into Cliniko or Dentally',
      'Emergency triage and urgent alerts',
      'Implant and cosmetic lead qualification',
      'Transcript and summary of every call',
      'ROI dashboard',
      '3 dashboard users',
    ],
  },
  {
    key: 'dental_pro',
    name: 'Dental Pro',
    tagline: 'Up to three locations and priority support',
    monthly: { au: 990, uk: 499 },
    setupFee: { au: 1490, uk: 749 },
    includedMinutes: 1500,
    maxNumbers: 3,
    maxUsers: 8,
    recommended: true,
    features: [
      '1,500 minutes a month',
      'Up to 3 practice numbers',
      'Everything in Essentials',
      'Priority support',
      '8 dashboard users',
    ],
  },
];

export const OVERAGE_PER_MIN: Regional<number> = { au: 0.39, uk: 0.2 };
export const ANNUAL_MONTHS_CHARGED = 10;
export const TRIAL = { days: 14, minutes: 50, cardRequired: true } as const;

export const ESSENTIALS = PLANS[0];
