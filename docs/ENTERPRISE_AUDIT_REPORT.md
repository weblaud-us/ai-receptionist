# Master Enterprise Launch Audit Report: Commercial Readiness Review

**Target Product**: `weblaud-us/ai-receptionist` (FastAPI Voice & Telephony Engine, Multi-Tenant Dashboard, Platform Super-Admin Dashboard, Shared Component Kit, Astro Landing Engine)  
**Target Market**: Private & Mixed Cosmetic/Implant Dental Clinics in Australia (AU) and the United Kingdom (UK)  
**Evaluation Standard**: Google-Grade Launchcal, Security Review, Healthcare Privacy Review, Chaos Engineering & SRE Production Readiness Review (PRR)  
**Launch Review Panel**: 8-Person Principal/Staff Engineering Launch Review Panel  
**Launch Chair & Principal TPM**: Role 8 — Launch Chair / Principal TPM  
**Date of Audit**: 2026-11-22  
**Audit Mode**: Read-Only Adversarial Live Codebase & Evidence-Based Verification  

---

## 1. Executive Launch Verdict

### Explicit Launch Decision: ⛔ **NO-GO (Commercial Launch Blocked)**

### Executive Narrative & Statutory Liability Assessment
Following an adversarial, multi-disciplinary launch review across all 8 Google-grade engineering review panels and live sandbox test suite executions, the panel has unanimously issued a **NO-GO** determination for the commercial release of `weblaud-us/ai-receptionist` in Australia and the United Kingdom.

While the monorepo exhibits high technical ambition—incorporating real-time WebRTC audio processing via LiveKit, modern conversational turn detection, automated Cliniko and Dentally practice management adapters, and a polished WCAG AA compliant Astro landing experience—the system in its current state contains **21 Critical P0 Launch Blockers** and **30 P1 High Risks**. Deploying this software to live private and mixed dental practices today presents unacceptable legal, patient safety, financial, and operational liability:

1. **Criminal Wiretapping & Telephony Sanctions**: In Australia's two-party consent jurisdictions (NSW *Surveillance Devices Act 2007* s 7, Victoria *Surveillance Devices Act 1999* s 11, SA, WA, Qld), recording a telephone call without clear prior notice or explicit consent is a criminal offense carrying up to 5 years imprisonment and corporate penalties of $55,000 AUD per violation. The audio pipeline begins streaming and committing caller audio packets to S3 **up to 2,000ms before the automated recording disclaimer finishes playing** (`PRIV-01`), and provides zero voice tools or listeners for a patient to withdraw consent or halt recording mid-call (`PRIV-02`). In both AU and the UK, callers objecting to recording are ignored while audio recording continues in the background.
2. **Special Category Health Data & GDPR/APP Exposure**: Under UK GDPR Article 9 and Australian Privacy Principle (APP) 3.3, collecting clinical symptoms (e.g. dental trauma, facial swelling, acute pain, bleeding) without explicit consent is unlawful (`PRIV-04`). Third-party AI sub-processors (OpenAI, Deepgram, ElevenLabs, Cartesia) are instantiated without active Zero Data Retention (ZDR) enforcement (`PRIV-03`), transmitting unredacted patient clinical transcripts and telephone numbers to external servers without `store=False`. Furthermore, clinical summaries are stored unencrypted in MongoDB and broadcast over unencrypted SMS and webhooks (`PRIV-08`). In the event of regulatory enforcement, maximum statutory penalties reach **£17.5M / 4% global annual turnover (UK ICO)** and **$50,000,000 AUD (OAIC)**.
3. **Statutory Spam Violations (ACMA & UK PECR)**: Outbound missed-call recovery SMS messages are dispatched automatically to any inbound caller who hangs up after 4 seconds (`PRIV-05`). Under the Australian *Spam Act 2003* s 16 and UK *PECR 2003* Reg 22, an abandoned inbound call does NOT constitute express or inferred consent. ACMA infringement notices reach up to **$330,000 AUD per day** for repeat automated marketing text violations.
4. **Direct Patient Safety & Emergency Hallucination Hazards**: In `core/prompts/verticals/dental.py`, the dental-specific prompt facts completely omit Australia's emergency number (`000`) and Healthdirect hotline (`1800 022 222`), and fail to distinguish UK NHS `111` nurse triage from life-threatening `999` airway emergencies (`VOICE-02`). Because new tenant signups default to `country="US"`, patients suffering from life-threatening Ludwig's angina or severe maxillofacial trauma are told to *"hang up and dial 911"*. Additionally, callers cannot interrupt the AI due to audio packet drops during playback (`VOICE-03`), and external PMS rate limits cause 429 cascades that drop callers during acute triage (`BACK-02`).
5. **Catastrophic Infrastructure Compromise & Live Secrets**: Active production credentials—including live MongoDB Atlas connection strings with administrative credentials, OpenAI production keys, Twilio account auth tokens, LiveKit cloud secrets, Resend API keys, and Google OAuth client secrets—were committed in cleartext to local environment and backup files (`SEC-15`). Furthermore, a TOCTOU DNS rebinding SSRF in the website scraper (`SEC-11`) allows remote attackers to compromise AWS IAM instance metadata (IMDSv1).
6. **Clinical Split-Brain & Diary Double-Booking**: On appointment booking, if an external PMS reservation succeeds in Cliniko or Dentally but the subsequent local database write fails, local slot locks are deleted without rolling back the PMS booking (`SRE-01`). The diary remains reserved externally while the AI re-offers and re-books the same physical chair to subsequent callers, creating unrecoverable clinical diary collisions.
7. **Release Pipeline Collapse**: The backend CI typechecker (`mypy`) currently fails with fatal type errors on `main` (`QA-01`), database migrations permanently destroy plaintext data preventing git rollbacks (`QA-03`), and across all four frontend packages there is **zero automated testing** (`QA-02`) and no React error boundaries (`FRONT-01`), leaving clinic receptionists vulnerable to unrecoverable white-screen crashes.

---

### 10-Point Enterprise Readiness Scorecard

| Evaluation Domain | Domain Weight | Raw Score (0–10) | Weighted Score | Launch Status | Key Domain Vulnerability |
|---|:---:|:---:|:---:|:---:|---|
| **1. Security & AppSec** | 15% | **3.5 / 10** | 0.525 | 🔴 **BLOCKER** | Committed live cloud secrets (`.env`); TOCTOU SSRF in scraper; disabled Twilio signatures. |
| **2. Healthcare Privacy & Regulatory** | 20% | **2.0 / 10** | 0.400 | 🔴 **BLOCKER** | Two-party consent race; no recording revocation; ZDR failure; Spam Act SMS violations. |
| **3. SRE & Distributed Systems Reliability** | 15% | **4.5 / 10** | 0.675 | 🔴 **BLOCKER** | Split-brain diary double booking; deploy call severance; Redis 256MB noeviction OOM lockup. |
| **4. Real-Time Voice, Telephony & AI Safety** | 15% | **3.0 / 10** | 0.450 | 🔴 **BLOCKER** | Dual-stack split-brain hotel routing; missing AU 000/Healthdirect; default 911; barge-in lockout. |
| **5. PMS Integrations & Stripe Billing** | 15% | **4.0 / 10** | 0.600 | 🔴 **BLOCKER** | Family phone collision & no DOB; PMS 429 fan-out cascade; distributed slot lock bricking. |
| **6. Frontend, Design Systems & Performance** | 10% | **5.5 / 10** | 0.550 | 🟠 **HIGH RISK** | Missing React error boundaries; unrestricted staff audio access; 3.5s preloader LCP delay. |
| **7. CI/CD, Chaos & QA Quality Gate** | 10% | **4.5 / 10** | 0.450 | 🔴 **BLOCKER** | 10 fatal mypy errors in CI; 0 frontend tests; irreversible migrations; unauthenticated Redis. |
| **OVERALL ENTERPRISE READINESS** | **100%** | — | **3.65 / 10** | ⛔ **NO-GO** | **Commercial launch blocked pending mandatory Sprint 0 P0 remediation.** |

---

## 2. Master Finding Registry

The findings across all 7 specialist domains have been cross-referenced, deduplicated, and classified strictly according to standard enterprise severity thresholds:
- **P0 — Launch Blocker (21 Findings)**: Critical security breaches, regulatory non-compliance, clinical safety hazards, data corruption, or total system unavailability. Must be remediated and verified before any commercial traffic or patient calls.
- **P1 — High Risk (30 Findings)**: Severe architectural flaws, rate limiting vulnerabilities, lack of circuit breakers, unhedged operational risks, or major SEO/performance degradations. Must be resolved before scaling beyond private beta.
- **P2 — Enterprise Hardening (17 Findings)**: Multi-site governance gaps, RBAC deficiencies, missing distributed tracing, localization and database index optimizations.
- **P3 — Nice-to-Have / Repository Hygiene (5 Findings)**: Code cleanup, dev-environment timeouts, mock test isolations, and marketing audio previews.

---

### P0 — Launch Blockers (All 21 Critical Blockers)

#### Finding P0-01 (SEC-15): Live Production Cloud Credentials & Sensitive API Keys Committed in Cleartext
- **Role**: Role 1 (Security & AppSec)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/.env:26-202`, `AI-receptionist-1/.env.bak-*`
- **Failure / Exploit Scenario**:
  Live credentials for external cloud providers were committed in plaintext in `.env` and multiple backup files (`.env.bak-before-phase0`, `.env.bak-before-phase4`, `.env.bak-before-twilio-group`, `.env.bak-before-comment`):
  - `MONGODB_URI`: MongoDB Atlas cluster `cluster0.giqhx.mongodb.net` with user `jsa` and cleartext password (`.env:26`).
  - `OPENAI_API_KEY`: Production OpenAI API key (`sk-proj-...`) (`.env:35`).
  - `DEEPGRAM_API_KEY`: Production Deepgram streaming key (`.env:52`).
  - `TWILIO_ACCOUNT_SID` & `TWILIO_AUTH_TOKEN`: Twilio master telephony credentials (`.env:65-66`).
  - `TWILIO_SIP_OUTBOUND_USERNAME` & `TWILIO_SIP_OUTBOUND_PASSWORD`: Live SIP trunk credentials (`.env:70-71`).
  - `LIVEKIT_API_KEY` & `LIVEKIT_API_SECRET`: LiveKit Cloud control plane keys (`.env:113-114`).
  - `RESEND_API_KEY`: Email dispatcher API key (`.env:197`).
  - `GOOGLE_OAUTH_CLIENT_SECRET`: Google OAuth secret (`.env:201`).
  - `META_WA_ACCESS_TOKEN`: Meta WhatsApp System User Access Token (`.env:107`).
  Any operator, developer workstation, or compromised dependency reading the repository directory has instant root access to live telephony infrastructure (enabling toll fraud), database contents (exposing all patient records), and AI vendor APIs.
- **Clinical / Business / Legal Impact**:
  Total infrastructure compromise. Immediate catastrophic patient health data breach, thousands of dollars in toll fraud liability, and mandatory regulatory breach disclosures under the Australian NDB scheme and UK GDPR Article 33.
- **Concrete Remediation Steps**:
  1. Immediately revoke and rotate all compromised keys across vendor administrative consoles (MongoDB Atlas, OpenAI, Deepgram, Twilio, LiveKit Cloud, Resend, Meta, Google Cloud).
  2. Permanently delete all `.env` and `.env.bak-*` files from developer environments and repository history.
  3. Enforce that production secrets are injected exclusively via runtime environment variables managed by AWS Secrets Manager, HashiCorp Vault, or Doppler.

---

#### Finding P0-02 (SEC-11): Time-of-Check to Time-of-Use (TOCTOU) DNS Rebinding SSRF in Website Scraper
- **Role**: Role 1 (Security & AppSec)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/core/services/site_import.py:95-105, 124-143`
- **Failure / Exploit Scenario**:
  In `site_import.py`, `_check(url)` validates that the hostname resolves to a public IP via `socket.getaddrinfo()` (`_host_is_public`). However, the subsequent request is performed by `client.stream("GET", url)` using `httpx.AsyncClient`, which executes an independent second DNS resolution.
  An attacker provides a malicious domain configured with DNS rebinding (TTL=0). The initial lookup returns a public IP (e.g. `1.1.1.1`), passing `_host_is_public`. The second lookup during `client.stream` resolves to `169.254.169.254` (AWS EC2 Instance Metadata Service). The scraper retrieves instance IAM role security credentials and returns them in the scraped text payload.
- **Clinical / Business / Legal Impact**:
  Full cloud infrastructure takeover. Cloud IAM credentials permit unauthorized access to S3 audio buckets, production databases, and VPC networks.
- **Concrete Remediation Steps**:
  1. Pin the verified IP address: refactor the HTTP transport to connect directly to the validated IP while retaining the virtual host in the `Host` header and SNI extension.
  2. Enforce AWS IMDSv2 (`HttpTokens=required`, `HttpPutResponseHopLimit=1`) across all host instances to neutralize metadata SSRF.

---

#### Finding P0-03 (SEC-08): Hardcoded `VERIFY_TWILIO_SIGNATURE=false` in Committed Environment Files
- **Role**: Role 1 (Security & AppSec)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/.env:81-82`
- **Failure / Exploit Scenario**:
  In `AI-receptionist-1/.env`, `VERIFY_TWILIO_SIGNATURE` and `VERIFY_TWILIO_WS_SIGNATURE` are hardcoded to `false`. In any non-production deployment (or production instances where `APP_ENV` is improperly set), incoming webhooks on `/telephony/sms/inbound` and `/voice/incoming` accept unauthenticated POST requests without verifying the `X-Twilio-Signature` HMAC header.
  An attacker posts forged inbound SMS payloads with `Body=STOP` from arbitrary phone numbers, automatically opting patients out of clinical appointment reminders, or injects forged voice calls to manipulate triage queues.
- **Clinical / Business / Legal Impact**:
  Unauthorized manipulation of patient communication opt-outs, injection of fraudulent triage leads, and Denial of Service against clinical notifications.
- **Concrete Remediation Steps**:
  1. Remove `VERIFY_TWILIO_SIGNATURE=false` from all environment files.
  2. Default signature verification to `True` unconditionally across all configurations; allow disabling only in dedicated hermetic test suites with mocked request objects.

---

#### Finding P0-04 (PRIV-01): Two-Party Recording Consent Race Condition & Silent Recording Bypass
- **Role**: Role 2 (Healthcare Privacy & Regulatory)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/agent/worker.py:278`, `AI-receptionist-1/agent/recording.py:32-57`, `AI-receptionist-1/core/tenancy/models.py:89-94, 709-715`
- **Failure / Exploit Scenario**:
  1. In `agent/worker.py:278`, when an inbound SIP or WebRTC call connects, `entrypoint()` immediately executes `asyncio.create_task(_record(ctx, state))` before the caller hears the greeting. LiveKit Room Composite Egress begins capturing, encoding, and streaming audio packets to S3.
  2. Network audio buffering and TTS streaming delay mean the caller does not hear `"Just so you know, this call may be recorded"` until 500ms to 2000ms into the call. If the caller speaks immediately upon pickup, their voice is captured prior to notice.
  3. In `core/tenancy/models.py:89-94, 709-715`, `RecordingConfig` provides an independent boolean `announce: bool = True`. If a clinic sets `recording.enabled = True` and `recording.announce = False`, the system covertly records the call without playing any disclosure.
- **Clinical / Business / Legal Impact**:
  Criminal liability under Australian state wiretapping statutes (NSW *Surveillance Devices Act 2007* s 7, Victoria, Queensland, SA, WA), carrying fines up to $55,000 AUD and 5 years imprisonment. In the UK, breach of *Investigatory Powers Act 2016* s 3 and UK GDPR Article 6/9, triggering ICO enforcement and fines up to £17.5M.
- **Concrete Remediation Steps**:
  1. Decouple egress startup from call entry: modify `agent/worker.py` to ensure LiveKit Egress is initiated ONLY after the greeting audio playout has completed (`await handle.wait_for_playout()`).
  2. Remove the `rec.announce = False` capability in `core/tenancy/models.py`; enforce that whenever recording is active in AU or UK jurisdictions, the statutory recording disclosure is mandatory and cannot be disabled.
  3. In two-party AU jurisdictions, prepend a deterministic telephony pre-chime or DTMF consent gate before routing audio to the agent.

---

#### Finding P0-05 (PRIV-02): Absence of In-Call Recording Objection / Revocation Functionality
- **Role**: Role 2 (Healthcare Privacy & Regulatory)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/agent/receptionist.py:53-138`, `AI-receptionist-1/agent/tools.py:24-30`, `AI-receptionist-1/core/prompts/builder.py:27-62`
- **Failure / Exploit Scenario**:
  When a patient hears the recording announcement and states: *"I do not consent to being recorded, please turn it off"* or *"Stop recording"*, the AI receptionist has no programmatic tool to halt recording. `ReceptionistAgent` only exposes `end_call` and `transfer_to_human`.
  The LLM generates an empathetic conversational reply (*"I understand, let's proceed without recording"*), but LiveKit Egress continues streaming and encoding the caller's audio to S3 in the background. The call fails open and continues recording against the caller's express prohibition.
- **Clinical / Business / Legal Impact**:
  Continuing to record after express revocation constitutes intentional unlawful interception under Australian surveillance devices legislation and violates UK GDPR Article 7(3) (Right to Withdraw Consent) and Article 21 (Right to Object). Recorded audio is legally inadmissible and subject to mandatory destruction orders.
- **Concrete Remediation Steps**:
  1. Implement a `@function_tool()` named `stop_recording` in `agent/receptionist.py` that immediately triggers `job.api.egress.stop_egress(api.StopEgressRequest(egress_id=...))` and clears the recording state.
  2. Add an explicit deterministic rule in `core/prompts/builder.py:BASE_VOICE_RULES`: if the caller refuses recording consent, invoke `stop_recording` immediately, confirm recording has ceased, and continue the call.
  3. If egress cannot be halted, the agent must offer transfer to an unrecorded line or politely terminate the call (`end_call`).

---

#### Finding P0-06 (PRIV-03): Zero Data Retention (ZDR) Failure & AI Model Training Exposure Across AI Vendors
- **Role**: Role 2 (Healthcare Privacy & Regulatory)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/config.py:84-88`, `AI-receptionist-1/agent/summary.py:76-81`, `AI-receptionist-1/agent/session_factory.py:90-114, 186-196`
- **Failure / Exploit Scenario**:
  In `config.py:84-88`, `OPENAI_ZDR_ENABLED` and `DEEPGRAM_ZDR_ENABLED` default to `False`.
  In `agent/summary.py:76-81`, post-call clinical summarization calls `client.chat.completions.create` with full clinical transcripts containing patient names, phone numbers, and acute symptoms, but **omits `store=False` or `extra_body={"store": False}`**.
  Similarly, `agent/session_factory.py` instantiates OpenAI RealtimeModel, Deepgram STT, ElevenLabs TTS, and Cartesia TTS without Zero Data Retention flags or signed Business Associate Agreements (BAAs). Patient clinical audio and text are retained on US third-party vendor servers for 30 days and exposed to vendor model training pipelines.
- **Clinical / Business / Legal Impact**:
  Direct violation of UK GDPR Article 28(3)(a) and Article 9, and Australian Privacy Principle 8 (Cross-border disclosure of personal information). Fraudulent misrepresentation in platform Data Processing Agreements (DPAs) which falsely guarantee ZDR compliance to clinic owners.
- **Concrete Remediation Steps**:
  1. Pass `extra_body={"store": False}` on all OpenAI chat completion requests (`agent/summary.py:77`, `ai/llm_provider.py:66`).
  2. Pass `mip_opt_out=True` on Deepgram streaming sessions (`session_factory.py:76`).
  3. Update `config._production_guard` and `agent/worker.py` to raise `SystemExit` if `OPENAI_ZDR_ENABLED` or `DEEPGRAM_ZDR_ENABLED` is False in production.
  4. Execute enterprise DPAs and UK Addendums with all AI sub-processors prohibiting data retention and training.

---

#### Finding P0-07 (PRIV-04): Unlawful Processing of Special Category Health Data Without Explicit Consent
- **Role**: Role 2 (Healthcare Privacy & Regulatory)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/agent/receptionist.py:63-78`, `AI-receptionist-1/core/tools/dental.py:144-198`, `AI-receptionist-1/core/prompts/verticals/dental.py:15-46`
- **Failure / Exploit Scenario**:
  When a patient describes symptoms (bleeding gums, severe toothache, jaw trauma, broken crown), the system invokes `triage_dental_emergency` in `core/tools/dental.py`, transcribes the health symptoms, classifies clinical urgency, and persists the health data in MongoDB.
  At no point in the interaction does the system obtain explicit consent to collect and process health data, nor does it present an Australian Privacy Principle 5 collection notice.
- **Clinical / Business / Legal Impact**:
  UK GDPR Article 9(1) prohibits processing special category health data without Article 9(2)(a) explicit consent or Article 9(2)(h) healthcare management under professional secrecy safeguards. Australian Privacy Act APP 3.3 strictly prohibits collecting sensitive health information without consent. Violations trigger immediate regulatory stop-processing notices and OAIC civil penalties up to $50,000,000 AUD.
- **Concrete Remediation Steps**:
  1. Implement a clinical triage consent interlock in `core/prompts/verticals/dental.py`: before recording symptom descriptions, the agent must ask: *"To assist our dental team, may I record your symptoms for the clinic? Do you agree?"*
  2. Implement an automated APP 5 collection notice delivered via SMS confirmation or clear audio disclosure.
  3. Include an explicit controller-processor clause in customer contracts establishing that the AI operates under the direct supervision of the practice's registered Principal Dentist.

---

#### Finding P0-08 (PRIV-05): Spam Act 2003 & UK PECR Violations via Automated Missed-Call Hangup Marketing SMS
- **Role**: Role 2 (Healthcare Privacy & Regulatory)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/core/services/recovery.py:51-62`, `AI-receptionist-1/agent/persistence.py:173-179`
- **Failure / Exploit Scenario**:
  At the end of every inbound call, `_schedule_recovery()` evaluates `wants_recovery()`. If an inbound caller disconnects after hearing the greeting (`ended_reason="caller_hangup"`), `wants_recovery()` returns `True`. Exactly 10 seconds later, the system dispatches an unsolicited outbound promotional SMS:
  *"Thanks for calling North Sydney Dental. Sorry we didn't get you booked in. Book online any time: https://... Reply STOP to opt out."*
  The recipient never opted in or provided consent to receive marketing messages.
- **Clinical / Business / Legal Impact**:
  An inbound hangup does NOT constitute inferred consent under Schedule 2 of the Australian *Spam Act 2003* or UK *PECR 2003* Regulation 22 (soft opt-in is invalid because no sale or negotiations took place). ACMA penalties reach up to **$330,000 AUD per day** for repeat spam violations.
- **Concrete Remediation Steps**:
  1. Modify `core/services/recovery.py` to prohibit automated SMS recovery on unconsented abandoned calls.
  2. Restrict recovery SMS exclusively to known, registered patients who have active, verified `sms_marketing_consent = True` in Cliniko or Dentally.

---

#### Finding P0-09 (SRE-01): Split-Brain Inconsistency & Diary Double-Booking on External PMS Booking Failure
- **Role**: Role 3 (Distributed Systems & SRE)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/core/services/scheduling.py:301-342`
- **Failure / Exploit Scenario**:
  In `SchedulingService.create_booking`:
  1. Line 295 acquires local slot locks in `slot_locks`.
  2. Line 302-306 creates the appointment in the external PMS (Cliniko or Dentally), successfully reserving the dentist's chair on the external cloud calendar.
  3. Line 339 attempts to insert the appointment record into local MongoDB `appointments`.
  If the local database insert fails (due to connection timeout, primary election step-down, or container restart), the `except Exception:` block executes line 341: `await self.repos.slot_locks.delete_many({"appointment_id": appointment_id})`.
  There is **NO compensating transaction** (`pms.cancel_appointment`) executed against Cliniko or Dentally! The external diary holds the booking, while local locks are wiped. Subsequent callers are offered the exact same slot, leading to an unrecoverable double booking.
- **Clinical / Business / Legal Impact**:
  Severe clinical operations disruption. Two patients arrive simultaneously for the same surgery chair; clinic partners lose confidence in the platform; commercial SLA breach.
- **Concrete Remediation Steps**:
  1. Wrap the local insert in an explicit two-phase compensating transaction: if local persistence fails, immediately call `await self.pms.cancel_appointment(created.id, reason="System persistence rollback")`.
  2. If rollback fails, enqueue an urgent compensation job in `jobs/worker.py` and alert clinic administration.

---

#### Finding P0-10 (SRE-02): Abrupt Telephony Call Severance & Inbound Outage During Deploys on Legacy Twilio Path
- **Role**: Role 3 (Distributed Systems & SRE)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/voice/twilio_handler.py:50, 229-238`, `deploy/docker-compose.prod.yml:43`, `deploy/deploy.sh:57-64`
- **Failure / Exploit Scenario**:
  When `LEGACY_TWILIO_VOICE=true` is enabled, `deploy.sh` restricts the API deployment to a single replica (`api-a`) because Twilio Media Stream WebSockets connect directly to an in-memory dictionary (`active_sessions`).
  When `deploy.sh` executes `dc up -d --wait --no-deps api-a`, Docker stops `api-a` with a 30s grace period. Active patient calls are abruptly severed mid-sentence. Furthermore, during the 15-30 second restart window, there are ZERO running API replicas, causing all incoming phone calls to fail with busy signals or disconnects.
- **Clinical / Business / Legal Impact**:
  Violates zero-downtime deployment guarantees. Patients in emergency situations are dropped; inbound clinic lines go completely dead during releases.
- **Concrete Remediation Steps**:
  1. Completely decommission and block `LEGACY_TWILIO_VOICE` from production environments in `config._production_guard`.
  2. Enforce 100% LiveKit SIP Trunking, which connects to independent agent worker pods supporting full graceful draining (`LIVEKIT_DRAIN_TIMEOUT`).

---

#### Finding P0-11 (VOICE-01): Dual Voice Stack Split-Brain & Legacy Hotel Receptionist Exposure in Production
- **Role**: Role 4 (Voice, Telephony & AI Safety)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/main.py:51`, `AI-receptionist-1/voice/twilio_handler.py:36, 42, 123-128, 563-572`, `AI-receptionist-1/ai/prompts.py:110-113`
- **Failure / Exploit Scenario**:
  The repository maintains two diverged voice stacks. The legacy Twilio handler (`voice/twilio_handler.py`) is unconditionally mounted at `/voice` in `main.py:51`.
  If an Australian or UK dental clinic purchases a number or configures their Twilio console with standard webhook URLs (`https://<domain>/voice/incoming`) instead of LiveKit SIP trunks, incoming patient calls hit `twilio_handler.py`. Callers are greeted by an AI claiming to be a hotel clerk in Bangladesh quoting room bookings in "three thousand taka" and check-ins at 2 PM (`get_hotel_profile()`).
- **Clinical / Business / Legal Impact**:
  Total failure of dental triage, severe brand damage, patient distress, and complete loss of incoming clinic appointment revenue.
- **Concrete Remediation Steps**:
  1. Guard router registration in `main.py`: mount `voice.twilio_handler` only if `settings.LEGACY_TWILIO_VOICE` is True. In production, requests to `/voice/incoming` must return HTTP 410 Gone or redirect to the LiveKit SIP trunk.
  2. Permanently deprecate `voice/twilio_handler.py` and `ai/prompts.py`.

---

#### Finding P0-12 (VOICE-02): Emergency Number Failure & Hallucination: Missing AU 000 / Healthdirect in Dental Facts & Default 911 Risk
- **Role**: Role 4 (Voice, Telephony & AI Safety)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/core/prompts/verticals/dental.py:20-25, 49-76`, `AI-receptionist-1/core/tenancy/models.py:609-613`, `AI-receptionist-1/routers/signup.py:61-63`
- **Failure / Exploit Scenario**:
  In `core/prompts/verticals/dental.py`, instructions direct the agent: *"Your FIRST sentence tells them to call the emergency number in the facts now"*.
  However, in `_facts()`:
  - For Australia (`AU`), `000` (Triple Zero) and `Healthdirect` (`1800 022 222`) are NEVER injected into the facts.
  - For the UK (`GB`), facts list `NHS 111` for closed clinics, failing to distinguish out-of-hours advice from life-threatening airway emergencies (`999`).
  - In `core/tenancy/models.py:610` and `routers/signup.py:62`, new tenants default to `country="US"`, causing the fallback logic to inject `911`.
  An Australian patient suffering acute Ludwig's angina (spreading neck swelling compromising the airway) is instructed: *"Please hang up and dial 911 immediately"*. Dialing 911 from fixed VoIP lines or landlines in Australia fails.
- **Clinical / Business / Legal Impact**:
  Fatal clinical triage delay, patient mortality risk, dental malpractice claims, and violation of Dental Board of Australia (AHPRA) and General Dental Council (GDC) emergency standards.
- **Concrete Remediation Steps**:
  1. Update `core/prompts/verticals/dental.py:_facts` to inject explicit emergency numbers:
     - AU: `- Life-threatening emergencies (airway compromise, severe bleeding, trauma): call 000 immediately or attend nearest Emergency Department. After-hours non-life-threatening advice: call Healthdirect on 1800 022 222.`
     - UK: `- Life-threatening emergencies: call 999 or attend A&E. Urgent out-of-hours dental advice: call NHS 111.`
  2. Eliminate default `country="US"`; require explicit selection of `AU` or `GB` upon signup.

---

#### Finding P0-13 (VOICE-03): Complete Lockout of Barge-in / Interruption in Legacy Voice Stack & High 500ms Threshold in LiveKit Stack
- **Role**: Role 4 (Voice, Telephony & AI Safety)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/voice/twilio_handler.py:363-364`, `AI-receptionist-1/agent/session_factory.py:173-178`
- **Failure / Exploit Scenario**:
  1. In `voice/twilio_handler.py:363`, incoming caller media packets are explicitly discarded via `continue` whenever `sd.get("ai_speaking")` is True. The caller is completely locked out from interrupting the AI.
  2. In `agent/session_factory.py:173-178`, LiveKit interruption `min_duration` is set to `0.5` seconds (500ms). Natural emergency interjections ("Stop!", "Wait!", "No!") last 200ms–350ms. The turn detector rejects these utterances as background clicks, causing the AI to talk over a distressed patient.
- **Clinical / Business / Legal Impact**:
  Callers in acute pain cannot halt incorrect flows or re-direct the agent, causing panic and violating accessibility standards.
- **Concrete Remediation Steps**:
  1. In `agent/session_factory.py`, tune `min_duration` from `0.5s` to `0.25s` to permit single-word emergency barge-in.
  2. Remove packet discard logic in `twilio_handler.py` and implement Twilio `clear` buffer events on interruption.

---

#### Finding P0-14 (BACK-01): Critical Patient Matching Failure on Family Shared Phone Numbers & Missing Mandatory Date of Birth (DOB)
- **Role**: Role 5 (Backend Architecture, PMS & Billing)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/core/integrations/cliniko.py:158-166`, `AI-receptionist-1/core/integrations/dentally.py:112-120`, `AI-receptionist-1/core/services/scheduling_provider.py:80-84, 288-292`
- **Failure / Exploit Scenario**:
  1. In both Cliniko and Dentally PMS adapters, patient record lookup splits the caller's spoken name into `(first_name, last_name)` and returns the **first patient** matching `last_name` and phone number. When family members share a phone (e.g. parent booking for a child, or elderly couple), bookings and clinical notes are merged into the wrong patient's medical record.
  2. The `Patient` domain model lacks Date of Birth (`DOB`). In both Australia (RACGP/ADA) and the UK (NHS/GDC), matching patient health records without DOB is a direct violation of clinical identification standards.
  3. `same_phone` compares only the last 9 digits (`da[-9:] == db[-9:]`), stripping international dial codes and causing false positive collisions on 10-digit UK mobile numbers.
- **Clinical / Business / Legal Impact**:
  Severe clinical safety hazard. Pediatric dental records merged with parent records; medical allergies and contraindications attributed to the wrong person; violation of UK GDPR Article 9 and Australian Privacy Act HPP 1 & 2.
- **Concrete Remediation Steps**:
  1. Add `date_of_birth: Optional[date]` to `Patient` model in `scheduling_provider.py`.
  2. Update voice scheduling tools to collect DOB when disambiguating existing patients.
  3. Update `cliniko.py` and `dentally.py` to match on both First and Last Name and verify DOB; if multiple patients share a number and name, fall back to creating a new profile or front-desk task.
  4. Replace 9-digit slicing with full E.164 parsing via Google's `phonenumbers` library.

---

#### Finding P0-15 (BACK-02): Unbounded PMS Availability Fan-Out & Complete Absence of Client-Side Rate-Limiting Queue Causing 429 Cascades
- **Role**: Role 5 (Backend Architecture, PMS & Billing)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/core/integrations/cliniko.py:131-155, 216-238`, `AI-receptionist-1/core/services/scheduling_provider.py:225-248`
- **Failure / Exploit Scenario**:
  When checking availability, `ClinikoProvider.get_available_slots` issues sequential HTTP requests across up to 6 practitioners and 2 weeks (12 to 24 sequential requests per voice turn).
  There is **zero client-side rate limiting** (no leaky or token bucket). Cliniko enforces 150 req/min per clinic. During morning peak booking hours or concurrent background patient recall scans (which fire up to 260 sequential requests), Cliniko responds with `HTTP 429 Too Many Requests`. The voice assistant encounters `PMSError(429)` and aborts the call: *"The practice diary isn't answering right now..."*
- **Clinical / Business / Legal Impact**:
  High-value patient booking calls ($3,000–$15,000 AUD implant/cosmetic enquiries) are dropped during peak hours; risk of Cliniko API key revocation.
- **Concrete Remediation Steps**:
  1. Implement a distributed token bucket rate limiter in Redis enforcing a maximum of 100 req/min per PMS credential.
  2. Introduce a 30-to-60-second Redis availability cache (`pms_slots:{tenant_id}:{service_id}:{date}`).
  3. Throttle background recall jobs with 500ms inter-request pacing and restrict them to out-of-hours windows.

---

#### Finding P0-16 (BACK-03): Distributed Slot Locking Vulnerabilities: Unscoped "any" Resource Booking Failure & Missing TTL on `slot_locks`
- **Role**: Role 5 (Backend Architecture, PMS & Billing)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/core/services/scheduling.py:279-315`, `AI-receptionist-1/database/mongodb.py:236-240`
- **Failure / Exploit Scenario**:
  1. When a caller books with "anyone available", `resource_id` is passed as `"any"`. Line 306 attempts to pass `str((resource_doc or {}).get("pms_id") or "")` to Cliniko/Dentally, evaluating to `""`. The PMS rejects the request with HTTP 422 (practitioner required). Furthermore, locking `"any"` in `slot_locks` fails to lock the specific practitioner, allowing concurrent double bookings.
  2. MongoDB `slot_locks` has **no TTL index** (`expireAfterSeconds`). If an API worker crashes after line 296 (`insert_many`) but before appointment finalization, the 5-minute blocks remain locked **permanently**. Neither the AI nor clinic staff can ever book that slot again.
- **Clinical / Business / Legal Impact**:
  Permanent phantom unavailability; double-booked chairs; revenue loss for dental practices.
- **Concrete Remediation Steps**:
  1. In `scheduling.py:book`, resolve `resource_id="any"` to a concrete available practitioner BEFORE acquiring locks.
  2. Add a MongoDB TTL index on `slot_locks` (`expires_at` with `expireAfterSeconds=0`, defaulting to `created_at + 15m`).

---

#### Finding P0-17 (FRONT-01): Complete Absence of React Error Boundaries Causing Unrecoverable White Screens
- **Role**: Role 6 (Frontend, Design Systems & Performance)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `dashboard/src/main.tsx:35-52`, `platform-dashboard/src/main.tsx:34-49`, `dashboard/src/App.tsx:80-126`
- **Failure / Exploit Scenario**:
  Neither the client dashboard nor the platform super-admin dashboard implements React Error Boundaries.
  Under React 19 fail-stop behavior, any unhandled rendering error—such as circular JSON in tool call arguments (`Conversations.tsx:282`), undefined latency metrics (`Calls.tsx:225`), or hidden-tab Recharts SVG calculation bugs—causes React to unmount the entire DOM below `#root`. The receptionist is stranded with an empty white screen and no reload option.
- **Clinical / Business / Legal Impact**:
  Receptionists managing active patient calls experience sudden, unrecoverable dashboard crashes, paralyzing clinic desk operations.
- **Concrete Remediation Steps**:
  1. Implement a root `<ErrorBoundary>` with `<Sentry.ErrorBoundary fallback={<AppCrashFallback />}>` in both SPAs.
  2. Place sub-route error boundaries inside `AppLayout` around `<Outlet />` to isolate page crashes while keeping navigation functional.

---

#### Finding P0-18 (FRONT-02): Unrestricted Staff Access to Patient Audio Recordings Without Granular RBAC or Audit
- **Role**: Role 6 (Frontend, Design Systems & Performance)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `dashboard/src/App.tsx:98`, `dashboard/src/pages/Calls.tsx:240-251`, `AI-receptionist-1/routers/tenant.py:1001-1014`
- **Failure / Exploit Scenario**:
  In `dashboard/src/App.tsx:98`, the route `<Route path="calls" element={<Only><Calls /></Only>} />` specifies no role restriction, granting access to `role="staff"`.
  In `routers/tenant.py:1002`, `GET /calls/{call_id}/recording` authorizes `_staff`, generates a 10-minute presigned S3 audio URL, and logs **NO audit trail**. Temporary staff or compromised credentials can listen to and exfiltrate private patient phone calls without detection.
- **Clinical / Business / Legal Impact**:
  Breach of Australian Privacy Principle 11 and UK GDPR Article 9/32. Mandatory data breach notifications under Australian NDB and UK ICO regulations.
- **Concrete Remediation Steps**:
  1. Restrict `/calls/{call_id}/recording` to `Depends(_admin_or_owner)`.
  2. In `Calls.tsx`, gate `<RecordingPlayer>` behind `{can('admin') && ...}`.
  3. Emit an immutable audit log entry on every presigned recording URL request (`user_id`, `call_id`, `timestamp`, `ip`).

---

#### Finding P0-19 (QA-01): Fatal Mypy Typecheck Failures in `core/services/site_import.py` Blocking CI/CD Pipeline
- **Role**: Role 7 (QA, Chaos & Release)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/core/services/site_import.py:180, 441, 467, 497, 513, 518-520`, `.github/workflows/ci.yml:28-29`
- **Failure / Exploit Scenario**:
  Running `mypy` against the backend produces 10 fatal type errors in `site_import.py` (missing type annotations on `scored`/`fallback`, incompatible kwargs unpacking on `HoursEntry`, union-attr access on `None`).
  Because `.github/workflows/ci.yml:29` runs `uv run --frozen mypy`, the CI workflow fails immediately on any push or PR to `main`.
- **Clinical / Business / Legal Impact**:
  Engineers cannot deploy hotfixes or security patches through automated CI/CD; release automation is completely broken.
- **Concrete Remediation Steps**:
  1. Add explicit type annotations (`scored: list[tuple[int, dict[str, Any]]] = []`) and construct `HoursEntry` with typed arguments.
  2. Verify `uv run mypy` returns 0 errors before tagging releases.

---

#### Finding P0-20 (QA-02): Complete Absence of Automated Testing Across All Frontend Applications
- **Role**: Role 7 (QA, Chaos & Release)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `dashboard/package.json`, `platform-dashboard/package.json`, `landing-page/package.json`, `packages/shared/package.json`, `TESTING.md:117`
- **Failure / Exploit Scenario**:
  None of the four frontend applications contain test scripts or test dependencies (`TESTING.md` states: *"The dashboards have no unit tests yet"*). There is zero automated verification for appointment booking UI state, currency formatting, or clinic schedule validation.
- **Clinical / Business / Legal Impact**:
  Silent UI regressions reach production unnoticed, leading to double-booked chairs, misquoted prices, or broken Stripe checkout forms.
- **Concrete Remediation Steps**:
  1. Install `vitest` and `@testing-library/react` across `packages/shared` and `dashboard`.
  2. Add unit and component tests for appointment status transitions, weekly opening hours, and regional currency rendering.
  3. Add `npm test` to `.github/workflows/ci.yml`.

---

#### Finding P0-21 (QA-03): Irreversible Database Migrations and Incomplete Rollback Protocol
- **Role**: Role 7 (QA, Chaos & Release)
- **Severity**: **P0 (Launch Blocker)**
- **Exact Location**: `AI-receptionist-1/migrations/m0005_encrypt_clinical_text.py:53-57`, `deploy/deploy.sh:7-9, 87-92`
- **Failure / Exploit Scenario**:
  The deployment script advertises "rollback via git tag". However, migration `m0005` encrypts clinical text into `_enc` fields and permanently executes `$unset` on plaintext fields (`problem`, `notes`, `transcript`).
  If a deployment fails and the operator rolls back to a previous git tag, the older application code does not know how to read the encrypted fields, resulting in `None` values and crashing lead triage views.
- **Clinical / Business / Legal Impact**:
  Rolling back a failed release corrupts production data access and causes extended application downtime.
- **Concrete Remediation Steps**:
  1. Adopt a two-phase expansion-and-contraction migration strategy: retain plaintext fields until the new release is verified stable.
  2. Implement an automated migration tracking collection (`schema_migrations`).

---

### P1 — High Risks (All 30 Findings)

| ID | Role | Title | Exact Location | Failure / Risk Scenario | Remediation Summary |
|---|---|---|---|---|---|
| **SEC-01** | Role 1 | Client IP Spoofing via `X-Forwarded-For` Allows Rate Limit Bypass | `AI-receptionist-1/core/ratelimit.py:159-160` | Naive `fwd.split(",")[0]` allows spoofing leftmost IP, dodging login brute-force limits. | Parse `X-Forwarded-For` only from verified reverse proxy CIDR ranges. |
| **SEC-02** | Role 1 | TOTP MFA Code Replay Allowed | `AI-receptionist-1/core/auth/totp.py:37-43` | RFC 6238 violation; identical TOTP code can be replayed within 90s window. | Persist `last_used_counter` in Redis/DB; enforce strictly monotonic counters. |
| **SEC-05** | Role 1 | Legacy Tenant Mode Bypasses Multi-Tenant Scoping | `AI-receptionist-1/core/repositories/scoped.py:111-115` | `tenant.is_legacy` returns raw unscoped DB handle; unscoped queries leak cross-tenant data. | Hard-deprecate `is_legacy`; require non-nullable `tenant_id` on all queries. |
| **SEC-06** | Role 1 | Insecure Direct Object Reference (BOLA) via Caller ID Spoofing | `AI-receptionist-1/core/tools/scheduling.py:90-94` | Spoofed PSTN Caller ID authorizes viewing and cancelling patient appointments. | Require multi-factor verification (DOB, postal code, or SMS OTP link). |
| **SEC-12** | Role 1 | Lack of Port Restrictions in URL Scraper | `AI-receptionist-1/core/services/site_import.py:107-118` | Scraper allows arbitrary ports (`:27017`, `:6379`), enabling internal port scanning. | Restrict fetcher ports strictly to 80 and 443. |
| **SEC-13** | Role 1 | Indirect Prompt Injection via Scraped Clinic Websites | `AI-receptionist-1/core/services/site_import.py:349-373` | Up to 40k chars of external site text fed into LLM without delimiters, altering pricing. | Delimit external text in XML tags; mandate human approval in dashboard before publishing. |
| **SEC-14** | Role 1 | Untrusted Caller Data Injected into Dynamic System Prompt | `AI-receptionist-1/core/prompts/builder.py:251-299` | Caller names and previous summaries concatenated without delimiters, allowing jailbreak. | Wrap caller history in `<caller_history>` XML tags with system safety instructions. |
| **SEC-16** | Role 1 | Single Global Key for Multi-Tenant Data at Rest & Silent Fallback | `AI-receptionist-1/core/crypto.py:27-33` | Single Fernet key encrypts all clinic data; falls back to `SECRET_KEY`, risking corruption. | Mandate `TOKEN_ENCRYPTION_KEY`; implement per-tenant envelope encryption (AWS KMS). |
| **PRIV-06** | Role 2 | Incomplete GDPR Art 17 / APP 11.2 Erasure Engine | `AI-receptionist-1/core/compliance/subjects.py:49-63` | Subject erasure omits `unanswered`, `call_sessions`, `sales_leads`, and Twilio carrier logs. | Expand `SOURCES` and `DELETE_KINDS`; delete corresponding Twilio Call/Message SIDs. |
| **PRIV-07** | Role 2 | Indefinite Accumulation of PII and Clinical Metadata | `AI-receptionist-1/services/tenant_jobs.py:184-212` | `conversations`, `messages`, `unanswered` lack automated purging or MongoDB TTL indexes. | Add MongoDB TTL indexes in migrations; add scheduled purge routines. |
| **PRIV-08** | Role 2 | Unencrypted Clinical Health Summaries in DB, SMS & Webhooks | `AI-receptionist-1/agent/persistence.py:194-195` | `summary` is stored unencrypted in MongoDB; acute symptoms sent via unencrypted SMS. | Add `summary` to `SEALABLE` encryption; send de-identified notifications (*"Login to view"*). |
| **PRIV-09** | Role 2 | Non-Compliant SMS Sender ID & Inbound Opt-Out Handling | `AI-receptionist-1/core/integrations/sms.py:35` | Alphanumeric SMS sender IDs prevent STOP replies; inbound receptionist lacks opt-out tool. | Mandate two-way local virtual numbers; add `opt_out_patient` voice tool. |
| **PRIV-10** | Role 2 | Absence of Australian NDB & UK ICO 72-Hour Breach Capabilities | Global codebase (`core/compliance/`) | No immutable access audit logging; no automated incident classification runbooks. | Create immutable `audit_logs` collection; implement breach triage runbooks. |
| **SRE-03** | Role 3 | Black-Hole Zombie Voice Worker Containers | `AI-receptionist-1/Dockerfile:59-60` | Healthcheck only checks local port 8081, not active LiveKit Cloud WebSocket connection. | Probe active LiveKit WebSocket connection state in health endpoint. |
| **SRE-04** | Role 3 | Unbounded Redis Memory with `noeviction` Policy | `deploy/docker-compose.prod.yml:126` | 256MB cap with `noeviction` rejects writes when full, freezing rate limits and job queues. | Increase Redis memory (1GB+); set `volatile-lru`; add Prometheus memory alerts. |
| **SRE-05** | Role 3 | Silent Permanent Loss of Inbound WhatsApp Messages | `AI-receptionist-1/whatsapp/meta_handler.py:481-486` | Message marked seen before processing; transient failures drop message on Meta retries. | Use three-state lease (`processing` -> `done`); allow retries on transient errors. |
| **SRE-06** | Role 3 | Permanent Deadlock of Stripe Webhook Claims | `AI-receptionist-1/core/billing/lifecycle.py:338-344` | Mid-execution worker crash leaves event in "processing" indefinitely; retries fail with 409. | Add lease timestamp (`lease_expires_at: now + 5m`); re-claim expired events. |
| **SRE-07** | Role 3 | Non-Atomic Patient Recall Claiming Causing Duplicate SMS | `AI-receptionist-1/core/services/reactivation.py:214-255` | Overlapping worker runs select same rows via `.find()`, dispatching duplicate marketing SMS. | Claim rows atomically using `find_one_and_update` with status `sending`. |
| **SRE-08** | Role 3 | Total Absence of Circuit Breakers Across Upstream APIs | `AI-receptionist-1/core/services/scheduling_provider.py:225-265` | Upstream PMS/Meta outages freeze async coroutines for 8s, starving event loop. | Implement circuit breakers (5 fails -> open 60s); fast-fail within 5ms. |
| **SRE-09** | Role 3 | Background Tasks Dropped During Container Shutdown | `AI-receptionist-1/main.py:116-124` | `notifications.drain()` never called in lifespan shutdown, severing in-flight emails/webhooks. | Add `await notifications.drain()` before closing DB/Redis connection pools. |
| **SRE-10** | Role 3 | MongoDB Client Missing Majority Write Concern | `AI-receptionist-1/database/mongodb.py:35-40` | Client defaults to `w=1`; primary failover rolls back un-replicated appointments. | Configure `AsyncIOMotorClient` with `w="majority"` and `maxPoolSize=150`. |
| **SRE-11** | Role 3 | Lack of Pre-Stop Drain Hook & Caddy Routing Race | `deploy/Caddyfile:7-15`, `deploy/deploy.sh:97-99` | Caddy polls `/readyz` every 5s; continues routing traffic to terminating containers. | Implement `/drain` signal returning 503 before uvicorn shutdown; passive Caddy checks. |
| **VOICE-04** | Role 4 | Conversational Latency Budget Blowout (1300–2600ms) | `AI-receptionist-1/scripts/latency_report.py:19-24` | Sequential turns, DB writes, and slow TTS blow latency past <800ms target. | Switch TTS to Cartesia Sonic-3 (90ms TTFB); offload DB writes to background tasks. |
| **VOICE-05** | Role 4 | Absence of Programmatic Clinical Output Filtering | `AI-receptionist-1/agent/speech.py:20-36` | Clinical safety enforced purely via prompt; LLM can hallucinate medication dosages. | Implement regex/classifier interceptor in `speech.py` blocking pharmaceuticals/dosages. |
| **VOICE-06** | Role 4 | Price Commitment Vulnerability on Dental Procedures | `AI-receptionist-1/core/prompts/builder.py:132-145` | Unfenced implant prices ($25,000) quoted without mandatory clinical exam disclaimers. | Prepend `"from "`; enforce automatic disclaimer appending (*"subject to clinical exam"*). |
| **BACK-04** | Role 5 | Out-of-Order Stripe Webhook Delivery State Corruption | `AI-receptionist-1/core/billing/lifecycle.py:138-176` | Out-of-order events revert delinquent tenants to active, enabling unpaid usage. | Verify `event["created"] > tenant.billing.last_event_created_at` before updating. |
| **BACK-06** | Role 5 | Voice Usage Metering Vulnerability on Worker Crash | `AI-receptionist-1/agent/persistence.py:230-262` | Crashed worker drops `_record_usage`; no reconciliation against Twilio/LiveKit CDRs. | Implement nightly Twilio CDR reconciliation job to backfill missing `usage_events`. |
| **BACK-07** | Role 5 | Multi-Currency Tax Registration (GST/VAT) Gaps | `AI-receptionist-1/core/billing/plans.py:36-54` | Floating-point revenue math; unverified AU ABN / UK VAT; ACL single-pricing risks. | Use `Decimal`; collect/verify ABN/VAT via Stripe Tax; disclose GST/VAT on landing page. |
| **FRONT-03** | Role 6 | Preloader Hides Hero Headline (H1) for 1.5–3.5s | `landing-page/src/components/Hero.astro:27-59` | `visibility: hidden` on H1 blocks Largest Contentful Paint (LCP) until preloader finishes. | Remove `data-hide` from H1; let semantic headline paint immediately. |
| **FRONT-04** | Role 6 | Client Timezone Drift in Appointment Calendar | `packages/shared/src/lib/format.ts:86-90` | `date-fns parseISO` uses browser timezone, shifting appointments across days. | Use calendar integer arithmetic (`addDaysCalendar`) independent of browser timezone. |
| **FRONT-05** | Role 6 | Regional Toggle Mutates URL Leaving Schema Mismatched | `landing-page/src/scripts/region.ts:40-42` | `history.replaceState` changes URL without updating canonical tags or JSON-LD schema. | Execute hard navigation `window.location.replace('/uk/')` when regional preference differs. |
| **FRONT-06** | Role 6 | Missing Security Headers in Caddy & Restrictive Landing CSP | `deploy/Caddyfile:18-42`, `landing-page/nginx.conf.template:14` | Caddy lacks HSTS; landing CSP `connect-src 'self'` blocks Sentry telemetry. | Add HSTS/X-Frame-Options in Caddy; parameterize landing CSP connect-src. |
| **QA-04** | Role 7 | Production Redis Deployed Without Password Authentication | `deploy/docker-compose.prod.yml:124-135` | Redis lacks `requirepass`, exposing rate limits, tokens, and job payloads to local network. | Enforce `--requirepass ${REDIS_PASSWORD}` in compose and environment configs. |
| **QA-05** | Role 7 | Conversational Voice Evals Silently Skipped in CI | `.github/workflows/ci.yml:50-76` | CI evals job exits 0 when `EVAL_OPENAI_API_KEY` is missing, hiding prompt regressions. | Make eval secret mandatory in release workflow; implement mock evaluation harness. |
| **QA-06** | Role 7 | Mutable Base Image Tags & Pinned Action Violations | `Dockerfile`, `.github/workflows/` | Floating image tags (`python:3.12-slim`, `node:22-alpine`) and unpinned GitHub Actions. | Pin base images to SHA256 digests; pin GitHub Actions to full 40-char commit SHAs. |

---

### P2 — Enterprise Hardening (Multi-Site Dental Chain Tier)

| ID | Role | Title & Exact Location | Risk & Operational Impact | Remediation Steps |
|---|---|---|---|---|
| **SEC-03** | Role 1 | Stateless JWT Access Token Lacks Immediate Revocation (`core/auth/deps.py:117-124`) | Suspended staff retain a 15-minute access window to exfiltrate patient records. | Implement Redis-backed token revocation check or check `token_version` on user. |
| **SEC-04** | Role 1 | CSRF Exposure on Session Refresh Endpoint (`routers/auth.py:130-140`) | When `SameSite=None`, cross-site requests consume refresh cookies without anti-CSRF token. | Enforce `SameSite=Lax` or require custom anti-CSRF header (`X-Requested-With`). |
| **SEC-07** | Role 1 | Flat S3 Storage Key Namespace for Recordings (`agent/recording.py:28-29`) | All clinic recordings pooled in `recordings/YYYY/MM/DD/`, lacking IAM tenant boundaries. | Partition S3 paths by tenant: `recordings/{tenant_id}/{YYYY}/{MM}/{DD}/{call_id}.ogg`. |
| **SEC-09** | Role 1 | Non-Constant-Time Meta WhatsApp Token Check (`whatsapp/meta_handler.py:426`) | String equality operator `==` susceptible to timing side-channel attacks. | Use `hmac.compare_digest(token.encode(), settings.META_WA_VERIFY_TOKEN.encode())`. |
| **SEC-10** | Role 1 | Missing Webhook Signature for LiveKit Egress (`agent/recording.py:52-57`) | Asynchronous recording failures or status updates cannot be verified securely. | Implement signature-verified LiveKit webhook receiver validating `Authorization`. |
| **PRIV-11** | Role 2 | Cross-Tenant Transcript Inspection & Eval Export (`core/services/reviews.py:68-80`) | Real patient clinical conversations exported to Git test fixtures (`tests/evals/cases/`). | Prohibit cross-tenant extraction; generate evaluation datasets synthetically via LLM. |
| **PRIV-12** | Role 2 | Hardcoded WhatsApp Hotel Logic in Dental Flows (`whatsapp/meta_handler.py:141-193`) | Dental patients receive hotel canned replies; violates Meta health policy. | Remove hotel keyword filtering; delegate to `build_chat_prompt` with dental templates. |
| **SRE-12** | Role 3 | Heavy MongoDB Aggregations on Prometheus Scrapes (`core/metrics.py:181-191`) | Every scrape reads 5000 call docs into memory, locking asyncio loop. | Pre-aggregate call metrics in a 60s background cron job; make `/metrics` an O(1) read. |
| **SRE-13** | Role 3 | Thundering Herd Vulnerability in Arq Job Backoff (`jobs/worker.py:68-71`) | Deterministic exponential backoff causes retrying jobs to hammer third parties at once. | Implement Full Jitter (`random.uniform(base * 0.5, base * 1.5)`). |
| **SRE-14** | Role 3 | Lack of OpenTelemetry Distributed Tracing (`core/log.py:21-25`) | No W3C `traceparent` propagation across SIP, LiveKit, LLM, and PMS boundaries. | Integrate OpenTelemetry SDK; bind trace IDs to LiveKit room names. |
| **SRE-15** | Role 3 | Single-Node Redis SPOF Without Failover (`deploy/docker-compose.prod.yml:124-135`) | Single Redis container crash takes down rate limiting and background queues. | Mandate Multi-AZ managed Redis (AWS ElastiCache or Redis Cloud) for production. |
| **VOICE-07** | Role 4 | Default `en-US` STT/TTS on AU/UK Tenancies (`core/tenancy/models.py:108-109`) | American phonetic dictionary increases Word Error Rate on Australian/British dental terms. | Dynamically initialize `agent.language` to `en-AU` or `en-GB` with localized dental terms. |
| **VOICE-08** | Role 4 | G.711 μ-law Hardcoding & Inbound AMD Blindness (`voice/twilio_handler.py:160`, `agent/receptionist.py`) | Distortion if carrier sends A-law; receptionist converses with clinic's own IVR/voicemail. | Support automatic codec negotiation (A-law/μ-law/Opus); add PBX loop detection. |
| **BACK-08** | Role 5 | Monolithic Router Anti-Pattern in `routers/tenant.py` (`routers/tenant.py:1-1483`) | 1,482 LOC file mixes 35 endpoints, raw untyped DB queries, and scheduling logic. | Deconstruct into subrouters under `routers/tenant/`; enforce Pydantic response models. |
| **BACK-09** | Role 5 | Absence of Standardized RFC 7807 Error Responses (`main.py:191-204`) | Inconsistent error schemas (`detail` vs `error` vs `message`) break client integrations. | Implement unified RFC 7807 `application/problem+json` exception handler. |
| **FRONT-07** | Role 6 | Clinic Staff Exposure to Team Roster & Invites (`dashboard/src/App.tsx:115`) | Receptionists have visibility into owner emails and pending invitation links. | Restrict `/team` route guard in `App.tsx` to `role="admin"`. |
| **FRONT-08** | Role 6 | Timestamps Formatted in Browser Local Time (`dashboard/src/pages/Calls.tsx:127`) | Remote managers see call records in their local time rather than clinic timezone. | Replace `dateTime()` with `zoneDateTime(iso, tz)` using clinic's configured timezone. |
| **FRONT-09** | Role 6 | Default BDT Fallback Currency & Narrow Symbols (`packages/shared/src/lib/format.ts:3`) | Unspecified currency defaults to Bangladeshi Taka; narrow symbols (`$100`) ambiguous. | Change default to `AUD`; use explicit currency prefixes (`A$` vs `£`). |
| **FRONT-10** | Role 6 | Unthrottled WebGL Fragment Shader Running in RAF (`landing-page/src/scripts/motion/orb.ts:145`) | Full-screen shader continues running RAF loop in light bands, draining laptop batteries. | Cancel `requestAnimationFrame` loop when `s.alpha === 0` in light sections. |
| **FRONT-11** | Role 6 | Missing Webfont Preload Directives (`landing-page/src/layouts/Base.astro:58-90`) | Fonts load sequentially after CSS, causing layout shifts (CLS) on initial load. | Add `<link rel="preload" as="font" type="font/woff2">` for Geist variable font. |
| **QA-07** | Role 7 | 180s Timeout on Native Library Probe Freezes Windows (`tests/conftest.py:84-95`) | Pytest test collection hangs for 3 minutes on Windows workstations importing LiveKit. | Reduce probe timeout from 180s to 5s; add fast-fail check for Windows. |
| **QA-08** | Role 7 | Windows Console Unicode Crash in `check_env.py` (`scripts/check_env.py:349`) | Emoji `\u2705` crashes on default `cp1252` console encoding, aborting preflight. | Add `sys.stdout.reconfigure(encoding="utf-8")` or fallback to ASCII `[OK]`. |
| **QA-09** | Role 7 | Permissive Linter & Typechecker Suppressions (`pyproject.toml:90, 105-107`) | Mypy disabled on 60% of codebase (`database`, `services`, `voice`, `routers`). | Incrementally enable typechecking on `database` and `services`; add bugbear rules. |
| **QA-10** | Role 7 | Dependabot Omits Production Infrastructure (`.github/dependabot.yml:30-33`) | `/deploy` and `/landing-page` containers omitted from automated dependency updates. | Expand Dependabot directory scope to include `/deploy` and `/landing-page`. |

---

### P3 — Nice-to-Have / Repository Hygiene (5 Findings)

| ID | Role | Title & Exact Location | Operational Impact | Recommended Action |
|---|---|---|---|---|
| **SEC-17** | Role 1 | Insecure 16-Char HMAC Truncation (`core/signed_links.py:71`) | 64-bit entropy tag theoretically vulnerable to birthday collision attacks. | Increase truncated HMAC signature length to 32 hex characters (`[:32]`). |
| **SRE-16** | Role 3 | Stop Grace Period Drift Vulnerability (`docker-compose.prod.yml:50`) | If `LIVEKIT_DRAIN_TIMEOUT` exceeds compose grace period (16m), calls SIGKILLED. | Add startup assertion ensuring compose grace period exceeds drain timeout + 60s. |
| **BACK-10** | Role 5 | Test Suite Twilio SMS Environment Dependency (`tests/test_phase5_billing.py:394`) | Local `.env` Twilio credentials cause `KeyError: 'dev_code'` in billing unit tests. | Mock `send_sms` in `conftest.py` so unit tests execute hermetically offline. |
| **FRONT-12** | Role 6 | Real Dental Audio Missing from Landing Page (`landing-page/src/data/scenarios.ts`) | Audio player shows "Recorded audio coming soon", reducing clinical sales conversion. | Record and upload high-fidelity synthetic Australian/British dental audio samples. |
| **QA-11** | Role 7 | Untracked Sensitive Backup Files on Disk (`AI-receptionist-1/.env.bak-*`) | Backup env files remain on developer disks with potential legacy credentials. | Delete all local `.env.bak*` files and add git pre-commit hook blocking backup files. |

---

## 3. Missing Enterprise Pillars Gap Analysis (Multi-Site Dental Groups: 5–50 Clinics)

Enterprise dental groups (such as Bupa Dental Care, Pacific Smiles Group, Dental Partners, or Portman Dental Care) operate multi-chair facilities with strict clinical governance, compliance audits, and multi-tier operational hierarchies. The audit revealed five missing enterprise architectural pillars that prevent onboarding multi-site dental chains:

```
+---------------------------------------------------------------------------------------------------+
|                        ENTERPRISE MULTI-SITE ARCHITECTURAL DEFICIT                                |
+---------------------------------------------------------------------------------------------------+
|  1. Granular Healthcare RBAC        | ❌ Only flat "owner", "admin", "staff"; no clinician/nurse. |
|  2. Tamper-Evident Audit Logging    | ❌ No WORM storage; recording playback & DSR un-audited.    |
|  3. Healthcare Certifications       | ❌ Missing SOC 2 Type II, ISO 27001, Cyber Essentials, DSPT.|
|  4. Multi-Region DR & Failover      | ❌ Single-node Redis; no automated cross-region database.   |
|  5. Spend Circuit Breakers & Caps   | ❌ No financial caps on OpenAI, Twilio, LiveKit toll spend. |
+---------------------------------------------------------------------------------------------------+
```

### Pillar 1: Granular Role-Based Access Control (RBAC)
- **Current State**: The platform provides only three coarse roles: `owner`, `admin`, and `staff` (`AI-receptionist-1/core/auth/models.py`). A receptionist with `role="staff"` has unrestricted access to full clinical transcripts and call audio recordings (`FRONT-02`), but cannot manage opening hours.
- **Enterprise Requirement**: Multi-site dental chains require a 4-tier clinical hierarchy with cross-practice group scoping:
  1. **Practice Principal / Clinical Director**: Full administrative, clinical, and financial governance across all clinics in the group; access to clinical audit logs and recording archives; ability to approve knowledge base changes.
  2. **Practice Manager**: Operational authority over assigned clinic site(s); manage opening hours, staff rosters, PMS sync mappings, and patient recall campaigns; view aggregate triage metrics.
  3. **Clinician (Dentist / Specialist)**: Access to assigned patient appointment diaries, clinical triage notes, and medical histories; strictly barred from billing, financial bank settings, or team management.
  4. **Dental Nurse / Receptionist**: Front-desk scheduling and patient check-in; access to basic appointment booking; strictly barred from listening to historical call audio recordings without supervisory authorization.
  5. **Group Compliance Auditor (Read-Only)**: Cross-site read-only access to immutable audit logs, DSR erasure proofs, and consent registries without operational permissions.

### Pillar 2: Immutable, Tamper-Evident Audit Logging
- **Current State**: Telemetry relies on standard application logging (`core/log.py`) and Sentry error captures. S3 recording downloads (`routers/tenant.py:1002`), patient data views, and staff deletions have zero dedicated audit records.
- **Enterprise Requirement**:
  1. An immutable, append-only audit trail conforming to NHS Information Governance and Australian Privacy Principle 11 standards.
  2. Every read, export, modification, or deletion of Protected Health Information (PHI) or call audio must be committed to a dedicated `audit_events` collection with cryptographic hash chaining (SHA-256 linking to previous block) and replicated to Write-Once-Read-Many (WORM) storage (e.g. AWS S3 Object Lock in Compliance Mode).
  3. Audit records must capture: `timestamp_utc`, `actor_id`, `actor_ip`, `actor_role`, `target_patient_id`, `action_type` (`VIEW_TRANSCRIPT`, `PLAY_AUDIO`, `EXPORT_DATA`, `REVOKE_CONSENT`), and `justification_code`.

### Pillar 3: Healthcare Regulatory Certifications & Compliance Frameworks
- **Current State**: The repository provides self-declared Data Processing Agreements in markdown (`core/compliance/legal.py`), but lacks formal third-party compliance controls.
- **Enterprise Requirement**: Enterprise procurement for dental networks requires certified adherence to:
  - **UK NHS Data Security and Protection Toolkit (DSPT)**: Mandatory annual self-assessment for any clinic delivering NHS dental contracts or integrating with NHS systems.
  - **UK Cyber Essentials Plus**: Mandatory technical verification including authenticated vulnerability scans and edge firewall certifications.
  - **SOC 2 Type II**: Independent auditor verification of Trust Services Criteria across Security, Availability, Processing Integrity, Confidentiality, and Privacy over a 6-month observation window.
  - **ISO 27001 / ISO 27701**: Formal Information Security and Privacy Management System certification.
  - **Australian Privacy Act 1988 & Notifiable Data Breaches (NDB) Scheme**: Documented, automated 72-hour incident response workflow with OAIC reporting templates.

### Pillar 4: Automated Disaster Recovery (DR) & Multi-Region High Availability
- **Current State**: Deployment automation (`deploy/deploy.sh`) targets a single virtual machine host per region. Redis runs as a standalone single container without failover (`SRE-15`).
- **Enterprise Requirement**:
  1. Multi-AZ, active-active or hot-standby deployment across regional zones: `ap-southeast-2` (Sydney / Melbourne) for Australia and `eu-west-2` (London) for the United Kingdom.
  2. Automated database failover: MongoDB Atlas multi-region replica sets with verified Recovery Time Objective (RTO) < 15 minutes and Recovery Point Objective (RPO) < 1 minute.
  3. Automated telephony trunk failover: LiveKit SIP trunks provisioned with dual-homed DNS SRV records routing to redundant worker clusters; automatic carrier failover in Twilio to secondary PSTN numbers upon webhook failure.
  4. Chaos Engineering Drills: Automated quarterly chaos injection (terminating Redis, simulating PMS 429 cascades, killing worker pods) to verify zero dropped patient calls.

### Pillar 5: Spend Circuit Breakers & Financial Safety Caps
- **Current State**: The system relies on monthly usage aggregation (`core/billing/metering.py`), but lacks real-time runtime throttles.
- **Enterprise Requirement**: Real-time voice inference combines multiple billable third-party streams (Twilio SIP trunking + Deepgram streaming STT + OpenAI LLM + ElevenLabs TTS + LiveKit Cloud egress). A single runaway call loop (e.g. PBX IVR loop, toll fraud dialer, or prompt injection loop) can generate $100+ USD per hour in inference charges.
  - Implement real-time spend circuit breakers:
    1. Maximum call duration hard ceiling enforced at 15 minutes (`MAX_CALL_DURATION_S = 900`).
    2. Tenant-level hourly and daily spend caps (e.g., maximum $50/day in AI usage on standard dental tiers); calls automatically route to standard clinic voicemail when caps are reached.
    3. Anomaly detection: automatically terminate and flag calls exceeding 30 turns or repeating identical conversational loops.

---

## 4. Phased Remediation Roadmap

```
+---------------------------------------------------------------------------------------------------+
| SPRINT 0: CRITICAL LAUNCH BLOCKERS (P0) — MANDATORY BEFORE ANY COMMERCIAL PATIENT CALLS            |
| Target: 100% Remediation of all 21 P0 Blockers | Duration: 2 Weeks                                |
+---------------------------------------------------------------------------------------------------+
  ├── [P0-01] Revoke and rotate all committed production cloud secrets; purge .env and backup files.
  ├── [P0-02] Fix TOCTOU SSRF in site_import.py by pinning resolved IP addresses; enforce IMDSv2.
  ├── [P0-03] Remove VERIFY_TWILIO_SIGNATURE=false; enforce mandatory webhook HMAC signatures.
  ├── [P0-04] Gate LiveKit recording egress until greeting finishes; remove rec.announce=False.
  ├── [P0-05] Implement stop_recording tool in ReceptionistAgent; enforce prompt objection rule.
  ├── [P0-06] Pass store=False in OpenAI summary/realtime calls; block boot on OPENAI_ZDR_ENABLED.
  ├── [P0-07] Implement explicit verbal consent gate for special category health data collection.
  ├── [P0-08] Completely disable unconsented outbound missed-call recovery marketing SMS.
  ├── [P0-09] Implement two-phase compensating rollback for Cliniko/Dentally PMS bookings.
  ├── [P0-10] Decommission LEGACY_TWILIO_VOICE; enforce 100% LiveKit SIP Trunking with graceful drain.
  ├── [P0-11] Disable legacy twilio_handler router in main.py to prevent hotel voice split-brain.
  ├── [P0-12] Inject AU 000/Healthdirect and UK 999/111 into dental facts; remove country="US" default.
  ├── [P0-13] Tune LiveKit interruption min_duration to 0.25s; remove audio packet drops in voice.
  ├── [P0-14] Add DOB to Patient model; update Cliniko/Dentally matching; fix 9-digit phone comparison.
  ├── [P0-15] Implement Redis token-bucket rate limiter for PMS queries; cache availability slots.
  ├── [P0-16] Resolve resource_id="any" to concrete practitioner; add TTL index to slot_locks.
  ├── [P0-17] Wrap dashboard & platform-dashboard in React Error Boundaries with recovery actions.
  ├── [P0-18] Restrict call recording playback to admin/owner roles; emit immutable audit log.
  ├── [P0-19] Fix 10 fatal mypy errors in site_import.py; restore green CI status.
  ├── [P0-20] Initialize Vitest and @testing-library/react in frontend packages; add smoke tests.
  └── [P0-21] Implement two-phase expansion-contraction database migrations; track schema versions.
                                                  │
                                                  ▼
+---------------------------------------------------------------------------------------------------+
| SPRINT 1: HIGH RISKS & RELIABILITY HARDENING (P1) — REQUIRED FOR COMMERCIAL PILOT EXPANSION       |
| Target: 30 P1 Findings | Duration: 3 Weeks                                                        |
+---------------------------------------------------------------------------------------------------+
  ├── [SEC] Validate reverse proxy CIDRs for X-Forwarded-For; enforce single-use TOTP; remove legacy DB mode.
  ├── [SEC] Restrict scraper ports to 80/443; fence caller history in XML prompts; enforce TOKEN_ENCRYPTION_KEY.
  ├── [PRIV] Expand DSR erasure to unanswered/sessions/Twilio; add MongoDB TTL indexes to high-velocity collections.
  ├── [PRIV] Add summary to SEALABLE encryption; sanitize outbound SMS alerts; mandate local virtual numbers.
  ├── [SRE] Update LiveKit worker Docker healthcheck to probe cloud WS connection; increase Redis memory to 1GB+.
  ├── [SRE] Redesign WhatsApp dedupe to atomic lease; add lease expiration to Stripe webhook claim logic.
  ├── [SRE] Implement find_one_and_update for recall SMS batches; introduce circuit breakers for PMS/Meta.
  ├── [SRE] Add notifications.drain() to lifespan shutdown; configure MongoDB w="majority" and pool 150.
  ├── [SRE] Implement pre-stop /drain hook in API; configure Caddy passive health checks.
  ├── [VOICE] Switch default TTS to Cartesia Sonic-3 (90ms TTFB); offload DB writes in dental tools to background.
  ├── [VOICE] Implement deterministic clinical safety regex interceptors; enforce mandatory price exam disclaimers.
  ├── [BILLING] Verify event timestamp ordering on Stripe webhooks; implement nightly Twilio CDR reconciliation.
  ├── [BILLING] Refactor billing math to Decimal; verify AU ABN and UK VAT in Stripe Tax; add landing tax caveats.
  ├── [FRONT] Remove data-hide from Hero H1 to fix LCP; replace addDaysISO with timezone-agnostic calendar math.
  ├── [FRONT] Replace history.replaceState with window.location.replace on landing page; add Caddy HSTS headers.
  └── [QA] Add --requirepass to production Redis; make CI evals mandatory; pin Docker base images to SHA256 digests.
                                                  │
                                                  ▼
+---------------------------------------------------------------------------------------------------+
| SPRINT 2: ENTERPRISE TIER & SCALE (P2 / P3) — REQUIRED FOR MULTI-SITE DENTAL CHAINS               |
| Target: 17 P2 & 5 P3 Findings | Duration: 4 Weeks                                                 |
+---------------------------------------------------------------------------------------------------+
  ├── [ENTERPRISE] Implement 4-tier granular healthcare RBAC (Principal, Manager, Clinician, Nurse).
  ├── [ENTERPRISE] Build tamper-evident audit logging with WORM storage (S3 Object Lock).
  ├── [ENTERPRISE] Implement real-time spend circuit breakers and financial safety caps ($50/day).
  ├── [PERF] Decouple /metrics from live MongoDB scans; pre-aggregate in background cron jobs.
  ├── [PERF] Pause WebGL canvas animation loop in light bands; add font preload tags for Geist/IBM Plex.
  ├── [LOCALE] Initialize agent language to en-AU and en-GB with regional dental phonetic dictionaries.
  ├── [LOCALE] Display dashboard timestamps in clinic timezone; format multi-currency economics in native symbols.
  ├── [CODE] Deconstruct monolithic routers/tenant.py into domain subrouters; implement RFC 7807 problem details.
  ├── [QA] Lower Windows LiveKit test probe timeout to 5s; reconfigure check_env.py for UTF-8 on Windows.
  └── [MEDIA] Record and upload genuine Australian and British dental voice demo recordings to landing page.
```

---

## 5. Live Verification Output Log (Unabridged Command Evidence)

In compliance with Requirement **R2**, live offline verification checks were executed across the monorepo to capture exact pass/fail counts, compiler warnings, and diagnostic errors.

### 5.1 Backend Verification (`AI-receptionist-1/`)

#### Backend Pytest Execution
- **Command**: `cd AI-receptionist-1; .\.venv\Scripts\pytest -v`
- **Exit Code**: `0` (Success)
- **Duration**: 171.83s (02:51)
- **Summary**: **631 passed, 17 skipped, 2 warnings** (644 collected, 4 skipped initially)
- **Warnings Captured**:
  1. `StarletteDeprecationWarning`: `Using httpx with starlette.testclient is deprecated; install httpx2 instead.` (`fastapi/testclient.py:1`)
  2. `UserWarning`: `Duplicate Operation ID fallback_telephony_fallback_get for function fallback at routers/telephony.py` (`fastapi/openapi/utils.py:295`)
- **Skipped Test Breakdown**:
  - `tests/evals/test_case_evals.py`: 8 evaluations skipped (require live external LLM API keys).
  - `tests/test_dental_wiring_audit.py`: 2 tests skipped.
  - `tests/test_phase6_compliance.py`: 1 test skipped.
  - `tests/test_phase7_launch.py`: 1 test skipped.
  - `tests/test_webhook_signatures.py`: 1 test skipped.
  - 4 configuration collection skips.

#### Environment Preflight Check (`scripts/check_env.py`)
- **Command**: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m scripts.check_env`
- **Exit Code**: `1` (Completed with 5 offline connection failures)
- **Diagnostic Output**:
  ```text
  livekit API request to https://ai-hxxt85x9.livekit.cloud failed (Cannot connect to host ai-hxxt85x9.livekit.cloud:443 ssl:default [getaddrinfo failed]), retrying with fallback url https://ai-hxxt85x9.livekit.cloud
  APP_ENV=development  telephony=twilio

  ✅ SECRET_KEY  set
  ❌ MongoDB     ConfigurationError: check the password, and Atlas → Network Access must allow your IP
  ❌ OpenAI      ConnectError while checking
  ❌ Deepgram    ConnectError while checking
  ⚠️  ElevenLabs  empty: the agent falls back to Cartesia or OpenAI voices if configured
  ⚠️  Cartesia    empty: set it to use the free Cartesia voice (see docs/TELEPHONY_TESTING.md, 0.5)
  ⚠️  Kokoro      off (KOKORO_BASE_URL empty). Optional local fallback voice, see docs/TELEPHONY_TESTING.md 0.5
  ✅ Voice       order: deepgram → openai
  ❌ LiveKit     ClientConnectorDNSError: URL, key and secret must come from the same LiveKit project
  ❌ Twilio      ConnectError while checking

  5 problem(s).
  ```
- **Console Encoding Bug Note**: When executed without `PYTHONIOENCODING="utf-8"`, `check_env.py` crashed immediately with:
  `UnicodeEncodeError: 'charmap' codec can't encode character '\u2705' in position 0: character maps to <undefined>`. Windows default code page (`cp1252`) cannot encode emoji output (`QA-08`).

#### Static Linting (`ruff`)
- **Command**: `cd AI-receptionist-1; .\.venv\Scripts\ruff.exe check .`
- **Exit Code**: `0`
- **Output**: `All checks passed!`

#### Static Type Checking (`mypy`)
- **Command 1**: `cd AI-receptionist-1; .\.venv\Scripts\mypy.exe .`
  - **Exit Code**: `1` (Blocking Failure)
  - **Output**: `services\calendar.py: error: Source file found twice under different module names: "calendar" and "services.calendar"`
- **Command 2**: `cd AI-receptionist-1; .\.venv\Scripts\mypy.exe --explicit-package-bases .`
  - **Exit Code**: `1` (Fatal Failure)
  - **Diagnostic Output**: **Found 438 errors in 39 files (checked 232 source files)**
  - Prominent categories:
    - 10 fatal type errors in `core/services/site_import.py` (`QA-01`).
    - Pydantic BaseModel unpacking errors (`TenantConfig`, `HoursEntry`).
    - Optional union unwrapping failures (`Item "None" of "dict[str, Any] | None" is not indexable`).
    - Incompatible arq job worker coroutine signatures (`jobs/worker.py`).

---

### 5.2 Landing Page Guards (`landing-page/scripts/`)

#### Guard 1: Price Book Consistency Guard
- **Command**: `bun landing-page/scripts/check-prices.mjs`
- **Exit Code**: `0` (Success)
- **Output**:
  ```text
  ✓ Landing-page prices match plans.py (2 dental plans, AUD and GBP, trial 14 days).
  ```
- **Validation Detail**: Verifies that pricing figures rendered on the public Astro landing page strictly match backend definitions in `AI-receptionist-1/core/billing/plans.py` across AUD and GBP plans with 14-day trials.

#### Guard 2: WCAG AA Accessibility Contrast Guard
- **Command**: `bun landing-page/scripts/check-contrast.mjs`
- **Exit Code**: `0` (Success)
- **Output**:
  ```text
  ✓ 16.64:1  ink on bone  (headings and body on bone bands, needs 4.5:1)
  ✓ 17.65:1  ink on paper  (headings and body on paper bands, needs 4.5:1)
  ✓ 18.70:1  ink on surface  (text on light cards, needs 4.5:1)
  ✓ 15.77:1  ink on teal-wash  (text on teal-wash chips, needs 4.5:1)
  ✓  5.64:1  slate on bone  (secondary text on bone, needs 4.5:1)
  ✓  5.98:1  slate on paper  (secondary text on paper, needs 4.5:1)
  ✓  6.34:1  slate on surface  (secondary text on cards, needs 4.5:1)
  ✓  5.35:1  slate on teal-wash  (secondary text on teal wash, needs 4.5:1)
  ✓  4.91:1  teal on bone  (accent headline clause on bone, needs 4.5:1)
  ✓  5.21:1  teal on paper  (accent headline clause on paper, needs 4.5:1)
  ✓  5.52:1  teal on surface  (accent text on cards, needs 4.5:1)
  ✓  4.65:1  teal on teal-wash  (chip text on teal wash, needs 4.5:1)
  ✓  5.90:1  white on teal  (booked diary slot, needs 4.5:1)
  ✓ 19.98:1  white on ink  (ink button, pressed toggle, needs 4.5:1)
  ✓  5.92:1  champagne-ink on surface  (estimated value on cards, needs 4.5:1)
  ✓  5.27:1  champagne-ink on bone  (lunch-blocked label, needs 4.5:1)
  ✓  6.15:1  alert on surface  (voicemail chip, needs 4.5:1)
  ✓ 16.64:1  mist on night  (headings and body on dark bands, needs 4.5:1)
  ✓ 15.74:1  mist on night-raised  (text on raised dark panels, needs 4.5:1)
  ✓ 14.45:1  mist on night-card  (text on dark cards, needs 4.5:1)
  ✓ 10.81:1  mist-muted on night  (secondary text on dark bands, needs 4.5:1)
  ✓  9.39:1  mist-muted on night-card  (secondary text on dark cards, needs 4.5:1)
  ✓  5.87:1  mist-faint on night  (timestamps on dark bands, needs 4.5:1)
  ✓  5.10:1  mist-faint on night-card  (timestamps on dark cards, needs 4.5:1)
  ✓ 12.60:1  aqua on night  (accent headline clause on dark, needs 4.5:1)
  ✓ 10.94:1  aqua on night-card  (speaker labels, trace status, needs 4.5:1)
  ✓ 12.60:1  night on aqua  (primary button, needs 4.5:1)
  ✓ 13.95:1  night on aqua-deep  (primary button, hover, needs 4.5:1)
  ✓ 15.79:1  aqua-light on night  (accent headline clause, step values on dark, needs 4.5:1)
  ✓ 13.71:1  aqua-light on night-card  (mint labels on dark cards, needs 4.5:1)
  ✓  5.55:1  mist-faint on night-raised  (mono labels on raised dark panels, needs 4.5:1)
  ✓ 13.25:1  mist on graphite  (ink button hover text, needs 4.5:1)
  ✓ 10.31:1  champagne on night  (plan bar, ROI anchor, needs 4.5:1)
  ✓  8.95:1  champagne on night-card  (blocked trace status, needs 4.5:1)
  ✓  7.60:1  alert-light on night-card  (alert trace status, needs 4.5:1)

  All 35 pairs meet WCAG AA.
  ```

---

### 5.3 Frontend TypeScript & Lint Checks

- **ESLint Scans**:
  - `packages/shared`: **0 errors, 0 warnings** (PASS)
  - `dashboard`: **0 errors, 0 warnings** (PASS)
  - `platform-dashboard`: **0 errors, 0 warnings** (PASS)
  - `landing-page`: **0 errors, 0 warnings** (PASS)
- **TypeScript Compiler Static Analysis (`tsc --noEmit`)**:
  - `packages/shared/tsconfig.json`: **0 errors** (PASS)
  - `dashboard/tsconfig.app.json`: **0 errors** (PASS)
  - `platform-dashboard/tsconfig.app.json`: **0 errors** (PASS)
- **Frontend Test Suite Execution**:
  - `dashboard`, `platform-dashboard`, `landing-page`, `packages/shared`: **0 tests configured / 0 tests executed** (`QA-02`).

---

## 6. Launch Review Sign-Off & Next Steps

This Master Enterprise Launch Audit Report serves as the definitive engineering and regulatory evaluation for `weblaud-us/ai-receptionist`. 

The commercial launch to Australian and United Kingdom dental clinics is formally **BLOCKED**. Commercial onboarding, marketing campaigns, and live patient phone routing must remain paused until all **21 Sprint 0 (P0)** launch blockers have been fully remediated, verified by automated test suites, and audited by the Launch Review Panel in a follow-up review.

**Signed,**  
**Role 8 — Launch Chair & Principal Technical Program Manager (TPM)**  
*On behalf of the 8-Person Google-Grade Launch Review Panel*
