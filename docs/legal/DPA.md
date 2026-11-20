# Data Processing Agreement

> **DRAFT for counsel review — not legal advice.** Version `2026-11-20`. Fill in every `[bracket]`.
> Accepted in the dashboard (Legal & privacy) by the business owner; the acceptance records the
> version, date, signer name and title.

This Data Processing Agreement ("**DPA**") forms part of the Terms of Service between **WEBLAUD
LLC** ("**Processor**", "**we**") and the business that accepts it ("**Controller**", "**you**").
It applies whenever we process personal data on your behalf under the GDPR, the UK GDPR and the
Data Protection Act 2018, Australia's Privacy Act 1988 and the Australian Privacy Principles
("**APPs**"), the Swiss FADP, US state privacy laws (as a "service provider" / "processor"), Canada's
PIPEDA, the UAE PDPL, or similar laws ("**Data Protection Law**").

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

5.2 **Australia (APP 8).** Where personal information of Australian callers is disclosed to an
overseas recipient (the sub-processors and locations in [SUBPROCESSORS.md](SUBPROCESSORS.md)), we
bind that recipient by contract to handle it consistently with the APPs. Australian businesses can
choose processing in the AWS Asia Pacific (Sydney) region, `ap-southeast-2`. [Confirm availability
and which vendors still process outside Australia: speech and language models.]

5.3 **United Kingdom.** UK businesses can choose processing in the AWS Europe (London) region,
`eu-west-2`. Transfers from the UK rely on the UK–US data bridge where the recipient is certified,
or the UK International Data Transfer Addendum. [Confirm availability.]

## 6. Liability

Liability under this DPA is subject to the limitations in the Terms, except where Data Protection
Law does not allow them.

## 7. Term and precedence

This DPA lasts as long as we process personal data for you. If it conflicts with the Terms, this
DPA prevails; if it conflicts with the SCCs, the SCCs prevail.

## 8. Health data (dental and other healthcare practices)

8.1 **What this covers.** Callers to a dental practice describe symptoms, pain, injuries and the
treatment they want. That is data concerning health: special category data under UK GDPR
Article 9, and "sensitive information" (health information) under the Privacy Act 1988 (Cth).

8.2 **Your basis.** You, as controller, are responsible for the lawful basis and the Article 9
condition (for health care, usually Article 9(2)(h) with the Data Protection Act 2018, Schedule 1,
Part 1, paragraph 2), and, in Australia, for collecting health information with consent or under
another APP 3.4 exception and for the APP 5 collection notice. The assistant's greeting discloses
that it is an AI and, when recording is on, that the call is recorded.

8.3 **What we do for it.** For dental practices the Service:
- does not give clinical advice, diagnoses or medication guidance, and tells callers with red-flag
  symptoms to call emergency services (000 in Australia, 999 in the UK);
- stores callers' symptom descriptions, treatment notes and call transcripts encrypted at the
  application layer, in addition to the hosting providers' encryption at rest;
- removes NHS numbers, Medicare numbers and dates of birth from our logs and error reports, and
  never sends free-text health details to error tracking;
- keeps call data for short default periods (recordings 30 days if recording is on, transcripts
  90 days, enquiry leads 365 days), which you can shorten in Legal & privacy;
- opts every speech-to-text request out of the provider's model-improvement programme and sends
  language-model requests with storage for training and evaluation turned off. Zero Data Retention
  with the language-model provider applies only where it has been approved for our account
  [state whether approved];
- writes bookings to your practice-management system (Cliniko or Dentally) only if you connect it,
  using credentials you provide, which we store encrypted.

8.4 **No HIPAA.** The Service is not offered to US covered entities or business associates under
HIPAA, and we make no HIPAA claims. This section addresses UK GDPR and the Australian Privacy Act.

8.5 **Breaches.** Our notice under §3 lets you meet your own deadlines: the ICO within 72 hours
(UK GDPR Article 33), and the Notifiable Data Breaches scheme in Australia (assessment within
30 days; notify the OAIC and affected individuals of an eligible data breach).

8.6 **Your systems stay yours.** Records in your practice-management system are your records. A
deletion or access request handled in our dashboard does not change them, and the dashboard says
so when a system is connected.

---

## Annex 1 — Description of the processing

| | |
|---|---|
| **Data subjects** | People who call or message the Controller's business; the Controller's staff whose details are in the configuration (e.g. transfer numbers) |
| **Categories of data** | Phone number; voice audio (in transit; stored only if recording is on); transcripts; name, e-mail and address given on the call; appointment and order details; messages left; WhatsApp messages; call metadata (time, length, outcome); optional notes added by staff |
| **Special categories** | **Dental and healthcare practices:** health data (UK GDPR Art. 9; health information under the Privacy Act 1988): symptoms, injuries, pain and treatment interests described by callers. Handled as in §8. **Other businesses:** not intended, although callers may volunteer it. US healthcare (HIPAA) is not supported |
| **Nature and purpose** | Answering calls and messages with an AI assistant: speech-to-text, language model, text-to-speech; booking and changing appointments; taking messages; transferring calls; sending confirmations and reminders; summaries and statistics for the Controller |
| **Frequency** | Continuous, for each call or message |
| **Retention** | Transcripts: Controller's setting (default 90 days). Recordings: Controller's setting (default 30 days), only if enabled. Dental enquiry leads: default 365 days. Call metadata: for the life of the account (needed for billing). All data: deleted [30] days after cancellation. Erasure requests: data deleted or anonymised immediately via the dashboard |
| **Sub-processors** | See [SUBPROCESSORS.md](SUBPROCESSORS.md) |

## Annex 2 — Technical and organisational measures

- **Isolation between businesses:** every record carries a tenant identifier; data access goes
  through a repository layer that always filters by tenant; an automated check in the build stops
  code that bypasses it.
- **Access control:** role-based access (owner / admin / staff); platform administrators use
  multi-factor authentication; every settings change, export and deletion is written to an audit log.
- **Encryption:** TLS for all traffic; third-party tokens and sensitive identifiers encrypted at rest
  (Fernet); for dental practices, callers' symptom descriptions, treatment notes and call transcripts
  are also encrypted at the application layer; database and storage encrypted at rest by the
  hosting providers.
- **Callers' privacy on the call:** the assistant reads out bookings only to the phone number that
  made them; it discloses that it is an AI; recording is announced.
- **Minimisation and retention:** recording off by default; per-business retention enforced by
  scheduled deletion jobs; logs mask phone numbers, e-mail addresses, NHS and Medicare numbers and
  dates of birth, and never contain transcripts in production; error reports carry no free-text
  health details or stack-frame variables.
- **Resilience:** managed database with backups; health checks; provider fallbacks for speech and
  language services; a telephony fallback that forwards calls to the business if the platform is down.
- **Monitoring and incidents:** error tracking, alerting, a written incident procedure with
  breach-notification steps.
- **Vendors:** API terms that exclude training on customer data; Deepgram model-improvement
  opt-out on every request; OpenAI requests sent with `store` off; Zero Data Retention where
  approved [list as confirmed].
- **Personnel:** confidentiality obligations; least-privilege access to production.
