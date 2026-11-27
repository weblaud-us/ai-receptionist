# Plan: Sales suite: AI offer copilot, safe launch, and a built-in CRM

## Context
Building a campaign is slow today. The "New offer" sheet (platform dashboard → Sales dialer → Offers) has about 20 hand-typed fields across 5 tabs. Its scripts must pass the AI/company/opt-out checks, and it also needs emails, consent wording and a voicemail. Only then can a campaign be set up by hand.

The owner wants AI to do the typing and the whole dialer to be enterprise-grade, with **no external CRM**: the platform dashboard should be the CRM.

Owner choices (2026-11-26):
- **AI:** draft a whole offer; per-section assist; AI offer review; learn from real calls.
- **Enterprise:** readiness and guardrails; version history with diff; campaign quick-setup; automated test calls; launch checklist; auto-pause guardrails; caller ID health; lead fit scoring; email follow-up sequences; sales roles; audit viewer; **a built-in CRM instead of HubSpot or Pipedrive**.
- **Not now:** four-eyes approval before publishing.

Principles. These keep today's safety model:
- AI only **suggests**. A person saves and publishes. `OfferSettings` and `validate_offer` still have the final say.
- **Facts written by AI must be confirmed** before publishing: proof points (the only claims the agent may make), the price sentence and consent wording.
- **Compliance settings come from `rules.py`, never from a model.** This covers campaign quick-setup, the launch checklist and auto-pause.
- Every AI call is audited, rate-limited per user and capped per day. It uses `settings.OPENAI_MODEL` in JSON mode, the same pattern as `core/outbound/research.py` and `qualify.py`.
- Existing rules still apply: no recording, no SMS (email only), no DELETE endpoints, and LaudLine copy rules for LaudLine offers.

## Step 0 (after approval) — ✅ done 2026-11-26
- Save this plan as `e:\WebLaud\Products\receptionist\SALES_SUITE_PLAN.md`, in the style of `SALES_DIALER_PLAN.md`, and tick each phase off there as it lands.
- Save a memory: "no external CRM: the CRM is built into the platform dashboard".
- Phases ship in order. Each one can ship on its own.

---

# Wave 1: Offer copilot

## 1. Foundations (backend) — ✅ done 2026-11-26
**New `AI-receptionist-1/core/outbound/offer_ai.py`.**
- `complete(messages, *, kind, max_tokens, llm=None) -> (dict, usd)`:
  - JSON mode with `store=OPENAI_STORE`, a timeout and one retry.
  - Cost comes from `research.cost_usd`.
  - `llm` can be injected, so tests use fakes (the same pattern as `research.research_lead`).
- Budget:
  - `sales_ai_meta`, one document per UTC day, with `$inc` on `usd` and `by_kind.<kind>`.
  - New settings `SALES_AI_ASSIST_ENABLED=true`, `SALES_AI_ASSIST_DAILY_USD=3.0`, and `SALES_AI_ASSIST_MODEL=""` (falls back to `OPENAI_MODEL`).
  - Add them to `config.py` and `.env.example`.
- `guard(user, kind)`:
  - `ratelimit.allow_fixed(f"sales-ai:{uid}", 60, 3600)` → 429
  - no key or disabled → 503
  - over budget → 429
  - Audits `sales.offer.ai.<kind>` with the cost.
- `coerce_offer(raw, base)` makes model output a valid draft:
  - It truncates to the `OfferSettings` limits, slugs variant ids so they are unique, drops placeholders outside `PLACEHOLDERS[kind]`, and runs `of.scrub`.
  - The result must pass `OfferSettings.model_validate`.
- `repair_openers(draft)`: one round that sends `check_opener` problems back to the model. Anything still wrong is left for the person to fix.
- Fixed generation rules in the prompt:
  - It is an AI and says so; only allowed placeholders.
  - No SMS or texting, no recording, no pressure.
  - Written to be spoken: numbers in words, short turns, AU/UK spelling to match the market.
  - Never invent results, clients or prices.
  - Proof points come only from the website text, each with its source URL. The site is fenced as `<website_text>` using `site_import._untag`.

**`core/outbound/offers.py`.**
- `validate_issues(s) -> [{level, field: "variants.1.cold_opener", message}]`. `validate_offer` stays as a wrapper, so existing callers and tests don't change. `draft_problems` also returns the field path.
- Draft-only `ai_unconfirmed: [field paths]` on the `sales_offers` doc:
  - It is not in `OfferSettings`, so never in versions or checksums.
  - `publish()` refuses while it is not empty.
  - The create and update routes take `OfferDraftBody(OfferSettings)`, which adds `ai_unconfirmed` and `expected_updated_at`.
- **Optimistic concurrency:** a stale `expected_updated_at` returns 409. The audit diff of `sales.offer.update` lists `changed: [keys]`.
- New warnings: mentions SMS or texting; "free trial" in a LaudLine offer (the brand says "14-day pilot").

**`routers/sales.py`:**
- `/status` gains `ai_assist {enabled, spent_today_usd, daily_cap_usd}`.
- `/preview` returns `issues`.

## 2. Sales roles and team — ✅ done 2026-11-26
This phase comes early so every later route is built with the right permission.
- Users get `sales_role: manager | caller | viewer`. Platform admins rank above manager.
- Tokens and principal:
  - `core/auth/tokens.access_token` adds an `sr` claim.
  - `core/auth/deps.Principal` adds `sales_role`.
  - `core/auth/service._issue` passes it through.
- New `require_sales(min_role)` in `core/auth/deps.py`, with the same MFA rule as `require_platform_admin`.
- Route map in `routers/sales.py`:

  | Role | Can use |
  |---|---|
  | viewer | GET routes |
  | caller | power dialer, live join and hang-up, wrap-up, notes, tasks, adding a do-not-call entry, CRM activity |
  | manager | offers, AI features, campaigns including start and legal confirmation, imports, queueing, exports, outbox, CRM settings |
  | platform admin | team management, compliance registries |

  Other platform pages stay platform-admin only.
- `GET/POST/PATCH /platform/sales/team`, platform admins only:
  - Invite reuses the set-password-link flow from `routers/platform.py` team.add.
  - Change the role or remove it. Users are never deleted.
  - Audited.
- Dashboard:
  - `components/require-platform-admin.tsx` lets sales-role users into `/dialer` only, and the sidebar is filtered.
  - `can(user, role)` hides actions people can't use. `Dialer.tsx` already has an `admin` flag; replace it.
  - New Team tab for platform admins.

## 3. Draft a whole offer with AI — ✅ done 2026-11-26
- `offer_ai.generate_offer(brief, website?, company_name?, persona_name?, brand?, market AU|GB|US|CA, call_goal?, pricing_hint?, on_progress)`:
  1. `site_import.crawl(max_pages=6)` reads the seller's own site with the existing SSRF-safe crawler. If it fails, carry on from the brief alone with a warning.
  2. One streamed JSON call. Progress labels come from the arriving keys, as in `site_import.extract`.
  3. `coerce_offer`, then `repair_openers`, then `validate_issues`.
- Returns `{draft, issues, ai_unconfirmed, sources, cost_usd}`. Nothing is saved.
- Routes:
  - `POST /offers/generate/stream`, an SSE stream through `utils/sse.progress_response`.
  - `POST /offers/generate`, a one-shot fallback.
- Dashboard:
  - "New offer" opens a chooser: **Draft with AI** (recommended), **Copy an offer**, or **Blank**.
  - `offer-editor/ai-draft.tsx` shows progress with the existing `SiteImportProgress` component.
  - The editor opens pre-filled. "AI" chips stay until a field is edited, and AI-written facts get "Confirm" checkboxes with their source links.
- Make `packages/shared/src/hooks/use-site-import-stream.ts` generic as `useProgressStream<T>`. Keep `useSiteImportStream` as a wrapper.

## 4. Per-section AI assist — ✅ done 2026-11-26
- `POST /offers/assist` takes `{offer, section, action: add | rewrite, target?, instruction?}`.
- Sections: value points, discovery and qualification questions, likely problems, objections, a new A/B script, `fix_opener` (at most 2 loops against `check_opener`, then 422), emails, follow-ups (Phase 17), and consent and voicemail.
  - Proof points can only be **rewritten**, never added.
- Returns a patch for that section only (the server checks the keys), plus `ai_unconfirmed` and notes.
- Dashboard: `offer-editor/assist.tsx` has a ✨ button for each section. `SuggestionDialog` shows before/after with "Apply selected". The list limits hold, and buttons are disabled with a reason when AI is off or over budget.

## 5. Readiness and guardrails — ✅ done 2026-11-26
- `offer-editor/readiness.ts`:
  - A `FIELD_TAB` map from field to tab, and `completeness(form)` with about 14 checks.
  - Both are unit-tested.
- Error and warning counts on each tab, and a "Ready 11/14" meter.
- Clicking an issue switches tab and focuses `#offer-field-<path>`.
- Closing with unsaved changes asks first, using `ConfirmDialog` from `packages/shared/src/components/blocks.tsx`. A `beforeunload` guard covers closing the tab.
- **Autosave** for saved offers:
  - Debounced at 5 seconds, only when the body would validate.
  - Sends `expected_updated_at`; a 409 shows "Reload / Keep mine".
  - Status line: "Saved 10:32".
- New offers are kept in `localStorage` and offered back when the sheet reopens.
- Split `components/sales/offers.tsx` into `components/sales/offer-editor/{form, ai-draft, assist, review, history, tests, insights, readiness}`.

## 6. AI offer review — ✅ done 2026-11-26
- `POST /offers/{id}/review` returns `{score, summary, findings[{severity, area: clarity|spoken|claims|compliance|flow, field, issue, fix?}]}`. Fields and areas come from whitelists, and each fix goes through `coerce_offer`.
- It flags claims not backed by a proof point, pressure tactics, lines too long to say, and missing purpose-of-call wording for AU.
- Stored as `last_review {at, by, checksum, result}`. It is advisory and never blocks.
- Dashboard: a sidebar panel shows the score and findings, with "Apply fix", "Go to field", and "Out of date" when the draft has changed.

## 7. Version history, diff and restore — ✅ done 2026-11-26
- `GET /offers/{id}/versions/{n}` returns the full snapshot.
- `publish()` stores `published_by_name`.
- `POST /offers/{id}/restore {version}` copies a snapshot into the draft. It is audited, refused for an archived offer, and uses `expected_updated_at`.
- Dashboard: a History tab lists versions, who published them and the campaigns pinned to each. Compare shows draft vs published or vN vs vM, through `lib/offer-diff.ts` (unit-tested), with rows grouped by tab. "Restore into draft" asks for confirmation.

---

# Wave 2: Safe launch

## 8. Automated test calls (simulation before launch) — ✅ done 2026-11-26
- **New `core/outbound/simulate.py`** becomes the single source of the scenarios that are now in `tests/evals/test_sales_evals.py`:
  - honest about being an AI; gatekeeper; plain "not interested" → `mark_not_interested`; "stop calling" → `add_to_dnc`
  - price policy, never or range; the judge is given the offer's own price sentence
  - relative-date callback; off-topic request; injection in the research text
  - 2 scenarios built from the offer's own objections
  - "makes no claim beyond these proof points"
- Each scenario: prospect turns, tools it must call and must not call, and a judge intent.
- `run_simulation(offer_runtime, variant_id)` reuses the eval harness:
  - The real `SalesAgent` in an `AgentSession` text run, with a `SalesContext(dry_run=True)` so nothing is written, plus an LLM judge.
  - It needs `livekit-agents`, so it runs as the arq job `offer_simulation` in `jobs/worker.py`. A server that can't import it reports "unavailable here".
- Storage: `sales_offer_simulations {offer_id, version or draft_checksum, variant_id, status, results[{scenario, passed, transcript, tools, reason}], passed, total, cost_usd}`. Cost counts under the AI budget, kind `simulation`.
- Routes: `POST /offers/{id}/simulate`, `GET /offers/{id}/simulations`, `GET /simulations/{sid}`.
- Dashboard: a "Test calls" tab with Run (one script or all), pass/fail for each scenario, the transcript and the judge's reason.
- `tests/evals/test_sales_evals.py` now imports its scenarios from `simulate.py`.

## 9. Campaign quick-setup (rules-based) — ✅ done 2026-11-26
- `POST /campaigns/suggest {offer_id, source?, tag?, lead_ids?}` uses the same lead query as `queue_leads` and groups the leads that can be queued by country.
- Each country maps to a call type:
  - an AI type when `rules.allowed_countries` allows it
  - otherwise `human_assisted`
  - otherwise none, with the reason; for example, "AU AI cold calls are off until counsel adds AU"
- It returns one draft per call type:
  - Name: "<offer> · <countries> · <Mon YYYY>".
  - Scripts: the first 2 published scripts, for A/B.
  - Calling window: business hours clamped inside `COUNTRY_WINDOWS`.
  - Caller IDs: numbers used by existing campaigns with a matching country and healthy status (Phase 12).
  - Starting limits: 2 at once, 50 a day, 3 attempts, retries after 48 and 96 hours.
  - Vertical: the most common lead vertical.
  - Each draft lists its `reasons[]` and `warnings[]`. Nothing is created.
- Dashboard:
  - "Create campaign" sits on published offers and appears as a toast after publishing.
  - It opens `CampaignDialog` pre-filled, with "Why these settings".
  - After saving, it offers "Queue these N leads now" (the existing `/leads/queue`).

## 10. Launch checklist — ✅ done 2026-11-26
- `core/outbound/preflight.py` `checks(db, campaign) -> [{id, label, status pass|warn|fail, detail, fix}]`. The checks:
  - `dialer.readiness()` and `campaign_problems`
  - the pinned version is published and not archived
  - a **passing simulation** for the pinned version and every chosen script (required when `SALES_REQUIRE_SIMULATION=true`, the default)
  - the do-not-call registries are fresh for every lead country, and the AU wash is done
  - caller IDs match the lead countries and none is rested
  - the window is inside the legal window
  - `SALES_EMAIL_FOOTER` and `WEBHOOK_BASE_URL` are set
  - there are queued leads
  - guardrails are set
- `GET /campaigns/{id}/preflight`.
- `start` returns 409 `preflight_failed` when any check fails. The checks are recorded with the legal-review confirmation.
- Dashboard: Start opens the checklist, with fix links, before the legal confirmation.

## 11. Auto-pause guardrails — ✅ done 2026-11-26
- The campaign gains `guardrails`. It is **not** a material field, so changing it doesn't need a new legal review. Defaults, all measured over 24 hours:

  | Guardrail | Default |
  |---|---|
  | Opt-out rate | at most 5% of people reached |
  | Answer rate | at least 5%, once there are 40 or more dials |
  | Error rate | at most 20%, once there are 20 or more calls |
  | Cost per meeting | no limit unless set |

- `core/outbound/guardrails.py`:
  - `evaluate(campaign, attempts)` is a pure function.
  - `check_all(db, now)` runs from `dialer.tick` at most once a minute for each campaign.
- On a trip:
  - `status=paused` with `paused_reason {code, detail, metrics, at}`.
  - A system audit entry `sales.campaign.auto_pause`.
  - One email to the new setting `SALES_ALERT_EMAILS`, sent through `send_email`.
  - A banner in Campaigns. Restarting runs the checklist again.

## 12. Caller ID health — ✅ done 2026-11-26
- `core/outbound/number_health.py`: for each `from` number over 7 days: dials, answer rate, human rate, short-hang-up rate and opt-out rate.
- A number is flagged as `suspect` when, with 30 or more dials, its answer rate is under half the median of numbers in the same country, or it fell sharply compared with the week before.
- `sales_number_state {_id: e164, rested_until, reason}`. `dialer.pick_from_number` skips rested numbers and never leaves a campaign with no number (it warns instead).
- Auto-rest for 72 hours, only when another healthy number for the same country exists.
- `GET /numbers/health` and `POST /numbers/{e164}/rest|reactivate` (manager, audited).
- Dashboard: a Numbers card shows a table with a sparkline and the flags.
- The checklist reminds you to register US numbers with the free caller registry. That is a manual step.

---

# Wave 3: Selling and the built-in CRM

## 13. Lead fit scoring — ✅ done 2026-11-26
- `research.py` adds `fit {score 0–100, reasons ≤3}`, measured against the offer's ideal customer, to the same LLM call, so it costs nothing extra. `sanitize` clamps it.
- Without research, a rules-based score uses vertical match, website, contact name and region, with `fit.source="rules"`.
- A top-level `lead.fit_score` field.
- `dialer.claim_lead` sorts by `fit_score` descending, then `next_eligible_at`. Callbacks still go first.
  - Campaign setting `order: fit | fifo`, defaulting to `fit`.
  - New index `{campaign_id, status, fit_score:-1, next_eligible_at:1}` in `database/mongodb.py`.
- `mark_pending` treats research done for a different offer as stale.
- Dashboard: a sortable Fit column with reasons, a "fit ≥ N" filter, and "queue only above N".

## 14. Learn from real calls — ✅ done 2026-11-26
- `POST /offers/{id}/insights {days: 7|30|90}`.
- Stats are worked out **in code** with `funnel.by_variant`. A script is called "leading" only when each script has reached at least 30 people and the rates differ by at least 5 points.
- The model sees only short extracted text, never raw transcripts:
  - `qualification.objections` and `.pains`
  - `not_interested_reason`
  - `summary`
- It needs at least 5 answered calls. The model groups the objections, marks each as covered or not by the offer's objections, and suggests answers and questions.
- Cached for 24 hours in `sales_offer_insights`.
- Dashboard: an Insights tab with top objections, a script table, and "Add to draft" through `SuggestionDialog`.

## 15. CRM core: contacts, deals, ownership, duplicates — ✅ done 2026-11-26
- **Contacts:** `lead.contacts[] {id, name, role, email, phone, primary, email_consent_at}`.
  - The lead stays the company record.
  - The dialer still calls only `lead.phone`, through the gate. Calling a contact's own number would need the gate for each number, and is out of scope.
- **Deals:** new `sales_deals {lead_id, offer_id, campaign_id, title, amount, currency, stage, probability, expected_close, owner_id, lost_reason, won_at}`.
  - Stages: qualified → meeting_booked → proposal → won / lost. Each stage has a default probability.
  - A deal is created automatically on `interested` or `demo_booked`. A new optional offer field, `deal_value_default`, gives its amount.
  - Proposals: `deal.proposal {sent_at, amount, link}`, and the AI can draft the proposal email.
  - The lead's pipeline stage is its most advanced open deal, or `followup.derived_stage` when it has none.
  - Migration `m0007_sales_crm` (dry run, then `--apply`) creates deals for leads that are already past "interested".
- **Ownership:**
  - `lead.owner_id` and `deal.owner_id`; tasks already have `owner`.
  - New leads and deals are assigned round-robin among users with "takes leads" on, or to `campaign.owner_id`.
  - A "My work" view shows my overdue and due-today tasks, my meetings and my open deals.
- **Duplicates and merge:**
  - Duplicates are found by phone (E.164), website domain and similar names.
  - Merge combines activity, tasks, history, contacts and deals. It always keeps the stricter compliance state: do-not-call wins, declined offers are combined, and the longest cooldown is kept.
  - The merged lead gets status `merged`, so it is never deleted.
  - Audited.
- Routes: `/deals` (GET, POST, PATCH), `/leads/{id}/contacts`, `/leads/{id}/merge`, `/leads/duplicates`, and `/me/work`.
- Dashboard: the Pipeline becomes a drag-and-drop deal board with totals and weighted value. LeadSheet gains contacts and deals.

## 16. CRM workspace — ✅ done 2026-11-26
- **Saved views and bulk actions:**
  - Saved views are filters kept per user, or shared, in `sales_views`.
  - Bulk actions: assign, change stage, tag, queue into a campaign, start a sequence, export.
- **Search:** a text index on business, contact names, emails and phones. A ⌘K search in the Dialer.
- **Custom fields:** `sales_custom_fields {key, label, type text|number|date|select, applies_to lead|deal}`, with values in `custom{}`. They can be edited and filtered, and the CSV importer can map them.
- **Compose email** from LeadSheet:
  - Uses offer templates or an AI draft, through `followup.send_sales_email` (suppression, footer, outbox) and logs to the timeline.
  - It is allowed only when the lead or contact has `email_consent_at`, or replied. A manager can record consent with a note, and that is audited.
- **Reply capture:**
  - A signed reply-to address `reply+<signed ref>@SALES_INBOUND_DOMAIN`, using `core.signed_links`.
  - Replies arrive at `POST /public/sales/inbound-email`, an inbound-parse webhook verified by `SALES_INBOUND_EMAIL_SECRET`. ⚠ Which email provider handles inbound mail is confirmed at build time.
  - A reply logs to the timeline, stops sequences and creates a "Reply from …" task.
  - Off until it is configured.

## 17. Email follow-up sequences — ✅ done 2026-11-26
- The offer gains `follow_ups: [{after_days 1–30, subject, body}]`, at most 3 steps. They are versioned with the offer, and AI assist covers them.
- **Consent first:**
  - A sequence starts only for a lead with `email_consent_at`: for example after "send me info", or after a meeting is booked (nudges before and after the meeting).
  - Never for no-answer or exhausted leads, which would be cold email, off until counsel approves it.
- `followup.schedule_sequence` writes `sales_outbox` rows with `send_after`, and the existing retry tick sends the ones that are due.
- A sequence stops on unsubscribe, a reply, a meeting booked, won or lost, do-not-call, not interested, or "Stop sequence".
- Dashboard: the steps are edited in Meeting & emails. LeadSheet shows scheduled steps and lets you cancel them.

## 18. Reports, daily digest and audit viewer — ✅ done 2026-11-26
- **Reports** (`GET /reports`):
  - pipeline value by stage, plus a weighted forecast
  - win rate, average deal size and cycle time
  - meetings by offer, campaign and source
  - activity per user each week
  - the funnel from lead to meeting to won
- **Daily digest:** an arq cron emails each user their tasks and meetings for the day.
- **Audit viewer:**
  - `GET /audit` reads the platform-scope `Repos(get_db(), "platform").audit`, filtered to `sales.*`, with filters for action, target, user and dates. User names are resolved, and it exports to CSV.
  - `GET /audit/verify` runs `core/compliance/audit_chain.verify` and shows "chain intact".
  - Dashboard: an Activity tab. The offer History tab also shows that offer's audit entries.

---

## Critical files
- New backend modules (`core/outbound/`): `offer_ai.py`, `simulate.py`, `preflight.py`, `guardrails.py`, `number_health.py`, `crm.py` (deals, contacts, merge, views, custom fields), `reports.py`.
- New migration: `migrations/m0007_sales_crm.py`.
- Changed backend files:
  - `core/outbound/offers.py`, `research.py`, `dialer.py`, `followup.py`
  - `core/auth/{deps,tokens,service}.py`
  - `routers/sales.py` (it may be split into `routers/sales_crm.py`)
  - `jobs/worker.py`, `database/mongodb.py` (indexes), `config.py`, `.env.example`
- Dashboard (`platform-dashboard/src/`):
  - `components/sales/offer-editor/*`, `pipeline.tsx` (deal board), `leads.tsx`, `campaigns.tsx`
  - new `components/sales/{crm-*, reports, activity, team, numbers}.tsx`
  - `lib/sales.ts`, `lib/offer-diff.ts`
  - `pages/Dialer.tsx`, `components/require-platform-admin.tsx`
- Shared: `packages/shared/src/hooks/use-site-import-stream.ts`.
- Reused as they are:
  - `site_import.crawl` / `_untag`, `utils/sse.progress_response`, `research.cost_usd`, `ratelimit.allow_fixed`
  - `of.scrub` / `check_opener`, `funnel.by_variant`, `rules.allowed_countries` / `COUNTRY_WINDOWS`
  - `followup.send_sales_email` / outbox / suppression, `core.signed_links`, `audit_chain.verify`
  - the eval harness in `tests/evals/test_sales_evals.py`
  - `SiteImportProgress`, `ConfirmDialog`, `useDebounced`

## Tests (added each phase, with a fake LLM and crawl injected)
- **Wave 1:**
  - Generate: coercion and limits; scrubbed fences and placeholders; opener repaired once; AI-written facts marked unconfirmed; nothing saved; cost audited; a failed crawl falls back to the brief.
  - Guards: 503 and 429.
  - Assist touches only its section, and proof-point "add" is refused.
  - Publishing is refused while facts are unconfirmed.
  - Review fields are whitelisted.
  - Restore is audited.
  - A stale `expected_updated_at` → 409.
  - **Role matrix:** a table-driven test that every `sales_router` route has a `require_sales` dependency; viewer is refused writes; caller can use dial-next but can't edit offers.
- **Wave 2:**
  - Scenario building from an offer (price policy, objections) and result totals, with a fake runner. The real runner runs in the Linux evals.
  - Suggest: AU and GB grouped by call type; AU AI cold off → `human_assisted` with a reason; the window lies inside the legal window.
  - Preflight blocks start when a simulation is missing or a registry is stale.
  - Guardrail thresholds and minimum samples; the tick pauses and emails once.
  - Number flagging; a rested number is skipped but the last one never is.
- **Wave 3:**
  - Fit is clamped, `claim_lead` orders by fit, callbacks still go first.
  - Insights: no LLM call with fewer than 5 calls; "leading" needs at least 30 people reached per script.
  - Deals are auto-created on interested or booked.
  - **Merge keeps the stricter compliance state.**
  - Compose is refused without consent.
  - A sequence stops on reply, unsubscribe or booking; no sequence starts without consent.
  - The inbound signature is verified.
  - Report sums are correct.
  - The audit viewer shows only `sales.*`, and verify passes.
- **Unchanged:** `tests/test_sales_offers.py`, and the existing no-DELETE test extended to the new routes.
- **Vitest:** `offer-diff`, `readiness`, the deal board's reducer and `can()`.
- **Evals** (`RUN_EVALS=1`): a generated offer publishes cleanly; without a website it has no invented proof points; injection in the site text has no effect.

## Verification
- Backend, in `AI-receptionist-1/`:
  - `uv run pytest -q`, `uv run ruff check .`, `uv run mypy`
  - `uv run python -m migrations.m0007_sales_crm`, then the same with `--apply`
- Frontend, from the repo root:
  - `npm run typecheck:shared`
  - `npm run lint -w platform-dashboard`
  - `npm run test -w platform-dashboard`
  - `npm run build -w platform-dashboard`
- Manual checks in the running app:
  1. Draft with AI from `weblaud.com` plus "AI automation for AU dental groups", then ✨ add objections and fix an opener, review it, confirm the facts and publish v1.
  2. Run the test calls (Linux worker) and see 10/10. Create a campaign from the offer for an AU+GB import. Start it: the checklist blocks until the simulation and the registries pass, then the legal confirmation follows.
  3. Seed attempts with many opt-outs: the campaign auto-pauses and an email arrives. A spam-flagged number is rested.
  4. Sign in as a caller: they see only the dialer and can't edit offers.
  5. An interested lead → a deal on the board → drag it to proposal → compose an email (consent shown) → a reply is captured → the sequence stops → the deal is won → it appears in Reports.
  6. Merge two duplicate leads, one of them on the do-not-call list: the merged lead stays on it.
  7. The audit viewer shows every step, and "chain intact" holds.

## Risks
- **Size:** 18 phases. Each one ships on its own, and wave 1 is useful by itself.
- **Roles change auth:** the role-matrix test and the platform-admin fallback limit the risk.
- **AI cost:** a daily cap, a per-user rate limit and audited spend. A generation is about 6k tokens in and 2k out. A simulation is about 12 short runs plus a judge, roughly $0.05–0.15.
- **Invented claims:** AI-written facts can't be published unconfirmed, the review flags unbacked claims, and the simulation judges claims.
- **Compliance drift:** setup, checklist and guardrails come only from `rules.py`. Merges keep the stricter state. Sequences need consent. Cold email stays off.
- **Inbound email** depends on which provider can receive mail. It ships behind settings and is off by default.
- **The simulation needs `livekit-agents`**, which runs only in the Linux worker. On Windows it shows "unavailable here".


---

## Implementation notes (2026-11-26)

**Built.** All 18 phases are built and tested.

**Where things deviate from the plan:**
- **Sales roles are read from the user record on every request**, not from a token claim. Taking a role away works at once instead of when the token expires. The API (`core/auth/deps.require_sales`) and the dashboard (`/auth/me` returns `sales_role`) both use the record.
- **Simulated test calls are tied to the checksum of the exact offer text**, not to a version number. A draft that passed and was then published unchanged counts as a pass for that version.
- **The consent wording and the voicemail are fixed templates around an AI-written topic.** The AI never writes the legal shape. If an opener still fails the checks after one repair round, a plain opener that always passes is used.
- **The launch checklist only fails on these:** the dialer isn't ready, the offer version is missing or archived, or a required simulation hasn't passed. Stale registries, AU washes, caller-ID country and the email footer are warnings, because the compliance gate already blocks those calls one by one.
- **The pipeline board shows deals.** Leads still being called stay on the Leads tab.
- **The safety work runs in one worker cron, `sales_safety_tick`:** simulations, guardrails, caller-ID health and the daily digest. The API process runs it too when there is no Redis.

**New backend modules** (`AI-receptionist-1/core/outbound/`):
- `offer_ai.py`: draft, assist, review, email drafts, budget and guards.
- `simulate.py`, `preflight.py`, `campaign_setup.py`, `guardrails.py`, `number_health.py`, `safety.py`.
- `crm.py`, `insights.py`, `reports.py`.
- Router: `routers/sales_crm.py`.
- Migration: `migrations/m0007_sales_crm.py`, which runs on every deploy.

**New dashboard pieces** (`platform-dashboard/src/components/sales/`):
- `offer-editor/*`: form, AI draft, assist, review, history, test calls, insights, readiness.
- `launch.tsx`, `numbers.tsx`, `team.tsx`.
- `crm-board.tsx`, `crm-lead.tsx`, `reports.tsx`, `activity.tsx`, `search.tsx`.

**New tests:**
- Backend: `tests/test_sales_offer_ai.py`, `test_sales_safety.py`, `test_sales_crm.py`.
- Dashboard: `src/__tests__/offer-copilot.test.ts` and `crm.test.ts`.
- `tests/evals/test_sales_evals.py` now also runs every simulation scenario through the real runner.

**Before using it:**
1. Deploy, or run `uv run python -m migrations.m0007_sales_crm --apply`.
2. The test calls need the jobs worker on Linux, where `livekit-agents` can be imported. Until it runs, a campaign on a published offer can't start, because `SALES_REQUIRE_SIMULATION=true`.
3. Set `SALES_ALERT_EMAILS` to get auto-pause and caller-ID alerts. Set `PLATFORM_DASHBOARD_URL` so sales-team invite links point at the platform dashboard.
4. Reply capture stays off until `SALES_INBOUND_DOMAIN` and `SALES_INBOUND_EMAIL_SECRET` are set, and your email provider's inbound-parse webhook posts to `/public/sales/inbound-email` with the `X-Inbound-Secret` header. Which provider handles inbound mail is still to be confirmed.
5. Run `RUN_EVALS=1 uv run pytest tests/evals/test_sales_evals.py` on Linux with a real OpenAI key.
6. ⚠ VERIFY with counsel: the consent template in `offer_ai.consent_text`, and the rule that follow-up emails need consent (they are never sent to people who didn't answer).

**Test failures that are not from this work:** 8 tests fail on fixed dates in the past (`test_scheduling`, `test_dental_pms`, `test_dental_identity`, which use 23–24 Nov 2026), a renamed demo practice (`test_seed_web_demo`), and changed sample-call scenarios (`test_render_sample_calls`).
