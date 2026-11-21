#!/usr/bin/env node
/**
 * Renders public/og.png (1200×630) for link previews. Run after changing the brand or headline:
 *   npm run og -w landing-page
 * The result is committed; the build doesn't run this.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const here = dirname(fileURLToPath(import.meta.url));
const site = readFileSync(resolve(here, '../src/data/site.ts'), 'utf8');
const brand = site.match(/export const BRAND = '([^']+)'/)?.[1] ?? 'Front Desk';

const serif = "Newsreader, 'Iowan Old Style', Georgia, 'Times New Roman', serif";
const sans = "Geist, 'Segoe UI', Helvetica, Arial, sans-serif";
const mono = "'Geist Mono', Consolas, 'Courier New', monospace";

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#FAFAF8"/>
  <g transform="translate(80 76)">
    <rect x="0" y="0" width="34" height="11" rx="3" fill="none" stroke="#0F172A" stroke-opacity=".35" stroke-width="2"/>
    <rect x="0" y="16" width="34" height="13" rx="3" fill="#0F766E"/>
    <text x="50" y="25" font-family="${serif}" font-size="30" fill="#0F172A">${brand}</text>
  </g>
  <text font-family="${serif}" font-size="68" fill="#0F172A" letter-spacing="-1.4">
    <tspan x="80" y="250">Stop losing implant</tspan>
    <tspan x="80" y="328">patients to voicemail.</tspan>
  </text>
  <text font-family="${sans}" font-size="26" fill="#475569">
    <tspan x="80" y="410">AI overflow receptionist for private dental</tspan>
    <tspan x="80" y="446">practices in Australia and the United Kingdom.</tspan>
  </text>
  <text x="80" y="548" font-family="${sans}" font-size="22" fill="#0F766E">Lunch breaks, evenings and busy lines</text>

  <g transform="translate(800 150)">
    <rect width="320" height="330" rx="20" fill="#FFFFFF" stroke="#0F172A" stroke-opacity=".08"/>
    <text x="24" y="44" font-family="${sans}" font-size="18" font-weight="600" fill="#0F172A">Thursday</text>
    <line x1="0" y1="66" x2="320" y2="66" stroke="#0F172A" stroke-opacity=".08"/>
    ${[['10:00', 'Hygiene', false], ['10:30', 'Implant consultation', true], ['11:00', 'Crown fit', false], ['11:30', 'Check-up', false]]
      .map(([t, label, booked], i) => {
        const y = 66 + i * 64;
        return `<text x="24" y="${y + 39}" font-family="${mono}" font-size="16" fill="#475569">${t}</text>
    ${booked ? `<rect x="88" y="${y + 12}" width="214" height="40" rx="8" fill="#0F766E"/>` : ''}
    <text x="${booked ? 102 : 96}" y="${y + 38}" font-family="${sans}" font-size="17" fill="${booked ? '#FFFFFF' : '#475569'}" font-weight="${booked ? 600 : 400}">${label}</text>
    ${i < 3 ? `<line x1="0" y1="${y + 64}" x2="320" y2="${y + 64}" stroke="#0F172A" stroke-opacity=".08"/>` : ''}`;
      })
      .join('\n    ')}
  </g>
</svg>`;

const out = resolve(here, '../public/og.png');
writeFileSync(out, await sharp(Buffer.from(svg)).png({ compressionLevel: 9 }).toBuffer());
console.log(`✓ wrote ${out}`);
