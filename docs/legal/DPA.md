# Data Processing Agreement

> **DRAFT for counsel review — not legal advice.** Version `2026-11-19`. Fill in every `[bracket]`.
> Accepted in the dashboard (Legal & privacy) by the business owner; the acceptance records the
> version, date, signer name and title.

This Data Processing Agreement ("**DPA**") forms part of the Terms of Service between **WEBLAUD
LLC** ("**Processor**", "**we**") and the business that accepts it ("**Controller**", "**you**").
It applies whenever we process personal data on your behalf under the GDPR, the UK GDPR, the Swiss
FADP, US state privacy laws (as a "service provider" / "processor"), Canada's PIPEDA, the UAE PDPL,
or similar laws ("**Data Protection Law**").

## 1. Roles and instructions

1.1 You are the controller and we are your processor for the personal data described in Annex 1.

1.2 We process that data only on your documented instructions. These Terms, this DPA and your
settings in the dashboard are your instructions. We will tell you if we believe an instruction
breaks Data Protection Law.

1.3 You confirm that you have a lawful basis for the processing and have given your callers the
information the law requires (including that calls are answered by an AI and, where you enable it,
recorded).

## 2. Our obligations

2.1 **Confidentiality.** Everyone we authorise to process the data is bound by confidentiality.

2.2 **Security.** We apply the technical and organisational measures in Annex 2.

2.3 **No other use.** We do not sell or share the data, use it for our own purposes, combine it
with other data except to provide the Service, or use it to train AI models. ("Service provider"
terms under the CCPA/CPRA: we do not retain, use or disclose the data outside the direct business
relationship with you.)

2.4 **Assistance.** We help you respond to data subject requests. The dashboard (Legal & privacy)
lets you find, export and delete everything held about a phone number, and keeps a dated log of
each request. We assist with DPIAs and consultations with supervisory authorities where they relate
to the Service.

2.5 **Deletion and return.** You can export your data at any time. After the subscription ends we
keep it for [30] days so you can export it or come back, then delete it, including recordings,
unless the law requires us to keep it. Backups are overwritten within [35] days.

2.6 **Audits.** We make available the information needed to show compliance with this DPA,
including our security overview and, when available, third-party reports. On-site audits: once a
year, on [30] days' notice, at your cost, or more often if required by an authority.

## 3. Personal data breaches

We notify you **without undue delay, and in any event within [48] hours** of becoming aware of a
personal data breach affecting your data. We give you what we know (nature, categories and
approximate numbers, likely consequences, measures taken) and update you as we learn more, so you
can meet your own 72-hour notification duty.

## 4. Sub-processors

4.1 You authorise the sub-processors in our [sub-processor list](SUBPROCESSORS.md). We impose data
protection obligations on each that are no less protective than this DPA, and remain responsible
for them.

4.2 We give **at least 30 days' notice** of a new sub-processor (by e-mail to the account owner and
on the published list). You may object on reasonable data-protection grounds; if we cannot
accommodate the objection, you may terminate the affected Service and receive a pro-rata refund of
prepaid fees.

## 5. International transfers

Where data is transferred outside the EEA, UK or Switzerland to a country without an adequacy
decision, the Standard Contractual Clauses (Commission Implementing Decision (EU) 2021/914, Module 2
controller→processor, and Module 3 for onward transfers), the UK International Data Transfer Addendum,
and the Swiss amendments are incorporated by reference, with the details in Annex 1 and Annex 2.
[Choose: Clause 7 docking — yes; Clause 9 option 2 (general authorisation, 30 days); Clause 11
optional redress — no; Clause 13 supervisory authority — ___; Clause 17/18 law and courts — ___.]
Businesses can choose EU-region processing. [Confirm availability.]

## 6. Liability

Liability under this DPA is subject to the limitations in the Terms, except where Data Protection
Law does not allow them.

## 7. Term and precedence

This DPA lasts as long as we process personal data for you. If it conflicts with the Terms, this
DPA prevails; if it conflicts with the SCCs, the SCCs prevail.

---

## Annex 1 — Description of the processing

| | |
|---|---|
| **Data subjects** | People who call or message the Controller's business; the Controller's staff whose details are in the configuration (e.g. transfer numbers) |
| **Categories of data** | Phone number; voice audio (in transit; stored only if recording is on); transcripts; name, e-mail and address given on the call; appointment and order details; messages left; WhatsApp messages; call metadata (time, length, outcome); optional notes added by staff |
| **Special categories** | Not intended. Callers may volunteer health or other sensitive information (e.g. to a clinic). The Controller decides whether its use of the Service is appropriate for that; US healthcare (HIPAA) is not supported |
| **Nature and purpose** | Answering calls and messages with an AI assistant: speech-to-text, language model, text-to-speech; booking and changing appointments; taking messages; transferring calls; sending confirmations and reminders; summaries and statistics for the Controller |
| **Frequency** | Continuous, for each call or message |
| **Retention** | Transcripts: Controller's setting (default 90 days). Recordings: Controller's setting (default 30 days), only if enabled. Call metadata: for the life of the account (needed for billing). All data: deleted [30] days after cancellation. Erasure requests: data deleted or anonymised immediately via the dashboard |
| **Sub-processors** | See [SUBPROCESSORS.md](SUBPROCESSORS.md) |

## Annex 2 — Technical and organisational measures

- **Isolation between businesses:** every record carries a tenant identifier; data access goes
  through a repository layer that always filters by tenant; an automated check in the build stops
  code that bypasses it.
- **Access control:** role-based access (owner / admin / staff); platform administrators use
  multi-factor authentication; every settings change, export and deletion is written to an audit log.
- **Encryption:** TLS for all traffic; third-party tokens and sensitive identifiers encrypted at rest
  (Fernet); database and storage encrypted at rest by the hosting providers.
- **Callers' privacy on the call:** the assistant reads out bookings only to the phone number that
  made them; it discloses that it is an AI; recording is announced.
- **Minimisation and retention:** recording off by default; per-business retention enforced by
  scheduled deletion jobs; logs mask phone numbers and never contain transcripts in production.
- **Resilience:** managed database with backups; health checks; provider fallbacks for speech and
  language services; a telephony fallback that forwards calls to the business if the platform is down.
- **Monitoring and incidents:** error tracking, alerting, a written incident procedure with
  breach-notification steps.
- **Vendors:** API terms that exclude training on customer data; zero-retention options where
  available [list as confirmed].
- **Personnel:** confidentiality obligations; least-privilege access to production.
