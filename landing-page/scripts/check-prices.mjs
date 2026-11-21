#!/usr/bin/env node
/**
 * Fails when the landing page's prices drift from the backend's.
 *
 * Source of truth: AI-receptionist-1/core/billing/plans.py (PRICE_BOOK, DENTAL_SETUP_FEES,
 * OVERAGE_PER_MIN, ANNUAL_MONTHS_CHARGED, the dental plans' included_minutes / max_numbers /
 * max_users) and AI-receptionist-1/config.py (TRIAL_DAYS, TRIAL_MINUTES).
 * Mirror: src/data/plans.ts (imported directly; Node strips its types).
 *
 *   npm run check:prices -w landing-page
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const backend = resolve(here, '../../AI-receptionist-1');
const plansPy = readFileSync(resolve(backend, 'core/billing/plans.py'), 'utf8');
const configPy = readFileSync(resolve(backend, 'config.py'), 'utf8');
const { PLANS, OVERAGE_PER_MIN, ANNUAL_MONTHS_CHARGED, TRIAL } = await import('../src/data/plans.ts');

const problems = [];
const expectEq = (what, site, backendValue) => {
  if (backendValue === undefined || Number.isNaN(backendValue)) problems.push(`${what}: not found in the backend source`);
  else if (site !== backendValue) problems.push(`${what}: landing page has ${site}, backend has ${backendValue}`);
};

/** The `{...}` body that follows `NAME ... = {` (balanced braces). */
function block(src, name) {
  const start = src.search(new RegExp(`^${name}\\b[^=\\n]*=\\s*\\{`, 'm'));
  if (start < 0) return '';
  let i = src.indexOf('{', start);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(i, j + 1);
  }
  return '';
}
/** `"KEY": {...}` inner dict as {k: number}. */
function dictFor(body, key) {
  const m = body.match(new RegExp(`"${key}"\\s*:\\s*\\{([^}]*)\\}`));
  if (!m) return {};
  return Object.fromEntries([...m[1].matchAll(/"(\w+)"\s*:\s*([\d.]+)/g)].map(([, k, v]) => [k, Number(v)]));
}
const flatDict = (body) => Object.fromEntries([...body.matchAll(/"(\w+)"\s*:\s*([\d.]+)/g)].map(([, k, v]) => [k, Number(v)]));

const priceBook = block(plansPy, 'PRICE_BOOK');
const setupFees = block(plansPy, 'DENTAL_SETUP_FEES');
const overage = flatDict(block(plansPy, 'OVERAGE_PER_MIN'));
const currency = { au: 'AUD', uk: 'GBP' };

for (const plan of PLANS) {
  const fees = dictFor(setupFees, plan.key);
  for (const [region, cur] of Object.entries(currency)) {
    expectEq(`${plan.key} monthly ${cur}`, plan.monthly[region], dictFor(priceBook, cur)[plan.key]);
    expectEq(`${plan.key} onboarding fee ${cur}`, plan.setupFee[region], fees[cur]);
  }
  const def = plansPy.match(new RegExp(`key="${plan.key}"[\\s\\S]*?\\)\\),`))?.[0] ?? '';
  const num = (field) => Number(def.match(new RegExp(`${field}=(\\d+)`))?.[1]);
  expectEq(`${plan.key} included minutes`, plan.includedMinutes, num('included_minutes'));
  expectEq(`${plan.key} numbers`, plan.maxNumbers, num('max_numbers'));
  expectEq(`${plan.key} users`, plan.maxUsers, num('max_users'));
  expectEq(`${plan.key} recommended`, plan.recommended, /recommended=True/.test(def));
  const name = def.match(/name="([^"]+)"/)?.[1];
  expectEq(`${plan.key} name`, plan.name, name);
}
for (const [region, cur] of Object.entries(currency)) expectEq(`overage per minute ${cur}`, OVERAGE_PER_MIN[region], overage[cur]);
expectEq('annual months charged', ANNUAL_MONTHS_CHARGED, Number(plansPy.match(/^ANNUAL_MONTHS_CHARGED\s*=\s*(\d+)/m)?.[1]));
expectEq('trial days', TRIAL.days, Number(configPy.match(/TRIAL_DAYS:[^\n]*default=(\d+)/)?.[1]));
expectEq('trial minutes', TRIAL.minutes, Number(configPy.match(/TRIAL_MINUTES:[^\n]*default=(\d+)/)?.[1]));

if (problems.length) {
  console.error(`✗ Landing-page prices are out of step with core/billing/plans.py:\n  - ${problems.join('\n  - ')}`);
  console.error('Update landing-page/src/data/plans.ts (or the backend) so both match.');
  process.exit(1);
}
console.log(`✓ Landing-page prices match plans.py (${PLANS.length} dental plans, AUD and GBP, trial ${TRIAL.days} days).`);
