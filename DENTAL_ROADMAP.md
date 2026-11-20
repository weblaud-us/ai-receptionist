# Dental vertical for AU and UK practices: implementation plan, Phases 1–6

## Context
The product will be sold as an AI overflow and after-hours receptionist and lead qualifier to private and mixed cosmetic and implant dental practices in AU (first) and the UK (second). Today dental is part of the generic `clinic` vertical. It has no dental intents, no emergency triage, no NHS-versus-private handling, no lead values, no greeting for lunch breaks or overflow, no missed-call recovery, no practice-management-system (PMS) integration, no health-data privacy controls aimed at AU or UK, and no ROI dashboard. This plan adds all of that phase by phase. Everything dental is gated behind `vertical == "dental"`, so the existing verticals keep working.

**Step 0, the first action after approval:** save this plan as `e:\WebLaud\Products\receptionist\DENTAL_ROADMAP.md`, next to `ROADMAP.md`. Then implement Phases 1 to 6 in order. Each phase ends with the full test suite, ruff, mypy and the dashboard builds passing, plus a short summary, before the next phase starts.

## Findings that change the spec (from reading the code)
- **Several requested fields already exist, so reuse them rather than add duplicates:** `country` (it already drives `emergency_number()`: AU→000, GB→999), `agent.stt_language` (already sent to Deepgram), `answering_mode="overflow"` (insights already read it) and `agent.recording.announce`.
- **Split-shift hours already work** in the backend (`hours: List[HoursEntry]`, with several entries per `dow`; `is_open()`) and in the dashboard (`hours-editor.tsx` already supports several intervals per day).
- **The live voice path is LiveKit** (`agent/worker.py`, `agent/session_factory.py`, `agent/persistence.py`). `voice/twilio_handler.py` and `voice/stt_tts.py` are the legacy path behind `LEGACY_TWILIO_VOICE`. Phase 2 work goes into `agent/`, not `voice/`.
- **Hotel pages are already hidden for non-hotel tenants** (`layout.tsx` `isHotel`, and `<Only hotel>` routes in `App.tsx`). Phase 5 nav work is mostly relabelling.
- **Billing already sells in AUD and GBP**, with a setup fee and metered overage (`core/billing/plans.py` `PRICE_BOOK`, `SETUP_FEE`, `OVERAGE_PER_MIN`). Phase 6 adds dental plans rather than adding currency support.
- **Number orders already carry regulatory-bundle and address SIDs for AU and UK** (`core/services/numbers.py` `_purchase`, `complete_regulatory`). Phase 2 only adds the ABN and Companies House number.
- **Google Calendar is not a scheduling backend.** Built-in scheduling (`core/services/scheduling.py` `SchedulingService`) is the system of record, and Google is only a busy-time and booking mirror (`calendar_sync.py`). The provider interface therefore wraps the built-in engine, which keeps its Google sync, and adds Cliniko and Dentally as external systems of record.
- **Tenant scoping comes from `core/repositories/base.py` `TenantRepo`** through `Repos(db, tenant_id)`. `database/tenancy.py` is only a compatibility shim. All new data goes through `Repos`, so `tests/test_isolation_guard.py` keeps passing.
- **Test HTTP mocking will use `httpx.MockTransport`.** httpx is already a dependency; no `respx`.
- **The pasted spec stops partway through Phase 6.1.** Phase 6's reactivation and regional-deploy items below are an outline drawn from the phase title. They need your confirmation before Phase 6 starts.

---

## Phase 1: Dental vertical, clinical guardrails, tools and evals

### 1.1 Schema (`core/tenancy/models.py`, `core/services/tenant_admin.py`, `routers/tenant.py`)
- Add `"dental"` to `Vertical` and `VERTICALS`. `routers/signup.py` and `routers/platform.py` validate against these, so they pick it up automatically.
- Add `DentalSettings` (a plug-in like `HotelSettings`): `accepts_nhs`, `accepts_new_nhs_patients`, `accepts_private_health_funds` (AU HICAPS), `finance_available`, all defaulting to `False`. Add `TenantConfig.dental` and put `"dental"` in `EDITABLE_FIELDS`.
- `RecordingConfig.announce_text: str = ""`, capped at 200 characters. `AgentConfig.stt_keyterms: List[str]`, capped at 50 entries of 40 characters, deduplicated, and merged in `agent/profile.py` `keyterms`.
- `default_hours_for("dental")` returns Mon–Fri `09:00–13:00` + `14:00–17:30`.
- `ServiceBody` and `ServicePatch` get `estimated_value_minor: Optional[int] (ge=0)` and `category: Optional[Literal["cosmetic","implant","emergency","routine","other"]]`.
- Add the `"dental": "dental practice"` label in `core/outbound/persona.py`.
- No migration is needed: every field has a default.

### 1.2 Prompt (`core/prompts/verticals/dental.py`, registered in `verticals/__init__.py`)
`builder.py` stays unchanged because it uses `get_vertical()`.
- **Static `rules`**, with four tracks:
  1. Cosmetic or implant consult: qualify, call `qualify_cosmetic_lead`, then offer to book the consultation or a priority callback.
  2. Emergency: red flags (swelling affecting breathing or swallowing, uncontrolled bleeding, trauma) mean the caller is told the emergency number in the **first sentence**. For urgent problems that aren't red flags, use `triage_dental_emergency` plus the earliest same-day slot.
  3. Routine or hygiene.
  4. NHS versus private, following the facts line.
- **Guardrails:** no diagnosis, medication or dosages. Never estimate a total treatment cost or a price range; quote only "from" prices and consultation fees from the facts, the Q&A or `answer_from_knowledge`. Replies of 1–2 sentences.
- **`facts(t)`**:
  - GB: the NHS line from the `dental.*` flags, plus "NHS 111" for urgent advice out of hours.
  - AU: the HICAPS and health-fund line.
  - The finance line.
- `tools = ("triage_dental_emergency","qualify_cosmetic_lead") + SCHEDULING_TOOLS + ALWAYS`.
- Labels: Patient, Dentist, Treatment.
- Dental `default_services`, each with a `category`, and `default_faqs`.
- `keyterms` from the spec's list (Invisalign, veneers, All-on-4, HICAPS, Bupa, Medibank, Denplan, …).
- Relabel `clinic.py` and `dashboard/src/pages/Signup.tsx` so the clinic option no longer says "dental".

### 1.3 Tools (`core/tools/dental.py`, registered in `core/tools/registry.py`)
Both tools follow the `core/tools/leads.py` pattern:
- They write to `ctx.repos().leads` and call `contacts.upsert_by_phone`, `fire(notify_owner(...))` and `webhooks.emit("lead.created")`.
- Leads keep the `phone` field, so SAR and erasure in `core/compliance/subjects.py` already cover them. They also keep `name`, `problem`, `urgency` and `status`, and add `kind`.
- Both are idempotent per `call_id` and `kind`.

The tools:
- `triage_dental_emergency(symptom_summary, severity_level, swelling_or_bleeding_red_flag, patient_name?, callback_phone?)`
  - Saves `kind="dental_emergency"`, with the summary capped at 300 characters.
  - Sends an urgent owner notification.
  - On a red flag it returns `escalation="emergency_services"` and the number from `emergency_number(t.country)`, so the number comes from code. For GB callers who are urgent while the practice is closed, it adds the NHS 111 guidance.
- `qualify_cosmetic_lead(treatment_category, patient_status, timeframe, health_fund_or_finance_interest, notes?, caller_name?, callback_phone?, service_name?)`
  - Takes `estimated_value_minor` from an exact `service_name` match first, then from the first active service whose category matches (implant or cosmetic), else `None`.
  - Stores `kind="cosmetic_consult"`, `currency`, `priority` and the qualification fields.
- `agent/tools.py`: add both tools to `_OUTCOMES` as `"message"`. A later booking still wins through the `set_outcome` ranking. Binding is already per vertical through `schemas_for(tenant)`.

### 1.4 Dashboard types and minimal visibility
- `dashboard/src/lib/tenant-types.ts`: `'dental'`, `dental?`, `stt_keyterms?`, `announce_text?`, the new `Service` fields and the `Lead.kind` fields.
- `platform-dashboard/src/lib/platform.ts`: `'dental'`.
- Signup option.
- `layout.tsx:72`: show `/leads` for `dental` as "Captured leads".

### 1.5 Evals (`tests/evals/cases/`)
- Four cases:
  - `seed_dental_implant_price_guardrail.json`: no full-arch price; quotes the consult fee.
  - `seed_dental_uk_nhs_to_private_pivot.json`
  - `seed_dental_au_emergency_red_flag.json`: says 000 first; `must_not_call` check_availability and book_appointment.
  - `seed_dental_after_hours_invisalign.json`: `hours: []` with `links.booking`; `must_call: [send_info_link]`.
- `test_case_evals.py`: `_profile_for` and `test_case_files_are_valid` pass `dental`, `links`, `answering_mode`, `country` and `currency` through.
- Update `cases/README.md`.

### 1.6 Tests
- `test_tenancy.py`:
  - `dental` is accepted, and an old document still loads.
  - Split shift: `is_open` is False at 13:30 and True at 14:15.
  - Field caps, and `announce_text` in the greeting.
- `test_prompt_builder.py`:
  - The dental rules block is present.
  - GB private-only shows the NHS line and 999/111; AU shows 000 and HICAPS.
  - The catalogue equals `VERTICALS`.
- New `tests/test_dental_tools.py`:
  - The tool set is dental-only.
  - Red-flag numbers are right for AU and GB.
  - Repeat calls don't duplicate leads.
  - Value lookup works by name and by category fallback.
  - Leads are isolated per tenant, and erasure deletes them.
- `test_api_tenancy.py`: the service value and category fields round-trip.

---

## Phase 2: Localisation, greeting states, missed-call recovery, AU/UK number metadata

### 2.1 STT, TTS and LLM localisation
- Country defaults at creation (`routers/signup.py` next to `currency_for`, and `create_tenant`):
  - AU → `locale`, `agent.language` and `agent.stt_language` set to `en-AU`, timezone `Australia/Sydney`.
  - GB → `en-GB`, `Europe/London`.
  - Existing tenants are untouched.
- `agent/profile.py` `keyterms`: the dental spec list plus `stt_keyterms` (Phase 1), capped at 100 terms in total for Deepgram's keyterm limit (⚠ verify). Check that `tts_language` still hands ISO-639-1 `en` to providers that need it.
- `config.py` and `.env.example`:
  - `DEEPGRAM_MIP_OPT_OUT=true`, passed to `deepgram.STT(...)` if the LiveKit plugin exposes it (⚠ verify; otherwise use the plugin's extra query parameters).
  - `OPENAI_STORE=false`, set explicitly on the LLM.
  - `OPENAI_ZDR_ENABLED` (was `LLM_ZERO_DATA_RETENTION`), a documentation flag only, because ZDR needs OpenAI account approval and the code can't switch it on. A production guard warns when an AU or GB tenant exists and the flag is off.

### 2.2 Greeting states (`core/tenancy/models.py`, `agent/profile.py`, `agent/receptionist.py`, `core/prompts/builder.py`)
- `TenantConfig.answer_window(local_now) -> "open"|"lunch_break"|"after_hours"`:
  - `lunch_break` means closed now, with one interval earlier today and another later today.
  - Overnight intervals are handled the same way as in `is_open`.
- `greeting_text(local_now=None)`, applied to dental tenants that have no custom greeting:
  - `LUNCH_BREAK`, `AFTER_HOURS`, or `BUSY_OVERFLOW` (open and `answering_mode == "overflow"`), using the spec's wording. Each version says "AI assistant", so `has_ai_disclosure` holds.
  - The recording disclosure (`announce_text`, or the default sentence) goes **first** when `recording.announce` is on.
  - Other verticals keep today's greeting exactly.
- `VoiceProfile.greeting_for(now)` replaces the `greeting_text` property in `receptionist.on_enter`. The greeting cache key already hashes the text, so each state caches on its own.
- The "Right now" block gets a line such as "Reception is on a lunch break until 2pm."
- `agent/persistence.py` stores `answer_window` and `overflow: bool` on the call document, next to `after_hours`, for the Phase 5 KPIs.

### 2.3 Missed and abandoned call SMS recovery (`agent/persistence.py`, new `core/services/recovery.py`, `jobs/worker.py`)
- Tenant config: `missed_call_recovery: {enabled: False, template: "", channel: "sms"}`, editable by the owner.
- When it triggers, from `CallRecorder.finish()` and also from `_run_busy_line`:
  - inbound phone call;
  - the caller's number is known;
  - the outcome is not one of booked, rescheduled, cancelled, message or transferred;
  - the caller hung up.
- Exactly one text per caller per business per 24 hours, using `core/dedupe.first_time("missed_call", f"{tenant_id}:{phone}", 86400)`.
- It sends through `jobs.queue.enqueue("missed_call_recovery", …, defer_s=10)` so the text goes out within 15 s. If there's no queue it sends directly with `fire()`.
- The sender is `notify_contact(requested=False)`, which already respects opt-outs and fails closed.
- The text carries the business name, the `links.booking` link (or "call us back on …") and "Reply STOP to opt out", as the AU Spam Act and UK PECR require.
- It stamps `calls.recovery_sent_at`.
- WhatsApp only with an approved template, because free text fails outside the 24-hour window. This is flagged and off by default.

### 2.4 AU/UK number metadata (`core/tenancy/models.py`, `core/services/numbers.py`)
- `TenantConfig.legal_entity: {registered_name, abn, company_number}`.
- ABN check: 11 digits with checksum. Companies House check: 8 characters.
- Included in `start_order` registration notes and the `_alert_operator` email, so the operator can build the Twilio bundle.
- `scripts/setup_telephony.py` needs no change: it manages LiveKit trunks, not number purchases.

### 2.5 Tests
- `answer_window` for lunch, after-hours, open and overnight.
- Greeting text for each state, recording disclosure first, other verticals unchanged.
- Recovery:
  - It triggers on an abandoned call.
  - It is skipped when the call was booked, the caller opted out, a text was already sent, or the feature is off.
- ABN and Companies House validation.
- Signup defaults for AU and GB.

---

## Phase 3: PMS adapters (Cliniko for AU, Dentally for UK)

### 3.1 Provider interface (new `core/services/scheduling_provider.py`)
- `SchedulingProvider` Protocol with `list_practitioners()`, `list_appointment_types()`, `get_available_slots(appointment_type_id, date_from, date_to, practitioner_id=None)`, `create_appointment(patient, appointment_type_id, start_at, practitioner_id, notes)`, `cancel_appointment`, `reschedule_appointment`, `test_connection()`.
- `BuiltinProvider` wraps the existing `SchedulingService`; Google sync stays inside it as it works today.
- `provider_for(tenant, repos)` reads `tenant.integrations.pms.provider`: `"builtin"` (the default), `"cliniko"` or `"dentally"`.
- `core/tools/scheduling.py` `_service(ctx)` returns the provider, and the tool schemas don't change.
- On a provider timeout (3 s) or a 5xx response, the tool returns `{"error":"pms_unavailable", message:"…take their details with take_message / qualify_cosmetic_lead and promise a callback"}`, so the caller is never dropped.

### 3.2 Mirroring, so the dashboard, prompt, reminders and insights keep working
- A sync job (`pms_sync` in `jobs/worker.py`, alongside the Google sync, plus an on-demand endpoint) copies practitioners into `resources` and appointment types into `services`, with `source` and `pms_id` set. The prompt snapshot keeps reading `services`.
- Value and category stay editable locally.
- AI bookings are created in the PMS first, then mirrored into local `appointments` with `pms_id`.
- Slot IDs reuse `encode_slot_id(pms_practitioner_id, start)`.

### 3.3 Cliniko (new `core/integrations/cliniko.py`)
- An async httpx client:
  - Base URL `https://api.{shard}.cliniko.com/v1`, with the shard taken from the API key suffix (au1, au2, uk1, …).
  - Basic auth: the API key as user, blank password.
  - A `User-Agent` from `settings.CLINIKO_USER_AGENT`, set by the operator.
  - Timeouts of 2 s to connect and 4 s overall; one retry on 5xx for reads only; honours 429 `Retry-After`.
- Maps practitioners, appointment_types, businesses and `available_times` (⚠ verify the maximum window per request), patient lookup by phone and last name or create, `individual_appointments` create, and cancel.
- The key is stored as `crypto.encrypt()` → `integrations.pms.api_key_enc` and is never returned. This mirrors how `refresh_token_enc` is popped in `routers/tenant.py:117`.

### 3.4 Dentally (new `core/integrations/dentally.py`)
- The same interface. OAuth2 bearer token stored encrypted. Production and sandbox base URLs.
- ⚠ Verify endpoints and scopes against Dentally's partner docs before coding. API access needs a partner application.

### 3.5 API and dashboard
- `routers/tenant.py`, admin-only and audited, following the Google endpoints at lines 1011–1046:
  - `PUT /t/integrations/pms` saves the provider and key.
  - `POST /t/integrations/pms/test`
  - `POST /t/integrations/pms/sync`
  - `DELETE /t/integrations/pms`
- `GET /t/integrations` adds `pms` status with no secrets.
- `integrations-card.tsx`: Cliniko and Dentally cards when `vertical === 'dental'`, with Cliniko first for AU and Dentally first for GB. They sit alongside Google Calendar, which is hidden while a PMS is connected.

### 3.6 Tests
- Both clients run against `httpx.MockTransport`: shard URL and auth header, slot mapping, patient lookup or create, booking mirror, 5xx and timeout fallback, 429 handling.
- A booking never happens twice on retry: before retrying a create, search for an existing appointment at that time.
- Tenant isolation of the mirrored data.
- The key is never present in API responses.

---

## Phase 4: Health-data privacy (UK GDPR Article 9, Australian Privacy Principles)

### 4.1 Redaction (`core/log.py` `redact` and `redact_obj`, `core/observability.py`)
- New patterns:
  - Dates of birth (dd/mm/yyyy and yyyy-mm-dd).
  - NHS numbers (3-3-4 or 10 digits, mod-11 check to cut false positives).
  - Medicare numbers (10–11 digits, checksum).
- `redact_obj` blanks sensitive keys whatever their value: `symptom_summary`, `problem`, `notes`, `transcript`, `message`, `summary`, `dob`. This covers Sentry events and tool arguments.
- Check that Prometheus labels in `core/metrics.py` carry no PII.

### 4.2 Encryption at rest
- `core/sensitive.py` gets `encrypt_fields(doc, fields)` and `reveal_fields(doc, fields)`, generalising `id_number_fields` with the same `TOKEN_ENCRYPTION_KEY`.
- Dental leads store `problem_enc` and `notes_enc` with no plaintext.
- Dental call transcripts and summaries are encrypted behind `privacy.encrypt_clinical_text` (default on for dental), which the `/t/calls` and lead endpoints decrypt for authorised staff.
- The admin transcript review and `scripts/export_eval_cases` decrypt and then redact.
- New `migrations/m0005_encrypt_dental_text.py` follows `m0004`: dry run by default, `--apply`, idempotent. It is added to `deploy/deploy.sh`.

### 4.3 Retention and erasure
- Dental defaults: recording retention 30 days, transcripts 90 days, and a new `privacy.lead_retention_days` purged by `services/tenant_jobs`.
- `dashboard/src/pages/Privacy.tsx`: preset buttons for 30, 60 and 90 days.
- `core/compliance/subjects.py`: export decrypts encrypted fields. Erasure must cover dental leads, recordings (deleted from storage through `retention.delete_recording_object`) and transcripts. Add tests that prove each cascade, including PMS-mirrored appointments.

### 4.4 Docs, all dated 2026, as templates for a lawyer to review
- `docs/legal/DPA.md`, `PRIVACY.md`, `SUBPROCESSORS.md` and `docs/COMPLIANCE.md` gain sections on:
  - UK GDPR Article 9: the practice is the controller and we are the processor; Article 9(2)(h) is the basis.
  - The APPs: APP 1, 5, 6, 8 (cross-border disclosure), 11, and the Notifiable Data Breaches scheme.
  - Hosting in `ap-southeast-2` and `eu-west-2`.
  - OpenAI ZDR (only once the account is approved) and Deepgram MIP opt-out.
- No HIPAA claims for AU or UK tenants.

---

## Phase 5: Dental ROI dashboard and UI gating

### 5.1 Navigation (`layout.tsx`)
- Hotel pages are already hidden. For dental, use the labels Patients, Consultations & appointments, Treatments & fees and Captured leads, with matching `VerticalSpec.labels` overrides in `dental.py`.

### 5.2 Hours and voice UI
- `hours-editor.tsx` already supports split shifts. Add an "Add lunch break" shortcut, and an Overflow-mode control that writes the existing `answering_mode`, next to the current Settings control.
- `voice-picker.tsx`: group voices by `accent` and pin Australian or British voices for AU or GB tenants. `Assistant.tsx` gets a tag input for `stt_keyterms`.

### 5.3 Treatment value and KPIs
- `Services.tsx` (dental only): a value input in major units, stored as minor units, and a category select.
- `core/services/insights.py` `value_report` adds a `dental` block:
  - `overflow_calls_answered`: from `answer_window` plus `overflow`.
  - `consultations_booked`: appointments whose service category is cosmetic or implant.
  - `emergency_triages_captured`
  - `missed_call_sms_recoveries`: from `recovery_sent_at`.
  - `estimated_pipeline_value_minor`: booked consult values plus the values of qualified leads that have no booking on the same `call_id`, which avoids counting a lead twice.
- These feed `routers/launch.py` results and the monthly and pilot report text in `services/tenant_jobs.py`.
- `TenantOverview.tsx` and `Results.tsx` show KPI tiles formatted with `Intl.NumberFormat(tenant.locale, {style:'currency', currency})`.

### 5.4 Tests
- Insights unit tests with seeded calls, appointments and leads, including the no-double-count rule.
- Dashboard type-check and build.

---

## Phase 6: AUD/GBP dental billing, compliant reactivation, regional deploy
⚠ The pasted spec ends at 6.1. Items 6.2 and 6.3 are an outline to confirm before this phase starts.

### 6.1 Multi-currency dental plans (`core/billing/plans.py`, `stripe_api.py`, `metering.py`, `costs.py`)
- Add `dental_essentials` and `dental_pro` plans, with `PRICE_BOOK` AUD and GBP prices, **a setup fee per plan per currency** (today `SETUP_FEE` is one value for all plans), included minutes and overage per minute.
- New `STRIPE_PRICE_DENTAL_*` settings and their `currency_options`.
- Signup shows the dental plans only for `vertical=dental`.
- `costs.py` gains AU and UK inbound telephony and SMS rates, so `margin` stays correct.
- Check that the metering path bills overage in the tenant's currency.
- 💰 You set the price numbers; the code ships placeholders.

### 6.2 Compliant patient reactivation (outline)
- SMS-first recall of lapsed patients, using `core/outbound` (`gate.py`, `rules.py`, `optout.py`, `dnc_list`) with new rule sets:
  - AU: Spam Act 2003 (consent, sender identification, unsubscribe) and the DNCR Act for calls, with AU calling hours.
  - UK: PECR regulation 22 soft opt-in for existing patients, and TPS screening for calls.
- Recalls come from PMS data (Phase 3), never from a list.

### 6.3 Regional deploy (outline)
- Add `"au"` and `"uk"` to `TenantConfig.region`. `routers/signup.py` `region_for()` maps AU → au and GB → uk.
- One stack per region: Mongo, S3 (`RECORDING_S3_REGION`) and Redis in ap-southeast-2 or eu-west-2, plus a LiveKit region.
- Env templates in `deploy/docker-compose.prod.yml` and `deploy/README.md`.
- Signup sends visitors to the regional API by country.
- Add a runbook section.

---

## Verification (after every phase)
- `cd AI-receptionist-1 && uv run pytest -q`: the full suite, including `test_isolation_guard.py`, `test_api_tenancy.py`, and `test_case_files_are_valid` for the eval JSON.
- `uv run ruff check .` and `uv run mypy`.
- `npm run build` (or `tsc --noEmit`) in `dashboard/` and `platform-dashboard/`.
- With an OpenAI key: `RUN_EVALS=1 uv run pytest tests/evals/test_case_evals.py -k dental`.
- Manual checks:
  - After Phase 1: create a dental tenant through the platform API, then inspect `build_voice_prompt` for AU and GB.
  - After Phase 2: check the greeting at 12:55, 13:30 and 20:00 local time.
  - After Phase 3: run against the Cliniko sandbox.
  - After Phase 5: open the dashboard Results page as a dental tenant.

---

## Implementation status (2026-11-20)

All checks green after each phase: backend `pytest` 581 passed / 15 skipped (the skips are the
paid real-LLM evals, which need `RUN_EVALS=1`, and LiveKit-runtime tests that can't import on Windows),
`ruff` and `mypy` clean; `packages/shared` type-check, lint across all workspaces, and both dashboard
builds clean. `scripts/synthetic_call.py` and `scripts/latency_report.py` still load and read the same
call fields.

| Phase | Status | Main files |
|---|---|---|
| 1 Dental vertical, guardrails, tools, evals | ✅ done | `core/prompts/verticals/dental.py`, `core/tools/dental.py`, `tests/evals/cases/seed_dental_*.json`, `tests/test_dental_tools.py` |
| 2 Localisation, greeting states, missed-call recovery, AU/UK number metadata | ✅ done | `core/tenancy/models.py` (`answer_window`, `greeting_text`, `LegalEntity`), `core/services/recovery.py`, `agent/persistence.py`, `tests/test_dental_phase2.py` |
| 3 Cliniko and Dentally | ✅ done (mock-tested) | `core/services/scheduling_provider.py`, `core/integrations/cliniko.py`, `core/integrations/dentally.py`, `core/services/pms_sync.py`, `dashboard/src/components/app/pms-card.tsx`, `tests/test_dental_pms.py` |
| 4 Health-data privacy | ✅ done | `core/log.py`, `core/sensitive.py`, `migrations/m0005_encrypt_clinical_text.py`, `docs/legal/*`, `docs/COMPLIANCE.md` §7b, `tests/test_dental_privacy.py` |
| 5 ROI dashboard and UI | ✅ done | `core/services/insights.py` (`dental` block), `dashboard/src/components/app/dental-roi.tsx`, `Settings.tsx` ("Your practice"), `Services.tsx`, `tests/test_dental_roi.py` |
| 6.1 AUD/GBP dental plans | ✅ done | `core/billing/plans.py`, `core/billing/costs.py`, `routers/billing.py`, `routers/signup.py`, `tests/test_dental_billing.py` |
| 6.2 Compliant patient reactivation | ✅ done | `core/services/reactivation.py`, `core/outbound/gate.py` (`evaluate_recall`), `core/outbound/rules.py`, `ReactivationSettings` in `core/tenancy/models.py`, `list_lapsed_patients` in `cliniko.py` / `dentally.py`, `routers/telephony.py` (STOP), `dashboard/src/components/app/recall-card.tsx`, `tests/test_patient_reactivation.py` |
| 6.3 Regional deploy and preflight | ✅ done | `config.py` (`DEPLOYMENT_REGION`, `TWILIO_EDGE`/`TWILIO_REGION`, `*_ZDR_ENABLED`, `CLINIKO_SHARD`, `DENTALLY_API_BASE_URL`), `deploy/regions/*.env.example`, `deploy/deploy.sh`, `deploy/README.md` → "Regional stacks", `scripts/check_env.py` (`dental_preflight`), `tests/test_regional_deploy.py` |

### Where the build differs from the plan
- **Provider interface:** `SchedulingService` itself is the built-in provider. When a PMS is connected
  it delegates to the provider (`self.pms`). There is no separate `BuiltinProvider` wrapper, which
  kept the tools and dashboard unchanged.
- **Call summaries stay plaintext.** Every call list shows them. Transcripts and lead symptoms and
  notes are encrypted. Follow-up: encrypt summaries for dental practices too, and decrypt them in the
  call lists.
- **The ZDR guard is about config, not tenants.** Production refuses to start with
  `DEEPGRAM_MIP_OPT_OUT=false` or `OPENAI_STORE=true`. `OPENAI_ZDR_ENABLED` (was `LLM_ZERO_DATA_RETENTION`) is a documentation
  flag only.
- **Greeting states (lunch, after hours, overflow) and the recording notice first** apply to dental
  practices without a custom greeting. Other verticals are unchanged.
- **Dentally is off by default** (`PMS_DENTALLY_ENABLED=false`) until there's partner access and it has
  been tested against the sandbox.

### Phase 6.2 and 6.3 notes
- **Recall is its own tenant-scoped system.** The sales dialer's leads and campaigns are platform-level,
  so recall is not built on them. Recall rows live in the tenant's `recalls` collection, and the sales
  campaign model refuses `campaign_type="patient_reactivation"`. The sales gate also refuses anything
  that isn't `sales_outbound`.
- **Recall rules:**
  - Only patients from the PMS are eligible.
  - The consent basis is logged on every send: AU `explicit_opt_in` or `au_inferred_existing_patient`;
    UK `explicit_opt_in` or `uk_pecr_soft_opt_in`. Inferred consent needs a visit within 24 months, and a
    PMS "no marketing" flag always blocks.
  - Mobile numbers only.
  - Sending hours are weekdays 09:00–18:00 and Saturday 10:00–16:00, never Sunday, and can never pass
    08:00–20:00.
  - At most one text per 30 days (stored history plus a dedupe lock) and two per recall cycle.
  - Template copy is checked for clinical terms, `{clinic_name}` and STOP.
  - Every send records the Twilio SID, a hash of the message and the consent basis.
  - A booking within 14 days is credited to the recall (`attributed_booking_id`) and ends the cycle.
- **STOP handling:** a STOP cancels every queued recall text for that number at once. Dental practices
  reply "You've been unsubscribed from {clinic} reminders."
- **Voice recall** stays off (`voice_enabled=false`). The gate already requires a DNCR (AU) or TPS/CTPS
  (UK) check unless express voice consent is recorded. UK voice calls in the sales dialer now always
  need TPS/CTPS, even with a consent record.
- **Renamed settings:** `LLM_ZERO_DATA_RETENTION` → `OPENAI_ZDR_ENABLED` and `DENTALLY_API_BASE` →
  `DENTALLY_API_BASE_URL`, to match the 6.3 spec.
- **Production guard:** it also refuses unknown `TWILIO_EDGE` and `DEPLOYMENT_REGION` values.
  `deploy.sh` refuses a region mismatch between the env and deploy files, and refuses `*-au` / `*-uk`
  environments with the wrong region.
- **Fixed along the way:** a flaky STOP/START ordering in `core/compliance/optout.is_opted_out`.
  Events in the same millisecond tied; `_id` now breaks the tie.

### Before the first dental customer (⚠ VERIFY)
- **Cliniko:** run the adapter against a real test account. Check `starts_at`/`ends_at`, the cancel
  endpoint and reason code, the `available_times` window, and patient search by `last_name`. Set
  `CLINIKO_USER_AGENT`.
- **Dentally:** check every endpoint and field against the sandbox, then set `PMS_DENTALLY_ENABLED=true`.
- **Deepgram:** confirm Nova-3 accepts `en-AU` / `en-GB` and the current keyterm limit (capped at 100).
- **Evals:** run the dental evals with a real key:
  `RUN_EVALS=1 uv run pytest tests/evals/test_case_evals.py -k dental`.
- **Prices:** set real ones in `PRICE_BOOK` / `DENTAL_SETUP_FEES`, create the Stripe Prices
  (`STRIPE_PRICE_DENTAL_*`), and check the AU/UK carrier and SMS rates in `costs.py`.
- **Counsel:** review DPA §8, the privacy policy's dental section and `COMPLIANCE.md` §7b. Have a
  dentist review the red-flag list and triage wording.
- **WhatsApp:** missed-call texts sent by WhatsApp need an approved Meta template, so SMS is the default.
- **Recall:**
  - Check the field names in Cliniko and Dentally `list_lapsed_patients`: `did_not_arrive`,
    `cancelled_at`, `phone_type`, `accepted_sms_marketing`, the Dentally appointment `state` and
    `marketing`.
  - Have counsel confirm the AU inferred-consent and UK soft opt-in rules and the clinical-terms list.
  - Load the AU DNCR and UK TPS/CTPS registers before ever turning voice recall on.
- **Regional stacks:**
  - Create Twilio credentials in AU1 / IE1.
  - Check which Twilio products are available in each region.
  - Run `scripts/check_env.py` on each host and fix every ❌ before go-live.
