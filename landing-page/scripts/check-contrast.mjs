#!/usr/bin/env node
/**
 * WCAG 2.x contrast check for every text/background pair the page uses, read from the @theme
 * tokens in src/styles/global.css. Body text needs 4.5:1 (AA); large display text 3:1.
 *
 *   npm run check:contrast -w landing-page
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const css = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../src/styles/global.css'), 'utf8');
const tokens = Object.fromEntries([...css.matchAll(/--color-([\w-]+):\s*(#[0-9a-f]{6})/gi)].map(([, k, v]) => [k, v]));
tokens.white = '#ffffff';

const lum = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => {
  const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
};

// [foreground, background, minimum, where]
const pairs = [
  // Light bands (bone, paper) and the cards on them (surface)
  ['ink', 'bone', 4.5, 'headings and body on bone bands'],
  ['ink', 'paper', 4.5, 'headings and body on paper bands'],
  ['ink', 'surface', 4.5, 'text on light cards'],
  ['ink', 'teal-wash', 4.5, 'text on teal-wash chips'],
  ['slate', 'bone', 4.5, 'secondary text on bone'],
  ['slate', 'paper', 4.5, 'secondary text on paper'],
  ['slate', 'surface', 4.5, 'secondary text on cards'],
  ['slate', 'teal-wash', 4.5, 'secondary text on teal wash'],
  ['teal', 'bone', 4.5, 'accent headline clause on bone'],
  ['teal', 'paper', 4.5, 'accent headline clause on paper'],
  ['teal', 'surface', 4.5, 'accent text on cards'],
  ['teal', 'teal-wash', 4.5, 'chip text on teal wash'],
  ['white', 'teal', 4.5, 'booked diary slot'],
  ['white', 'ink', 4.5, 'ink button, pressed toggle'],
  ['champagne-ink', 'surface', 4.5, 'estimated value on cards'],
  ['champagne-ink', 'bone', 4.5, 'lunch-blocked label'],
  ['alert', 'surface', 4.5, 'voicemail chip'],
  // Dark bands (night) and raised panels on them (night-raised, night-card)
  ['mist', 'night', 4.5, 'headings and body on dark bands'],
  ['mist', 'night-raised', 4.5, 'text on raised dark panels'],
  ['mist', 'night-card', 4.5, 'text on dark cards'],
  ['mist-muted', 'night', 4.5, 'secondary text on dark bands'],
  ['mist-muted', 'night-card', 4.5, 'secondary text on dark cards'],
  ['mist-faint', 'night', 4.5, 'timestamps on dark bands'],
  ['mist-faint', 'night-card', 4.5, 'timestamps on dark cards'],
  ['aqua', 'night', 4.5, 'accent headline clause on dark'],
  ['aqua', 'night-card', 4.5, 'speaker labels, trace status'],
  ['night', 'aqua', 4.5, 'primary button'],
  ['night', 'aqua-deep', 4.5, 'primary button, hover'],
  ['aqua-light', 'night', 4.5, 'accent headline clause, step values on dark'],
  ['aqua-light', 'night-card', 4.5, 'mint labels on dark cards'],
  ['mist-faint', 'night-raised', 4.5, 'mono labels on raised dark panels'],
  ['mist', 'graphite', 4.5, 'ink button hover text'],
  ['champagne', 'night', 4.5, 'plan bar, ROI anchor'],
  ['champagne', 'night-card', 4.5, 'blocked trace status'],
  ['alert-light', 'night-card', 4.5, 'alert trace status'],
];

let failed = 0;
for (const [fg, bg, min, where] of pairs) {
  if (!tokens[fg] || !tokens[bg]) {
    console.error(`? missing token: ${!tokens[fg] ? fg : bg}`);
    failed++;
    continue;
  }
  const r = ratio(tokens[fg], tokens[bg]);
  const pass = r >= min;
  if (!pass) failed++;
  console.log(`${pass ? '✓' : '✗'} ${r.toFixed(2).padStart(5)}:1  ${fg} on ${bg}  (${where}, needs ${min}:1)`);
}
if (failed) {
  console.error(`\n${failed} pair(s) below WCAG AA.`);
  process.exit(1);
}
console.log(`\nAll ${pairs.length} pairs meet WCAG AA.`);
