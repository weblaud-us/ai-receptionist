# Legal and compliance — Phase 6

**Not legal advice.** This file sorts the rules into two groups: what the product already enforces,
and what still needs a lawyer or a business decision. Before relying on any rule, check it against
the primary source (⚠ VERIFY) and have counsel confirm it for each launch market. Budget a few hours
each with a **US telecom/TCPA lawyer**, a **GDPR / UK privacy lawyer**, an **Australian privacy /
consumer-law lawyer** (§7a — Australia is a first launch market) and, before the Gulf, **UAE
counsel** (ROADMAP §11).

Related: draft documents in [legal/](legal/README.md) · how to test what's built in
[PHASE_6_TESTING.md](PHASE_6_TESTING.md) · production steps in
[PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md#phase-6--legal-and-compliance) · incidents and
breaches in [RUNBOOK.md](RUNBOOK.md#incident-response).

Legend: ✅ in the product · 🧑‍⚖️ needs counsel · 📋 a business task (paperwork, registration, insurance)

---

## 1. Your two legal roles

| You are… | For… | So you need |
|---|---|---|
| **Processor** (GDPR / UK GDPR) · **service provider** (US state privacy laws) | Callers' data: transcripts, recordings, contact details, appointments | A DPA with every business (✅ accepted in the dashboard, text in [legal/DPA.md](legal/DPA.md)), a sub-processor list (✅ `GET /public/legal`), and use of their data only to run the service |
| **Controller** | Your own sales leads, website visitors, the businesses' account data | Your own [privacy policy](legal/PRIVACY.md), a lawful basis, opt-out handling, DNC compliance (Phase 4 gate) |

The business (your customer) is the controller of its callers' data and answers their requests. The
product gives it the tools.

## 2. Product-wide obligations (all markets)

| Obligation | Status | Where |
|---|---|---|
| **AI disclosure** at the start of every call and whenever asked | ✅ | Greeting always names the assistant as virtual (`TenantConfig.greeting_text`); "Honesty" rules in the base prompt (`core/prompts/builder.py`); sales opener check (`core/outbound/persona.check_opener`) |
| **Recording notice** whenever recording is on | ✅ | "This call may be recorded." appended to the greeting when recording is on (`greeting_text`); recording only on Pro (`core/billing/policy.py`) |
| **Transcription is processing**, even without recording | 📋 | Covered in the privacy policy and DPA drafts (Annex 1) |
| **Retention**, configurable per business, enforced by jobs | ✅ | Transcripts: `agent.transcript_retention_days` (default 90), recordings: `agent.recording.retention_days` (default 30), both purged by the tenant jobs (`services/tenant_jobs.py`, `core/compliance/retention.py`); after cancellation 30 days, then everything deleted (`core/services/offboarding.py`) |
| **Data subject requests** (find / export / delete everything for a phone number) | ✅ | Owner: **Legal & privacy** page; API `/t/privacy/*` (`core/compliance/subjects.py`); log with due date in `dsr_requests`; platform view: **Compliance** |
| **No training on customer data** by AI vendors | 🧑‍⚖️📋 | Use API terms that exclude training; request zero-data-retention where offered (OpenAI, Anthropic, Deepgram, ElevenLabs business plans). ⚠ VERIFY per vendor and keep the confirmations |
| **Emergency calls**: not an emergency service | ✅ | Base prompt + the local number in the business facts (911 US/CA, 999 UK/UAE, 112 EU…; `builder.emergency_number`); Terms §6 |
| **Accessibility: never trap callers** | ✅ | Saying "agent", "operator", "representative" or **pressing 0** goes to `transfer_to_human`, or a message when nobody is available (base prompt; `agent/receptionist.DtmfWatcher`) |
| **Consent revocation from any channel** | ✅ | SMS STOP/START/HELP (`POST /telephony/sms/inbound`), WhatsApp "stop", voice opt-out (Phase 4) → `consent_events`; contacts marked do-not-contact; platform-wide opt-outs also go on the permanent sales `dnc_list`; opted-out contacts get no texts (`core/notifications.notify_contact`) |
| **Evidence of what customers agreed to** | ✅ | Versions of terms, privacy and AUP stored per user at signup; DPA acceptance per business with signer name; re-acceptance when a version changes (`core/compliance/legal.py`) |
| **Security of processing** | ✅📋 | Encryption in transit, encrypted secrets, MFA for admins, tenant isolation guard, audit log (Phases 0–3); summary for customers: [legal/SECURITY.md](legal/SECURITY.md) |

## 3. United States

| Area | Requirement | Product | Still to do |
|---|---|---|---|
| TCPA: AI voice | FCC Declaratory Ruling FCC 24-17 (Feb 2024): AI voices count as "artificial or prerecorded voice" under 47 CFR 64.1200(a). Consent required for calls to mobile and residential numbers; prior express **written** consent for telemarketing | ✅ Phase 4 gate: AI cold calls only to business landlines (line-type lookup), consent records for anything else | 🧑‍⚖️ confirm the landline-only B2B approach |
| TCPA: identification and opt-out | Artificial-voice telemarketing must identify the caller at the start, give a callback number and offer an **automated opt-out** during the call (47 CFR 64.1200(b)) | ✅ templated opener, `add_to_dnc` tool and keyword listener (`core/outbound/optout.py`) | ⚠ VERIFY exact timing and content |
| Consent revocation | FCC rules: revoke consent "by any reasonable means", honoured within a set window; reported to take effect **25 November 2026** | ✅ one place records revocations from SMS, WhatsApp and voice; `dnc_list` is permanent | ⚠ VERIFY scope, dates and the honouring window; 🧑‍⚖️ confirm whether one revocation must cover informational texts from the same sender |
| National + state DNC | TSR / FCC rules; state lists | ✅ registry loader, 31-day freshness check (Task 4.3) | 📋 SAN registration with the FTC; state lists where required |
| Calling hours | 8 am–9 pm called party's local time; several states narrower | ✅ rule table (`core/outbound/rules.py`), fail-closed | 🧑‍⚖️ review state rows |
| State "mini-TCPA" laws | e.g. Florida FTSA, Oklahoma, Maryland, Washington | ✅ blocked by default (`SALES_BLOCKED_US_STATES`) | 🧑‍⚖️ before unblocking each |
| Caller ID | Accurate caller ID; STIR/SHAKEN | ✅ local-presence numbers you own (Task 4.1) | 📋 CNAM, carrier reputation registration |
| SMS | TCPA consent + **A2P 10DLC** brand/campaign registration or toll-free verification; CTIA keywords | ✅ STOP/START/HELP handled and recorded | 📋 10DLC registration before sending at volume |
| Recording | All-party-consent states (CA, DE, FL, IL, MD, MA, MT, NH, PA, WA, with nuances elsewhere — ⚠ VERIFY current list) | ✅ always announced when on; never silent | — |
| Privacy | CCPA/CPRA and other state laws; you are a "service provider" | ✅ DPA includes service-provider terms (§9) | 🧑‍⚖️ review |
| Healthcare | **HIPAA**: BAAs with every vendor | — | 📋 don't sell to US healthcare until BAAs exist (ROADMAP Task 2.1) |
| AI-specific laws | Utah AI disclosure, California bot disclosure (B&P Code §17940+), Colorado AI Act (high-risk decisions) | ✅ disclosure on every call; receptionist makes no consequential decisions | ⚠ VERIFY scope |

## 4. Canada

| Area | Product | Still to do |
|---|---|---|
| CRTC Unsolicited Telecommunications Rules: National DNCL (B2B exemptions), **ADAD** rules (automated dialling needs consent for solicitation), calling hours, caller identification | ✅ CA is human-assisted only for sales (`SALES_HUMAN_ASSISTED_COUNTRIES`); AI sales calls need consent (`SALES_AI_CONSENTED_COUNTRIES` excludes CA by default) | 📋 DNCL subscription; 🧑‍⚖️ how interactive AI voice is treated (⚠ VERIFY) |
| **CASL** for SMS / e-mail: consent, sender identification, unsubscribe | ✅ SMS STOP; owner e-mails are transactional | 🧑‍⚖️ review marketing e-mails before you send any |
| **PIPEDA**; **Québec Law 25** (privacy impact assessment, privacy officer, stricter consent) | ✅ DSR tool, retention | 📋 name a privacy officer; PIA before selling in Québec |

## 5. United Kingdom

| Area | Product | Still to do |
|---|---|---|
| **UK GDPR + DPA 2018** | ✅ DPA, DSR tool, retention, sub-processor list | 📋 ICO registration (data protection fee) — ⚠ VERIFY whether a non-UK company must pay |
| **UK representative** (Art. 27) if no UK establishment | — | 📋 appoint one (services exist for this) |
| **PECR** reg. 19: automated marketing calls need prior consent (also to corporate subscribers); reg. 21: live calls screened against TPS/CTPS | ✅ GB is human-assisted only for sales; TPS/CTPS loader (Task 4.3) | 📋 TPS/CTPS licence |
| International transfers | ✅ EU/UK tenants can run in an EU cell (Task 3.2) | 📋 UK IDTA or UK Addendum to EU SCCs; or vendors certified under the UK–US data bridge |

## 6. European Union

| Area | Product | Still to do |
|---|---|---|
| **GDPR**: DPA (Art. 28) | ✅ accepted in-app, versioned | 🧑‍⚖️ review [legal/DPA.md](legal/DPA.md); attach SCCs |
| Records of processing (Art. 30) | 📋 | Fill in §9 below and keep it current |
| **DPIA** (Art. 35): likely required for systematic processing of communications with AI | 📋 | Template in §10 below; do it before the first EU customer |
| Security (Art. 32) | ✅ | [legal/SECURITY.md](legal/SECURITY.md) |
| Breach notification (Art. 33/34): 72 hours to the authority, customers without undue delay | ✅ procedure | [RUNBOOK.md → Incident response](RUNBOOK.md#incident-response) |
| Data subject rights | ✅ | DSR tool for businesses; your own requests via `PRIVACY_CONTACT_EMAIL` |
| **EU representative** (Art. 27) if WEBLAUD LLC has no EU establishment | — | 📋 appoint one |
| International transfers | ✅ EU cell design | 📋 vendors on the EU–US Data Privacy Framework or SCCs + transfer impact assessment; prefer EU-region processing for EU tenants |
| **ePrivacy** national laws: consent for automated marketing calls; B2B cold calls vary (strict in Germany) | ✅ no EU AI sales calls by default | 🧑‍⚖️ per country before any outbound |
| **EU AI Act** Art. 50: people must be told they're talking to an AI (applies from August 2026 — ⚠ VERIFY whether a delay was adopted); keep system documentation. A receptionist is not "high-risk" under Annex III in general | ✅ disclosure; this repo is the documentation | ⚠ VERIFY before moving into employment, credit or essential services |

## 7a. Australia (first launch market, not in ROADMAP §11)

Added for the AU/UK/EU launch. Have an **Australian privacy / consumer-law lawyer** confirm every row
before the first Australian customer.

| Area | Product | Still to do |
|---|---|---|
| **Privacy Act 1988** and the Australian Privacy Principles (APPs). Small businesses under A$3m turnover are mostly exempt, but you (the platform) may not be, and many customers will ask anyway | ✅ DPA, DSR tool, retention, sub-processor list | 🧑‍⚖️ confirm whether the Act applies to WEBLAUD LLC; an APP-compliant privacy policy (APP 1) and collection notice (APP 5) — ⚠ VERIFY the 2024 amendments (statutory tort, children's code) |
| **APP 8** cross-border disclosure: callers' data goes to US/EU vendors | ✅ sub-processor list with locations | 🧑‍⚖️ wording in the privacy policy and DPA; prefer the nearest region for AU tenants |
| **Notifiable Data Breaches** scheme: tell the OAIC and affected people about eligible breaches, assessment within 30 days | ✅ breach procedure | ✅ [RUNBOOK.md → Personal data breach](RUNBOOK.md#personal-data-breach-au-ndb--uk-ico) |
| **Spam Act 2003**: SMS and e-mail need consent, sender identification and a working unsubscribe honoured within 5 business days | ✅ consent on the call, STOP/START, opt-outs kept even for people who aren't contacts yet | ⚠ VERIFY the SMS sender ID register rules (ACMA) for alphanumeric senders |
| **Do Not Call Register Act 2006** + Telemarketing and Research Industry Standard 2017 (calling hours, ID, no calls on public holidays) | ✅ no outbound to AU (`SALES_*_COUNTRIES` exclude it); inbound only | 🧑‍⚖️ before any AU outbound, including whether an AI voice counts as a "recorded message" |
| **Call recording**: federal Telecommunications (Interception and Access) Act plus state surveillance-devices laws; some states need every party's consent | ✅ recording off by default; the greeting announces it when on | 🧑‍⚖️ confirm the announcement is enough for each state |
| **Australian Consumer Law**: unfair contract terms in standard-form **small-business contracts** (penalties since Nov 2023); misleading conduct (say it's an AI) | ✅ AI disclosure in every greeting | 🧑‍⚖️ review the Terms: liability cap (§12), auto-renewal, unilateral changes (§14.1), indemnity (§13) |
| **GST**: register once AU sales pass A$75,000 a year (non-resident suppliers of digital services) | ✅ Stripe Tax can collect it | 📋 accountant; Stripe Tax registration for AU |
| Australian numbers need a regulatory bundle (AU business address) | ✅ operator-assisted ordering (platform app → Numbers) | 📋 a bundle per business in the Twilio console |

## 7b. Dental practices: health data in the UK and Australia

Dental is a regulated-data vertical (`vertical == "dental"`). Callers describe symptoms and
treatment interests, which is **health data**. Have the UK and Australian privacy lawyers confirm
each row before the first dental customer in that country. **No HIPAA claims**: the product is not
offered to US healthcare.

| Area | Product | Still to do |
|---|---|---|
| **UK GDPR Art. 9** special category data; DPA 2018 Sch. 1 Part 1 para 2 (health care). The practice is the controller and needs the Art. 9 condition; we are the processor | ✅ DPA §8 and Annex 1; application-layer encryption of symptoms, notes and transcripts (`core/sensitive.seal`, migration m0005); short default retention (recordings 30 d, transcripts 90 d, leads 365 d) with presets in Legal & privacy | 🧑‍⚖️ confirm the DPA §8 wording; a DPIA template for practices (high-risk processing of health data, Art. 35) |
| **APP 3.3 / APP 5 / APP 6 / APP 11** (sensitive information: consent, collection notice, use, security) | ✅ AI disclosure and recording notice first in the dental greeting; encryption; retention; DSR tool | 🧑‍⚖️ APP 5 collection-notice wording for practices to put on their website and in the greeting |
| **APP 8** cross-border disclosure (US speech and language vendors) | ✅ sub-processor list with locations; `ap-southeast-2` hosting planned | 🧑‍⚖️ whether reasonable-steps contracts are enough or callers must be told about overseas processing |
| **Notifiable Data Breaches** (AU) and **ICO 72-hour** reporting (UK) | ✅ breach procedure; DPA §8.5 | ✅ [RUNBOOK.md → Personal data breach](RUNBOOK.md#personal-data-breach-au-ndb--uk-ico) |
| **Clinical safety**: the assistant must not diagnose or advise | ✅ dental rules: no diagnosis, medication or price estimates; red flags mean 000 or 999 first (`core/prompts/verticals/dental.py`), with the number coming from code (`triage_dental_emergency`); eval cases `seed_dental_*` | 🧑‍⚖️ have a dentist review the red-flag list and the triage wording |
| **Advertising and price claims**: AHPRA advertising guidelines (AU), GDC standards and the ASA CAP Code (UK). No misleading "from" prices or guarantees | ✅ the assistant quotes only prices the practice entered, as "from" prices or consultation fees, and never estimates treatment costs | 🧑‍⚖️ confirm that "from" pricing on calls meets AHPRA and GDC guidance |
| **NHS / private** (UK): tell patients clearly whether NHS treatment is available | ✅ per-practice NHS settings drive the facts line (`dental.accepts_nhs`, `accepts_new_nhs_patients`) | — |
| **Identifiers**: NHS numbers, Medicare numbers, dates of birth | ✅ never asked for by the assistant; masked in logs and error reports (`core/log.redact`); Sentry runs without frame variables | — |
| **Missed-call recovery SMS** (AU Spam Act 2003; UK PECR reg. 22 / a service message following the caller's own call) | ✅ off by default; names the practice; "Reply STOP"; one per caller per day; respects opt-outs (`core/services/recovery.py`) | 🧑‍⚖️ confirm it is a service message (not marketing) in both countries |
| **Call recording consent** (AU state surveillance-devices laws; UK) | ✅ recording off by default; the disclosure comes first in the dental greeting | 🧑‍⚖️ as §7a |
| **Processor data use**: no training on call data | ✅ `DEEPGRAM_MIP_OPT_OUT=true`, `OPENAI_STORE=false` (the production guard refuses anything else); `OPENAI_ZDR_ENABLED` / `DEEPGRAM_ZDR_ENABLED` record whether each vendor approved ZDR | 📋 apply for OpenAI ZDR; until it is approved, don't claim ZDR |
| **Practice-management systems** (Cliniko, Dentally): the practice's own processors | ✅ credentials encrypted, never returned by the API; connected only on the practice's instruction; erasure results say the PMS isn't changed | 📋 Dentally partner application before enabling `PMS_DENTALLY_ENABLED` |

## 7. Middle East

| Area | Product | Still to do |
|---|---|---|
| **UAE** telemarketing (Cabinet Resolutions 56 & 57 of 2024): TDRA approval, 9 am–6 pm, DNCR, licensed UAE number, recording with notice, heavy fines | ✅ no outbound to AE (`SALES_*_COUNTRIES` exclude it); inbound only | ⚠ VERIFY that inbound AI reception of customer-initiated calls isn't telemarketing |
| UAE **PDPL** (Federal Decree-Law 45/2021); sector data localisation (health, finance); DIFC and ADGM have their own laws | ✅ region "me" exists on tenants | 🧑‍⚖️ before the first UAE customer |
| **Saudi Arabia** PDPL (in force since September 2023), transfer restrictions; CST telecom rules | — | 🧑‍⚖️ research before selling there |
| Local numbers often need a local entity | — | 📋 go through a local reseller (ROADMAP §11.7) |

## 8. Documents before the first paying customer

| Document | Draft | Published at | Notes |
|---|---|---|---|
| Terms of Service | [legal/TERMS.md](legal/TERMS.md) | `TERMS_URL` | Accepted at signup (versioned) |
| Privacy Policy | [legal/PRIVACY.md](legal/PRIVACY.md) | `PRIVACY_URL` | Your controller processing |
| Data Processing Agreement | [legal/DPA.md](legal/DPA.md) | `DPA_URL` | Accepted per business in **Legal & privacy** |
| Sub-processor list | [legal/SUBPROCESSORS.md](legal/SUBPROCESSORS.md) | `SUBPROCESSORS_URL`, also `GET /public/legal` | 30 days' notice of changes; source of truth `core/compliance/legal.SUBPROCESSORS` |
| Acceptable Use Policy | [legal/AUP.md](legal/AUP.md) | `AUP_URL` | Accepted at signup |
| Service Level Agreement (Pro and up) | [legal/SLA.md](legal/SLA.md) | `SLA_URL` | |
| Security overview | [legal/SECURITY.md](legal/SECURITY.md) | `SECURITY_URL` | For customers' due diligence |
| Cookie banner | — | marketing site | Only if the site sets non-essential cookies (EU/UK) |

Production refuses `SIGNUP_ENABLED=true` until `TERMS_URL`, `PRIVACY_URL` and `DPA_URL` are set.
When you change a document's wording, bump its version in `core/compliance/legal.py`. Owners then
see "We've updated…" in **Legal & privacy**, and a business whose DPA is out of date sees
"Action needed".

## 9. Records of processing (GDPR Art. 30) — fill in and keep current

| Processing | Role | Data subjects | Data | Purpose | Retention | Recipients |
|---|---|---|---|---|---|---|
| Answering calls for businesses | Processor | Callers | Phone number, voice, transcript, name, appointment details, messages | Provide the AI receptionist | Transcripts 90 d (configurable), recordings 30 d (configurable, Pro), call metadata for the life of the account | Sub-processors (§8) |
| WhatsApp conversations | Processor | Customers who message the business | Phone number, messages | As above | 15 d conversation memory, then as transcripts | Meta, OpenAI |
| Customer accounts | Controller | Business owners and staff | Name, e-mail, phone, login data | Run the account, billing, support | Account life + 30 d; invoices as tax law requires | Stripe, Resend, Sentry |
| Sales prospecting | Controller | Business decision-makers | Business phone, name, call outcome, consent records | Sell the product (Phase 4) | Decide and enforce (suggest: leads 24 months after last contact — not automated yet); DNC and consent records permanently | Twilio, LiveKit, AI vendors |
| Website | Controller | Visitors | Analytics, form submissions | Marketing | Per the cookie policy | Analytics vendor |

## 10. DPIA outline (do it before the first EU customer)

1. **What:** inbound AI phone answering for small businesses; speech-to-text, LLM, text-to-speech;
   optional recording; transcripts kept for the business.
2. **Necessity and proportionality:** recording off by default; retention short and configurable;
   no training on customer data; minimum data in prompts (only that caller's own bookings).
3. **Risks to callers:** wrong information (the AI makes mistakes), unexpected recording, data
   leaking between businesses, vendor access, transfers outside the EU, callers unable to reach a person.
4. **Measures:** AI disclosure; recording notice; tenant isolation (repository layer + CI guard);
   ownership checks before reading out bookings; "press 0" / ask for a person; EU-region cell;
   DPA and SCCs with vendors; encryption; audit log; DSR tool; deletion jobs.
5. **Residual risk and sign-off:** who decided, when, and when it will be reviewed.

## 11. Business protections 📋

- **Cyber liability** and **technology E&O / professional liability** insurance.
- Trademark search on the product name in each target market before printing it anywhere.
- Contracts with customers are with **WEBLAUD LLC**, never with you personally; keep company records clean.
