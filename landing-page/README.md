# Landing page (dental, AU and UK)

The public marketing site for the dental AI receptionist: an overflow, lunch-break and after-hours
receptionist for private and mixed dental practices in Australia (first) and the UK. Static Astro
build, Tailwind v4, a few small TypeScript scripts, no UI framework. Served by Nginx in production.

**With the whole stack:** `npm run stack` (or `make dev`) from the repo root starts the landing page
with everything else, on **http://localhost:4321** (Australia at `/`, United Kingdom at `/uk/`). Its
signup and sign-in buttons point at the local client dashboard on :5173. Logs: `docker compose logs -f landing`.

**On its own:**

```sh
npm install                      # from the repo root (npm workspaces)
npm run dev:landing              # http://localhost:4321  (or: npm run dev -w landing-page)
npm run build -w landing-page    # astro check + static build into landing-page/dist
npm run lint -w landing-page     # ESLint, including jsx-a11y rules on .astro templates
npm run check:prices -w landing-page    # prices match core/billing/plans.py
npm run check:contrast -w landing-page  # every colour pair meets WCAG AA
npm run og -w landing-page       # re-render public/og.png after changing the brand or headline
```

Settings are build-time `VITE_*` variables: see [.env.example](.env.example).

## How it fits together

| Path | What it is |
|---|---|
| `src/pages/index.astro`, `src/pages/uk/index.astro` | The same page prerendered for each region (`/` is `en-AU`, `/uk/` is `en-GB`, with hreflang). |
| `src/components/LandingPage.astro` | Section order. Each section is its own component. |
| `src/data/regions.ts` | Everything that differs by region: currency, PMS, emergency number, hosting, sample practice. `Regional<T>` makes a missing UK (or AU) value a type error. |
| `src/data/plans.ts` | Mirror of `AI-receptionist-1/core/billing/plans.py` (dental plans, onboarding fees, overage, trial). |
| `src/data/scenarios.ts` | The four sample calls, with timed transcript lines, for both regions. |
| `src/data/faq.ts`, `src/data/site.ts` | FAQ copy; brand, nav and env-driven links. |
| `src/styles/global.css` | Design tokens (`@theme`), region visibility rules, motion and reduced-motion rules. |
| `src/scripts/*.ts` | Region switch, mobile menu, hero call sequence, demo player, ROI calculator. |
| `public/region-init.js` | Runs in `<head>` before first paint to apply a saved AU/UK choice (an external file because the CSP allows no inline script). |

**Region switch.** Both regions' text is in the HTML (`<R>` and `<Regional>` components); CSS hides
the inactive one from `html[data-region]`. The toggle flips that attribute and updates `lang`, the
title and the URL (`/` ↔ `/uk/`). It also rewrites signup links (`data-signup-au` / `data-signup-uk`)
and fires `regionchange`, which the calculator and demo player listen for. The choice is remembered
in `localStorage` when storage is available.

**Signup links** go to `VITE_DASHBOARD_SIGNUP_URL?vertical=dental&country=AU|GB&plan=…`, which
the dashboard's signup page already reads.

**Prices** come only from `src/data/plans.ts`. When `plans.py` changes, update `plans.ts`.
`check:prices` (also run in CI) fails until they match.

**Live demo ("Talk to it now", `LiveDemo.astro` + `src/scripts/live-demo.ts`).** The visitor signs in
with Google, taps the orb and talks to the receptionist of a fictional demo practice in the browser,
with a live transcript and a countdown. Each Google account becomes a lead in the platform dashboard
(**Demo leads**). It shows only when the build has `VITE_WEB_DEMO=true`, `VITE_API_URL` and
`VITE_GOOGLE_CLIENT_ID`. `livekit-client` loads on the first tap, and Google's script when the panel
nears the viewport. To switch it on:

1. API: `uv run python -m scripts.seed_web_demo`, then `WEB_DEMO_ENABLED=true`, `WEB_DEMO_TENANT_AU` /
   `_GB`, `GOOGLE_SIGNIN_CLIENT_ID`, and this site's origin in `CORS_ORIGINS` (API and worker).
2. Google Cloud: add this site's origin to the OAuth client's "Authorized JavaScript origins".
3. Landing build: the three `VITE_*` values above (`WEB_DEMO` and `GOOGLE_SIGNIN_CLIENT_ID` GitHub
   variables in the deploy workflow). At container start, `API_ORIGIN` (and `LIVEKIT_CSP` for
   self-hosted LiveKit) fill the CSP. `deploy/docker-compose.prod.yml` sets both.

**Sample-call audio.** `uv run python -m scripts.render_sample_calls` (in `AI-receptionist-1/`) renders
the scripts in `src/data/scenarios.ts` in the agent's real voice into `public/audio/`, with timings in
`src/data/recordings/`. The next build plays them, with the transcript and trace in step.

## Deploy

`landing-page/Dockerfile` builds from the repo root like the dashboards and serves `dist/` with
`nginx.conf.template`, which sends strict security headers (`script-src 'self'` plus Google sign-in for the live demo, fonts self-hosted).
The landing page is optional per environment: set `SITE_URL` (GitHub environment variable) and
`SITE_DOMAIN` (the server's `.deploy` file) on the one environment that serves it. See
[deploy/README.md](../deploy/README.md).

## Copy rules (why the page says what it says)

The page only claims what the product does today:

- **Speed:** "replies in about a second". The measured targets are p50 ≤ 1.0 s and p95 ≤ 1.8 s
  (`AI-receptionist-1/README.md`). No "<800 ms" claim.
- **Zero Data Retention:** not claimed. `config.py` says never to claim ZDR while `OPENAI_ZDR_ENABLED`
  or `DEEPGRAM_ZDR_ENABLED` is false. The page says calls are never used to train AI (true:
  `OPENAI_STORE=false`, Deepgram model-improvement opt-out, and the privacy policy). Set
  `VITE_CLAIM_ZDR=true` only after both vendors approve.
- **Dentally** is labelled "early access" (`PMS_DENTALLY_ENABLED=false` until partner access).
- **Data location:** database, recordings and servers are in-region; speech and language models
  run where each provider hosts them. The page never says data doesn't leave Australia or the UK.
- **Encryption:** transcripts and the symptoms and notes taken from patients are field-encrypted.
  Call summaries are not yet, so the page doesn't say "all call notes".
- **Trial:** 14 days, the first 50 minutes, card required. The onboarding fee is charged at
  sign-up (it is a one-time line item in the trialing Stripe Checkout). No "risk-free" wording.
- **Statistics:** none without a source.
- No HIPAA anywhere.

## Placeholders to replace before launch

- [x] **Brand name:** LaudLine (`BRAND` in `src/data/site.ts`). Re-run `npm run og -w landing-page`
  after changing it. Logo: `brand/laudline.svg` (as supplied) → `src/assets/laudline.svg` (cropped, `currentColor`,
  used by Logo, Footer, Preloader and the social card) and `packages/shared/src/components/laudline-path.ts`
  (dashboards). The favicons are the joined "dl" from the wordmark.
- [ ] **Production domain and links:** `VITE_SITE_URL`, `VITE_DASHBOARD_SIGNUP_URL`,
  `VITE_DASHBOARD_LOGIN_URL`.
- [ ] **Legal pages:** `VITE_PRIVACY_URL`, `VITE_TERMS_URL`, `VITE_DPA_URL`. The footer leaves out
  empty links. The drafts in `docs/legal/` still need counsel review.
- [ ] **Sample-call audio:** render it with `AI-receptionist-1/scripts/render_sample_calls.py` (writes
  `public/audio/` and `src/data/recordings/`) and re-render whenever `src/data/scenarios.ts` changes.
- [ ] **Demo line and booking link:** `VITE_DEMO_PHONE_AU`, `VITE_DEMO_PHONE_UK`, `VITE_DEMO_CALL_URL`,
  `VITE_BOOK_DEMO_URL`. `AI-receptionist-1/ui/call.html` shows the practice's own name.
- [ ] **Prices:** `plans.py` marks the dental prices and onboarding fees as placeholders. Set the real
  ones there and in Stripe, then mirror them in `src/data/plans.ts`.
- [ ] **Sample practice details** (fictional names; ACMA and Ofcom reserved phone numbers) and the
  consultation fees in `src/data/regions.ts` and `scenarios.ts` are illustrative.
- [ ] **Clinical copy:** have a dentist review the emergency wording in `scenarios.ts` and `faq.ts`,
  as `DENTAL_ROADMAP.md` asks for the product itself.
- [ ] **Hosting claims:** confirm the AU and UK regional stacks are live before publishing "Hosted in
  Sydney / London" (`docs/legal/SUBPROCESSORS.md` still says to confirm availability per region).
