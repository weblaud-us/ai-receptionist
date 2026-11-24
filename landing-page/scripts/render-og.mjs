#!/usr/bin/env node
/**
 * Renders public/og.png (1200×630) for link previews, in the site's Ordin-style look: ink ground,
 * a diagonal mint aurora, a dotted sphere with orbital rings, the LaudLine wordmark and headline.
 * Run after changing the brand or headline:
 *   npm run og -w landing-page
 * The result is committed; the build doesn't run this.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const here = dirname(fileURLToPath(import.meta.url));
const site = readFileSync(resolve(here, '../src/data/site.ts'), 'utf8');
const brand = site.match(/export const BRAND = '([^']+)'/)?.[1] ?? 'LaudLine';
// The wordmark itself (src/assets/laudline.svg): its path and the viewBox it is cropped to.
const logo = readFileSync(resolve(here, '../src/assets/laudline.svg'), 'utf8');
const logoPath = logo.match(/ d="([^"]+)"/)[1];
const [lx, ly, , lh] = logo.match(/viewBox="([^"]+)"/)[1].split(/\s+/).map(Number);
const logoScale = 44 / lh; // 44 px tall

// librsvg uses installed system fonts, so list close fallbacks after the site's faces.
const sans = "'Geist', 'Segoe UI', Helvetica, Arial, sans-serif";
const mono = "'IBM Plex Mono', Consolas, 'Courier New', monospace";

const INK = '#05090C';
const BONE = '#E6ECE4';
const STONE = '#7D8F8A';
const MINT = '#34E8A6';
const MINT_LIGHT = '#5EFFC6';

// Dotted sphere: a seeded lat/long lattice, front dots bright, back dots faint.
const cx = 905;
const cy = 330;
const R = 190;
let seed = 7;
const rand = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
const dots = [];
const tilt = 0.42;
for (let i = 1; i < 36; i++) {
  const ph = (i / 36) * Math.PI;
  for (let j = 0; j < 72; j++) {
    if (rand() < 0.62) continue;
    const th = (j / 72) * Math.PI * 2;
    let x = Math.sin(ph) * Math.cos(th);
    let y = Math.cos(ph);
    let z = Math.sin(ph) * Math.sin(th);
    [y, z] = [y * Math.cos(tilt) - z * Math.sin(tilt), y * Math.sin(tilt) + z * Math.cos(tilt)];
    const front = z > 0;
    dots.push(`<circle cx="${(cx + x * R).toFixed(1)}" cy="${(cy - y * R).toFixed(1)}" r="${front ? 1.6 : 1.1}" fill="${front ? MINT_LIGHT : MINT}" fill-opacity="${front ? (0.5 + rand() * 0.5).toFixed(2) : 0.25}"/>`);
  }
}
const ring = (rx, ry, rot, op) =>
  `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" transform="rotate(${rot} ${cx} ${cy})" fill="none" stroke="${MINT}" stroke-opacity="${op}" stroke-width="1.4"/>`;

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="aurora" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="${MINT}" stop-opacity="0"/>
      <stop offset=".5" stop-color="#087259" stop-opacity=".55"/>
      <stop offset="1" stop-color="${MINT}" stop-opacity=".6"/>
    </linearGradient>
    <filter id="blur" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="60"/></filter>
    <radialGradient id="core" cx=".5" cy=".5" r=".5">
      <stop offset="0" stop-color="${MINT}" stop-opacity=".08"/>
      <stop offset=".9" stop-color="${MINT}" stop-opacity=".02"/>
      <stop offset="1" stop-color="${MINT}" stop-opacity=".22"/>
    </radialGradient>
  </defs>
  <rect width="1200" height="630" fill="${INK}"/>
  <path d="M-100 640 L1300 40 L1300 210 L-100 790 Z" fill="url(#aurora)" filter="url(#blur)"/>

  <circle cx="${cx}" cy="${cy}" r="${R}" fill="url(#core)"/>
  ${dots.join('')}
  ${ring(R * 0.3, R * 1.02, 0, 0.6)}
  ${ring(R * 1.28, R * 0.62, -22, 0.5)}
  ${ring(R * 1.04, R * 0.98, 35, 0.3)}

  <g transform="translate(72 58) scale(${logoScale}) translate(${-lx} ${-ly})" aria-label="${brand}">
    <path d="${logoPath}" fill="${BONE}" fill-rule="evenodd"/>
  </g>

  <text x="72" y="196" font-family="${mono}" font-size="15" fill="${STONE}">AI OVERFLOW RECEPTIONIST · DENTAL PRACTICES · AU / UK</text>
  <text font-family="${sans}" font-size="72" fill="${BONE}" letter-spacing="-4">
    <tspan x="70" y="286">Stop losing implant</tspan>
    <tspan x="70" y="366">patients</tspan>
    <tspan x="70" y="446" fill="${MINT_LIGHT}">to voicemail.</tspan>
  </text>
  <line x1="72" y1="520" x2="1128" y2="520" stroke="${BONE}" stroke-opacity=".18"/>
  <text x="72" y="560" font-family="${mono}" font-size="15" fill="${STONE}"><tspan fill="${MINT_LIGHT}">01</tspan>  Lunch breaks   <tspan fill="${MINT_LIGHT}">02</tspan>  Busy lines   <tspan fill="${MINT_LIGHT}">03</tspan>  After hours</text>
</svg>`;

const out = resolve(here, '../public/og.png');
writeFileSync(out, await sharp(Buffer.from(svg)).png({ compressionLevel: 9 }).toBuffer());
console.log(`✓ wrote ${out}`);
