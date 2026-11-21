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
  ['ink', 'porcelain', 4.5, 'body text'],
  ['ink', 'surface', 4.5, 'text on cards'],
  ['ink', 'sage', 4.5, 'ROI panel, console reply'],
  ['ink', 'teal-wash', 4.5, 'pilot band'],
  ['slate', 'porcelain', 4.5, 'secondary text'],
  ['slate', 'surface', 4.5, 'secondary text on cards'],
  ['slate', 'sage', 4.5, 'ROI panel labels'],
  ['slate', 'teal-wash', 4.5, 'pilot band body'],
  ['teal', 'surface', 4.5, 'accent text'],
  ['teal', 'porcelain', 4.5, 'accent text'],
  ['teal-deep', 'sage', 4.5, 'recovered figure, console label'],
  ['white', 'teal', 4.5, 'primary button'],
  ['white', 'teal-deep', 4.5, 'primary button hover'],
  ['white', 'ink', 4.5, 'pressed toggle, badge'],
  ['champagne-ink', 'surface', 4.5, 'estimated value'],
  ['champagne', 'surface', 3, 'large numerals only'],
  ['mist', 'night', 4.5, 'dark band body'],
  ['mist', 'night-raised', 4.5, 'dark panel body'],
  ['mist-muted', 'night', 4.5, 'dark band secondary'],
  ['mist-muted', 'night-raised', 4.5, 'dark panel secondary'],
  ['teal-light', 'night', 4.5, 'dark band accent'],
  ['teal-light', 'night-raised', 4.5, 'speaker labels'],
  ['night', 'teal-light', 4.5, 'dark band button'],
  ['white', 'night', 4.5, 'dark band headings'],
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
