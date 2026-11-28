# Plan: Single calls in the sales dialer (call one business now; the AI talks)

## Context
Today every outbound sales call has to go through a campaign. You create the campaign, queue leads, pass the launch checklist and the lawyer sign-off, and start it. Only then does the tick (or Dial next) ring anyone. Calling one business means building a whole campaign first.

The owner wants a **single call**: pick a business, let the AI read it and fill everything in, review it, and have the AI call now.

Owner decisions (2026-11-27):
1. **The AI always does the talking.** You can still listen in, take over or end the call from the existing live controls.
2. **You choose the business**: an existing lead, or a new one you enter yourself (website and/or name and phone). There is no Google search.
3. **The AI fills in the details** from the business's website: name, address and time zone, contact, kind of business, and research notes. It also **suggests the offer and script**, with a one-line reason. You confirm or change them.
4. **The review screen before the call is the sign-off.** "Approve & call" records who approved the call and exactly what they saw. No separate legal sign-off is needed for single calls.
5. **Callers and managers** can place single calls. Viewers can't.

What this means in practice: the rules in `core/outbound/rules.py` still decide who the AI may ring.
- AI cold calls go to US business landlines only (`SALES_AI_COLD_COUNTRIES=US`).
- AI consented calls go to people with an active consent for that company (`SALES_AI_CONSENTED_COUNTRIES`). The default is `US,AU`; the local `.env` has `US`.
- UK and Canada numbers, and Australian numbers without consent, are refused with the reason on the review screen.
- An Australian number also needs a fresh Do Not Call Register wash, even with consent (the existing ⚠ VERIFY rule in `rules.py`). A new AU business typed in by hand shows "wash this number first (Compliance tab)" until the wash result is loaded.
- Human-assisted campaigns remain the way to reach those numbers.
- The gate is never bypassed.

---

## Step 0 (after approval) — ✅ done 2026-11-27
Save this plan as `SALES_SINGLE_CALL_PLAN.md` in the repo root, in the style of `SALES_DIALER_PLAN.md`. Tick off each part as it lands.

## 1. Backend core: `core/outbound/single_call.py` (new; pure helpers, plus orchestration with injectable `crawl`/`llm`/`dispatch`) — ✅ done 2026-11-27
- `call_type(lead, consent, offer_company) -> (mode | None, reason)`, decided by rules and never by the model:
  - `ai_consented` when the lead's country is in the consented list and an active consent covers the offer's company (`gate.consent_covers`)
  - else `ai_cold_b2b_us` when the country is in `SALES_AI_COLD_COUNTRIES`
  - else None, with a plain reason
- `campaign_for(settings)`: the in-memory campaign used by the gate and the agent. It is stored on the attempt as `single.campaign`.
  - `{_id: None, kind: "single", campaign_type: "sales_outbound", mode, offer_id, offer_version, variant_ids: [variant], from_numbers: [from], status: "running"}`
  - `calling_window {08:00–18:00, days 1–6}` where the business is; the legal windows and AU holidays apply on top. The review shows the business's local time.
  - `max_attempts: 1`, `voicemail_policy: "hang_up"`, `vertical`, `notes: ""`, `owner_id` (the caller, used by the WhatsApp follow-up task), `script_split: "even"`, `retry_timing: "fixed"`
  - Call-specific context goes into the lead's existing **"notes for the agent"** (`lead.agent_notes`, already fenced in the prompt as `<lead_notes>`), which can be edited on the review screen. There is no new notes field.
- `gate_view(lead)`: the lead as the gate sees it for a single call.
  - `attempts=0`: a campaign's attempt count doesn't block a deliberate call.
  - `next_eligible_at` is kept only when the status is `callback`, which respects a time they asked for.
  - While the lead is claimed (`in_progress`), its status is read from `claimed_from`, so the agent's last check before dialling sees the same lead as the review did.
  - Everything else applies as is: DNC, registries, line type, consent and scope, the 30-day cap of 6, the 24 h gap after a campaign's call, declined offers and cooldown, and the legal hours.
- Single-call limits (new settings), checked in `review` and `place`:
  - `SALES_SINGLE_CALL_MIN_GAP_HOURS=4` between single calls to the same lead
  - `SALES_SINGLE_CALLS_PER_DAY=30` overall
  - `SALES_SINGLE_CALLS_MAX_CONCURRENT=2`
- `caller_ids(db, lead, now)`:
  - Candidates are `SALES_SINGLE_CALL_FROM_NUMBERS` (new, comma-separated E.164) together with `number_health.campaign_numbers(db)`.
  - Keep only numbers for the lead's country that aren't rested and are under `SALES_MAX_CALLS_PER_NUMBER_PER_DAY`.
  - Rank them like `dialer.pick_from_number` (area code, then region).
  - With no candidates, the review says how to add one.
- `prepare(db, *, lead_id | business{website, business_name, phone, country, contact_name}, by, crawl, llm, now)`:
  1. **A new business**: crawl the website once (`site_import.crawl`, 4 pages). Take the phone from `hints.phones` when none was typed; with no phone, return `needs_phone`. Then find the lead by phone, or create it with `importer.build_lead(row, {f: f for f in importer.FIELDS}, …)` (source `single_call`, tag `single-call`, `owner_id` = the creator). Insert it with an upsert on `{owner, phone}` so two people adding the same business don't create two leads. A merged lead resolves to the lead in its `merged_into`. Refuse platform customers (`importer.customer_numbers`) and the internal DNC.
  2. **Research and suggestion in one model call**, using the same crawl:
     - The prompt is fenced like `research.prompt`, lists the published, non-archived offers (name, what_we_sell, ideal_customer, script labels), and asks for the research shape plus `vertical`, `offer_id`, `variant_id` and `why`.
     - The output is sanitised with `research.sanitize`, and the chosen ids are validated against the published versions.
     - It is stored in the **same shape research_lead writes** (`lead.research`, TTL, cost, `offer_id`), so the agent's `<prospect_research>` keeps working. Spend counts against `SALES_RESEARCH_DAILY_USD`, with a per-user hourly limit.
     - A lead that already has fresh research keeps it, and there is no model call. The suggestion is the offer that research was done for, if it is still published; otherwise the fallback below applies.
     - No website, a crawl that fails (`SiteImportError`: the reason is shown, and the call can still go ahead), or no model or budget: no model call. The offer is the one this user last used for a single call, else the first published offer, with its first script and a stated reason.
  3. **Fill only empty lead fields** from the hints and research: business name, address and region, then time zones again via `phones.normalize_region` and `timezones_for`, contact name, email, vertical, website. Record `ai_filled: [fields]` so the review can flag them.
  4. Store the suggestion as `lead.call_prep{offer_id, offer_version, variant_id, why, by, at}`.
- `review(db, lead, offer_id, variant_id, from_number?, now)` works everything out on the server:
  - call type and why, caller IDs and the pick, and the exact opener (`persona.opener`)
    - The callback opener is used when the lead is waiting for a callback they asked for. The attempt's `dialled_as` is then `"callback"`, otherwise `"single"`, so the agent opens the same way the review showed.
  - the gate on `gate_view`, with `record=False`; for US AI cold this includes the paid line-type lookup
  - the single-call limits, the next allowed time (`gate.next_window`), and the business's local time
  - `review_hash` = sha256 of `{mode, offer_id, offer_version, variant_id, from_number, phone, opener}`
- `place(db, lead_id, approval, by, dispatch, now)`:
  1. Check `readiness()`, then run `review` again and return 409 when the hash differs ("Something changed since you reviewed it").
  2. **Claim** the lead atomically: any status except `dnc`, `bad_number`, `merged` or `in_progress` moves to `in_progress`, with `claimed_from` and `claimed_for: "single"`. This stops a double click or a campaign tick from dialling the lead at the same moment. A `blocked` lead may be tried, because it was blocked for a campaign's call type and the gate decides again for this one.
  3. Gate it with `record=True`. A refusal hands the lead back to `claimed_from` and returns the failures.
  4. Write the attempt with the shared builder (see 2):
     - `campaign_id: None` and `kind: "single"`
     - `single: {by, campaign, approval: {by, at, hash, material, opener, checks, suggested: lead.call_prep}, lead_before: {status, next_eligible_at}}`. It is `approval`, not `review`, because the top-level `review` field already belongs to the AI call coach.
     - `learning.OFF`, `variant_pick {how: "single"}` and `attempt_no: None`, so it stays out of A/B tests, playbooks and the pick-up-time stats (`timing.py` counts `attempt_no == 1`)
     - `lead.attempts` is **not** increased (it counts a campaign's tries). The lead gets `last_attempt_at`; `lead.single_calls` goes up once the call has a counted outcome. `followup.derived_stage` treats `single_calls > 0` as "contacted".
  5. Check concurrency and the daily cap with the existing `dialer.within_concurrency` and `dialled_since`, keyed on `campaign_id: None`: every single call shares one bucket, so no new counting code is needed. Then dispatch. If the dispatch fails, the attempt finishes as `dispatch_failed`.

## 2. Backend changes to existing code — ✅ done 2026-11-27
- `dialer.py`:
  - Extract `new_attempt(...)` from `dial_lead`; `single_call.place` uses it too.
  - `reap()` returns a stuck single-call claim to `claimed_from`, not `queued`.
  - New `may_dial(campaign, attempt, now) -> str` ("" = go), shared with the agent:
    - campaigns: running and `legal_review_valid`
    - single calls: an approval exists, it is newer than `SALES_SINGLE_CALL_APPROVAL_MINUTES=15`, and its material matches the attempt
- `outcomes.py`:
  - `finish_attempt` uses `single.campaign` when the attempt is a single call.
  - In `apply_to_lead`, for single calls:
    - uncounted and retry outcomes restore `single.lead_before` (only while the lead is `in_progress`): never requeued into a campaign, attempts left alone
    - decisive outcomes work as today
    - `callback` works as today: status `callback`, due at the time they asked for. In addition:
      - The lead leaves its campaign's queue (`campaign_id: None`, with the old run pushed onto `history` the way requeueing does). Otherwise that campaign would ring them at that time about a *different* offer, under the callback's exemption from the spacing rule.
      - The person who placed the call gets a task (`followup.create_task`, `source: "single_callback"`, due at that time): "Call back {business}, {time}: they asked". They make the callback as another single call, and `gate_view` blocks it until the time they asked for.
- `agent/sales.py` (small; logic lives in core):
  - `_load` returns `single_call.campaign_for(attempt)` for single attempts.
  - The pre-dial check uses `dialer.may_dial`.
  - Single attempts gate on `gate_view(lead)` and skip `apply_gate_failure` (`finish_attempt` restores the lead).
- `research.py`: split `research_lead` into crawl → analyse → `store_research(db, lead, doc)`, so `single_call` reuses the storage, TTL and spend bookkeeping.
- `config.py`:
  - `SALES_SINGLE_CALLS_ENABLED=true`, `SALES_SINGLE_CALL_FROM_NUMBERS=""`, `SALES_SINGLE_CALL_MIN_GAP_HOURS=4`, `SALES_SINGLE_CALLS_PER_DAY=30`, `SALES_SINGLE_CALLS_MAX_CONCURRENT=2`, `SALES_SINGLE_CALL_APPROVAL_MINUTES=15`
  - `validate` checks that the from-numbers are E.164.
- `reports.py`: label null-campaign single attempts "Single call".
- `learning.learning_curve` leaves out `kind: "single"`. Hand-picked businesses would make the offer's weekly next-step rate look better than it is.
  - Single calls still feed the call coach, insights and memory: they're real calls with the offer's script.
  - Bandit, guardrails and experiments already key on a campaign or an experiment, so they never see single calls.

## 3. API: `routers/sales_calls.py` (new; registered in `main.py` `_ROUTERS`; caller role; audited) — ✅ done 2026-11-27
- `POST /platform/sales/calls/prepare` with `{lead_id}` or `{business{…}}`. Returns `{lead, research, suggestion, ai_filled, needs_phone?}` and takes 10–40 s.
- `POST /platform/sales/calls/review` with `{lead_id, offer_id, variant_id, from_number?}`. Returns the review described in 1.
- `POST /platform/sales/calls` with `{lead_id, offer_id, offer_version, variant_id, from_number, review_hash, approved: true}`.
  - Returns `{attempt_id, room, join}`, where `join` is a hidden listen token from `_room_token`.
  - The audit entry is `sales.call.single`.
- Lead edits on the review screen use the existing `PATCH /leads/{id}` (caller role). `LeadPatch` gains `vertical`.
- `routers/sales.py`:
  - `/attempts` and `/funnel` accept `campaign_id=single`, which means `{campaign_id: None, kind: "single"}`. That uses the existing `call_attempts (campaign_id, created_at)` index, so no new index is needed. The per-lead gap query uses `(lead_id, created_at)`.
  - `GET /status` adds `single_calls: {enabled, problems[]}`, for example "dialer off" or "no caller ID yet". The dashboard hides or disables the buttons with that reason.
  - The docstring invariant changes to: no endpoint skips the gate; single calls go to a lead, through the gate, after a recorded review.

## 4. Dashboard (`platform-dashboard/`) — ✅ done 2026-11-27
- `lib/single-call.ts` (new; `lib/sales.ts` is already ~1080 lines). It follows `lib/learning.ts`: its own small `get`/`send` over `api(..., { tenant: null })`, plus query invalidation.
  - types `CallPrep`, `CallReview`, `PlacedCall`
  - hooks `usePrepareCall`, `useCallReview` (debounced as choices change), `usePlaceCall`
  - Placing a call invalidates `s-live`, `s-leads` and `s-lead`. Errors use the global toast. The 409 "changed since you reviewed" is `silentError`, and the sheet shows it inline and fetches the review again.
- `components/sales/single-call/` (new): `SingleCallSheet`, a wide `Sheet` with four steps.
  1. **Business**: search your leads (the existing leads query), or "New business" with website, name, phone and country (AU/GB/US/CA).
  2. **Preparing**: progress text ("Reading the website… Choosing the offer…").
  3. **Review**:
     - business details you can edit (AI-filled ones tagged "from the website")
     - what the AI found: summary, problems, hook, fit
     - offer, version and script, with the AI's reason
     - the call type and why, the caller ID select, and the exact opener
     - every check, pass or fail in plain words, with the next allowed time
     - the business's local time
     - **Approve & call**, disabled while any check fails; pressing it is the sign-off
  4. **Live**:
     - `CallAudio` with the listen token, Take over and End (the existing join and hangup, with the same confirmations), the live transcript and a timer
     - afterwards, the outcome and summary (`useAttempt` polling) and an optional note or corrected outcome through the existing wrap-up
     - Reuse `Transcript`/`CallTimer` by moving them out of `power-dialer.tsx` into `components/sales/call-panel.tsx`.
- `Dialer.tsx` renders the sheet once and opens it from the URL: `?call=new`, or `?call=<lead_id>` to start at Preparing. Every entry point is then just a link, it works from inside the lead sheet without stacking sheets, and it can be bookmarked.
- Entry points (`useCan('caller')`):
  - "Call a business" in the `Dialer.tsx` `PageHeader` `actions` (with `data-tour="dialer-single-call"`)
  - "Call now" in the `lead-sheet.tsx` action row, which opens the sheet with that lead and skips step 1
  - A callback task in Pipeline → Follow-ups already opens its lead, which has "Call now". No change is needed there.
  - The lead sheet's existing "Call again" (which queues the lead into a campaign) is renamed "Add to a campaign", matching the leads toolbar, so the two buttons can't be confused
- `live.tsx` shows a "Single call" badge. `results.tsx` adds "Single calls" to the campaign filter.
- Help:
  - new `src/help/single-call.md` (plain Markdown, AU/UK spelling, short steps, no secrets: the folder ships in the app bundle), with a row in `lib/help-internal.ts` (group "Sales")
  - mention it in `live-calls.md` and `sales-first-day.md`
  - one `SALES_TOUR` step in `tours/platform-tours.ts`, which also means updating `onboarding.test.tsx`:
    - `{id: 'single-call', target: 'dialer-single-call', when: (u) => can(u, 'caller'), learnMore: 'single-call'}`
    - body: "Pick a business or paste its website. The AI fills in the details and suggests what to sell. You check it, then the AI calls."
- `docs/COMPLIANCE.md`: a short "9b. Single sales calls" section after 9a. It covers who the AI may call, the review as the sign-off, the limits, and the audit trail: the attempt's `single.approval` and the `sales.call.single` audit entry.

## Reuse (don't rebuild)
- `gate.run_gate`, `gate.next_window`, `gate.consent_covers`, `registry.active_consent`, `registry.on_internal_dnc`
- `importer.build_lead`, `importer.customer_numbers`, `phones.*`
- `site_import.crawl`, plus `research.sanitize`, `research.prompt` pieces and spend
- `offers.offer_for`, `offers.variant`, `persona.opener`
- `dialer.room_name`, `dialer.livekit_dispatch`, `dialer.readiness`, `number_health.rested`/`campaign_numbers`
- `outcomes.finish_attempt`, `learning.OFF`, `_room_token`, `_audit`
- Dashboard: `CallAudio`, live join and hangup, `ConfirmDialog`, `Sheet`, `SimpleSelect`

## Tests
- **`tests/test_sales_single_call.py`**, reusing the fixtures in `tests/test_phase4_outbound.py` (`seeded`, `admin`, `lead_doc`, `campaign_doc`, `Dispatched`, `NOW`, `frozen_api_clock`) and `m0006_sales_offers.run(...)` for the seeded offers, with a fake crawl and LLM:
  - Call type:
    - `test_call_type_ai_cold_us_consented_au_refuses_gb_ca`
    - `test_consent_for_other_company_refused`
  - Prepare:
    - `test_prepare_new_business_from_website_creates_lead_fills_empty_fields_only`
    - `test_prepare_finds_existing_lead_by_phone`
    - `test_prepare_refuses_customer_and_dnc`
    - `test_prepare_needs_phone_when_site_has_none`
    - `test_suggestion_ids_validated_and_fallback_without_ai`
    - `test_prepare_reuses_fresh_research`
  - Review and approval:
    - `test_review_hash_changes_with_material`
    - `test_place_rejects_changed_review`
  - Gate and limits:
    - `test_gate_view_ignores_campaign_attempts_but_keeps_callback_time`
    - `test_single_call_respects_legal_window_and_24h_campaign_gap`
    - `test_min_gap_daily_cap_and_concurrency`
  - Placing and dialling:
    - `test_place_claims_lead_and_double_click_dials_once`
    - `test_place_writes_attempt_without_campaign_and_learning_off`
    - `test_campaign_tick_never_dials_single_claimed_lead`
  - Outcomes and clean-up:
    - `test_retry_outcome_restores_lead_and_never_requeues_campaign`
    - `test_decisive_outcomes_apply_as_today`
    - `test_callback_leaves_campaign_queue_and_creates_task`
    - `test_reap_returns_single_claim_to_previous_status`
  - Agent check:
    - `test_may_dial_single_needs_fresh_matching_approval`
  - Routes:
    - `test_routes_need_caller_role_and_are_audited`
    - `test_attempts_and_funnel_filter_single`
- `tests/test_phase4_sales_agent.py`: `test_agent_loads_single_call_campaign_from_attempt`, `test_agent_refuses_stale_single_approval`. These skip themselves on Windows when `livekit.agents` can't import (`tests/conftest.py` `livekit_agents_importable`), so run them on Linux or in CI.
- The existing `test_every_sales_route_says_who_may_use_it` (`tests/test_sales_offer_ai.py`) covers the new routes automatically. Role tests use its `_sales_user(db, role)` helper.
- **Vitest** `src/__tests__/single-call.test.tsx`:
  - the steps flow
  - Approve is disabled until the box is ticked and every check passes
  - viewers see no entry buttons
  - the lead sheet's "Call now" skips step 1

## Risks
- **The agent change can't be tested on Windows.** It is kept to a few lines in `agent/sales.py`; the decisions live in core (`may_dial`, `gate_view`, `campaign_for`) and are tested there. Run the agent tests on Linux before the first real single call.
- **Cost.** Each prepare is one crawl and one model call, counted against `SALES_RESEARCH_DAILY_USD` with a per-user hourly limit. A US review may need a paid line-type lookup (about $0.005).
- **The stated rule changes from "no endpoint dials an arbitrary number" to "single calls go to a lead, through the gate, after a recorded review".** Update `routers/sales.py`'s docstring and `docs/COMPLIANCE.md` in the same change.
- **Australia and the UK.** Most AU and UK clinics can't get an AI single call (no consent, or GB not allowed). That is by design; the review says why and points to human-assisted campaigns.

## Verification
- Backend (`AI-receptionist-1/`): `uv run pytest -q`, `uv run ruff check .`, `uv run mypy`. Run the agent tests on Linux.
- Frontend (repo root): `npm run typecheck:shared`, `npm run lint -w platform-dashboard`, `npm run test -w platform-dashboard`, `npm run build -w platform-dashboard`.
- Manually in the running app:
  1. As a caller, "Call a business" → paste a US business website → check the AI-filled fields, the suggested offer and the opener → approve → listen → take over → end. Check the attempt's review record, the lead's timeline, and that no campaign changed.
  2. A GB number: refused with the reason.
  3. An AU number with consent: allowed. Without consent: refused.
  4. A double click on Approve dials once.
  5. A lead queued in a running campaign: after a no-answer single call it is back in the campaign's queue, and the campaign doesn't ring it for 24 h.
  6. A viewer sees no buttons.

---

## Implementation notes (2026-11-27)

**Built.** Every part above is built. A pressure-test of the design before coding changed a few details. Where the build differs from the text above, this is what it does:

- **Two model calls, not one.**
  - The suggestion is one small copilot call (`offer_ai._ask`, kind `single_call`, the copilot's budget).
  - The research is the existing `research.research_lead`, given the crawl already made. The website is still read only once.
  - `research.py` is unchanged; it didn't need splitting.
- **Nothing extra is stored for the campaign.** `single_call.campaign_for(attempt)` rebuilds it from the attempt, and the lead's own `claimed_from` is the status it goes back to. The approval is the attempt's top-level `approval` (who, when, hash, material, checks, the AI's suggestion and the gate inputs).
- **Calling hours: 8 am–6 pm, Monday to Saturday, where the business is**, inside the legal windows and AU holidays. This replaces "legal windows only, with a warning outside 9–5".
- **No tick box.** "Approve & call" is the sign-off, as the owner chose.
- **Fewer settings.** There is no `SALES_SINGLE_CALLS_ENABLED` (`SALES_SINGLE_CALLS_PER_DAY=0` switches single calls off), and the 10-minute approval limit is a constant.
- **Added safeguards:**
  - one live single call per person
  - the caller ID must be one of ours
  - the offer's automated test calls must have passed (`SALES_REQUIRE_SIMULATION`), as for a campaign launch
  - a lead waiting for a callback is only called back about the offer it asked about
  - the calling hours must fit every time zone the number itself could be in, so a time zone typed in by hand can't stretch them
- **Filling in from the website:** the region and time zones are filled only when the number gave none, and "notes for the agent" is never pre-filled from website text (it is untrusted).
- **After the call:** the sheet shows the outcome and summary. A caller can correct the outcome (wrap-up) or add a note.
- **Elsewhere in the dashboard:**
  - The lead sheet's "Call again" is now "Add to a campaign".
  - Results has a "Single calls" filter (`campaign_id=single`), and Reports labels these meetings "Single calls".

**New files:**
- Backend:
  - `core/outbound/single_call.py`
  - `routers/sales_calls.py`
  - `tests/test_sales_single_call.py` (34 tests)
  - an agent test in `tests/test_phase4_sales_agent.py`
- Dashboard:
  - `lib/single-call.ts`
  - `components/sales/single-call.tsx`
  - `components/sales/call-panel.tsx` (the timer and transcript, shared with the power dialer)
  - `help/single-call.md`
  - `__tests__/single-call.test.tsx`
  - `__tests__/single-call-entry.test.tsx`

**Before the first real single call:**
1. Set `SALES_SINGLE_CALL_FROM_NUMBERS`, or have a campaign with caller IDs for the country.
2. Run the agent tests on Linux: `tests/test_phase4_sales_agent.py` skips itself on Windows.
3. Make sure the offer's automated test calls have passed for the script you'll use.
4. Get counsel's view on the ⚠ VERIFY row in `docs/COMPLIANCE.md` §9b.

---

## Addendum (2026-11-28): any country, business mobiles, businesses with no website

**Owner decisions:**
1. Single calls may be **AI cold calls to businesses in every country**, with a soft warning where the law is stricter about AI or automated calls.
2. **Outside the US the AI may ring a business's mobile**. US mobiles stay blocked.
3. A business with **no website** (or only a Facebook-style page) can be called. The AI is told, and is offered a **draft website-build offer** to sell them. The person can also write their own.

**Built:**
- **Countries:**
  - `SALES_SINGLE_CALL_COUNTRIES` (default `*`; `SALES_AI_COLD_COUNTRIES` always counts too) and `rules.may_call(mode, country, single=True)`.
  - The gate uses it for `kind: "single"`, so campaigns are unchanged.
  - `rules.STRICT_AI_COLD` (UK, EU/EEA, CA, AU, IN, AE) adds a `local_rules` **warn** check to the review, so the approval records that the person saw it.
  - Do-not-call registers still block (TPS/CTPS, DNCR wash, DNCL).
- **Mobiles:**
  - `phones.line_kind`.
  - Outside the US a single call may ring a landline, a toll-free number or a company number (UAN/shared cost). With `SALES_SINGLE_CALL_MOBILES` (default true) it may also ring a mobile, a landline-or-mobile or a VoIP number, shown as a **warn** ("make sure it's the business's number").
  - Campaigns abroad: geographic landlines only, as before.
- **Working week:**
  - `rules.FRI_SAT_WEEKEND`: Bangladesh, Saudi Arabia, the Gulf states, Egypt, Jordan, Iraq, Israel and others.
  - The default legal window there is Sunday–Thursday 9–6, and the single-call window drops Friday (`single_call.window(country)`, also rebuilt by the agent from the attempt's `country`).
- **Caller ID:** our numbers in the business's country first; with none there, ours from elsewhere, as a **warn**. The paid US line-type lookup is only required for US numbers (`dialer.campaign_problems(us_lookups=…)`).
- **No website:**
  - `lead.no_website`, set by the form's tick, by a social link (`research.social_page`), or on the review (PATCH, which prepares again so the suggestion is redone).
  - Social links are never crawled; `research_lead` marks them `no_website`.
  - The suggestion asks the AI with `website: "no website" / "only a Facebook page"`. Without the AI it picks an offer that builds websites (`sells_websites`) and its `no_website` / `social_only` script.
  - The agent's prompt says "we couldn't find one of their own … never say you looked at their website" (`persona.no_website_line`).
- **Draft offer:** `off_weblaud_websites` ("WebLaud: a website for your business").
  - Migration `m0009_sales_website_offer` (in `deploy/deploy.sh`) adds it as a **draft**, with its facts marked AI-written.
  - It has three scripts: `no_website`, `social_only` and `site_refresh`.
  - It quotes no prices.
  - Nobody is called with it until someone confirms its facts, publishes it and runs its test calls.
- **Dashboard:**
  - The country list has every country (`lib/countries.ts`) and remembers your last choice.
  - New ticks: "They don't have a website" on the form and "…of their own" on the review.
  - Warn checks don't block Approve.
  - The call type reads "AI cold call to a business".
- **Tests:**
  - `tests/test_sales_single_call.py`: 46 tests, 11 of them new (mobiles abroad vs campaigns, the mobile switch, the Fri–Sat week, a BD business with no website, the rule fallback, Facebook pages, the agent line, strict countries keep their registers, the draft offer, settings, the API).
  - `src/__tests__/single-call.test.tsx`: 3 new tests.

**Before the first call abroad:**
1. Publish the website offer and run its test calls.
2. Add a caller ID; a local number answers more often.
3. Enable the destination countries on the SIP trunk (Twilio: Voice Geographic Permissions).
4. Read the ⚠ VERIFY note in `docs/COMPLIANCE.md` §9b for each new country.

## Addendum (2026-11-28): overriding a failed check

The owner asked for an override on the review, instead of a greyed-out "Approve & call". What was built:

- **Who:** a sales manager or a platform admin. Callers see "A sales manager can override this" on the check.
- **What can be overridden** (our own rules only, `single_call.OVERRIDES`):
  - the offer's automated test calls (`simulation`)
  - business hours (`business_hours`): ours, and a country's default hours where no law sets them (`rules.LegalWindow.statutory=False`: the UK and every country not in `COUNTRY_WINDOWS`). Still only 8 am–9 pm their time (`OVERRIDE_WINDOW`), inside any statutory window and AU holidays.
  - the gap between single calls (`single_gap`)
  - the rest after a campaign's call (`recently_called`)
- **What can't:** statutory calling hours, holidays, do-not-call lists and registers, consent, line types, the 30-day cap, a callback time they asked for, a declined offer, the daily and concurrent limits, a missing caller ID or dialer setup.
- **How:**
  - Ticking Override works the review out again with `overrides`; the check comes back `overridden`, and the hash covers the overrides.
  - "Override & call" needs a reason (3+ characters).
  - The attempt keeps `overrides` and `approval.override_reason`; so does the audit log.
  - `campaign_for(attempt)` turns the gate's share (`gate.WAIVABLE`) into the campaign's `waive`, so the agent's own check before dialling agrees. `gate.waived` honours `waive` only for `kind == "single"`.
- **The button never just greys out:** the line under it says what's stopping the call and whether it can be overridden.
- **Tests:** 8 new in `tests/test_sales_single_call.py`, 4 new in `src/__tests__/single-call.test.tsx`.
