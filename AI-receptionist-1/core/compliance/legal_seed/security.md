# Security overview

> **DRAFT.** Version `2026-11-19`. For customers' due diligence. Keep it true: every line below
> describes something the product does today. Fill in the hosting details in brackets.

**WEBLAUD LLC — LaudLine**  ·  Last reviewed: [date]  ·  Contact: [security@domain]

## Architecture

Calls arrive from the phone network through Twilio SIP trunks into LiveKit (real-time media). A
voice agent transcribes speech, generates replies with a language model, and speaks them with a
text-to-speech provider. Business data lives in MongoDB Atlas; short-lived state in Redis;
recordings (only when enabled) in S3-compatible storage. Hosting: [provider, regions]. Businesses in
the EU can be served from an EU region where all of this runs in the EU. [Confirm.]

## Data protection

- **Separation between businesses.** Every record carries a business identifier, and all access
  goes through a data layer that always filters on it. An automated check in our build pipeline
  fails if code reads business data any other way. Tests check that one business can never see
  another's calls, bookings or contacts.
- **Encryption.** TLS for the dashboard, the API and every connection to our providers. [Confirm
  whether the SIP trunk uses TLS/SRTP before claiming it.]
  Third-party tokens (e.g. Google Calendar) and identity numbers are encrypted at rest with Fernet
  keys held outside the database. Database, backups and storage are encrypted at rest by the
  providers.
- **Minimal retention.** Recording is off by default and announced when on. Each business sets how
  long transcripts and recordings are kept; scheduled jobs delete them. Data is deleted 30 days after
  cancellation.
- **On the call.** The assistant says it's an AI, reads bookings only to the number that made them,
  and never reads out other customers' details.
- **No training.** Customer data isn't used to train AI models; we use vendor API terms that exclude
  training. [List zero-retention settings confirmed per vendor.]

## Access control

- Dashboard users have roles (owner, admin, staff). Sessions use short-lived access tokens kept in
  memory and httpOnly refresh cookies that rotate on every refresh.
- Platform administrators must use multi-factor authentication (TOTP); production refuses to start
  without it.
- Every settings change, export, data deletion, and administrator action on a business is recorded
  in an audit log the business can see.
- Production access: [named individuals], least privilege, MFA on every provider console.

## Application security

- All inbound webhooks are signature-checked (Twilio, Meta/WhatsApp, Stripe); unsigned requests are rejected.
- Rate limits per IP and per business on sign-in, sign-up and APIs; per-business concurrent-call limits.
- Secrets are generated per environment and never committed; production refuses weak or default secrets.
- Logs are structured, mask phone numbers, and never include transcripts in production.
- Dependencies are updated automatically (Dependabot); every change runs lint, type checks and tests in CI.

## Availability and recovery

- Managed database with continuous backups [and point-in-time restore, retention ___].
- Health and readiness checks; external uptime monitoring; public status page [STATUS_PAGE_URL].
- Speech and language providers have automatic fallbacks.
- If the platform is unreachable, a separate fallback service forwards calls to the business's staff number.
- Deploys let live calls finish before a worker stops.

## Incident response

A written procedure: acknowledge within 15 minutes, mitigate, assess personal-data impact, notify
affected businesses without undue delay (within [48] hours, DPA §3) so they can meet their 72-hour
duty, then a written post-incident review.

## Compliance

GDPR / UK GDPR processor terms (DPA with SCCs), US state privacy "service provider" terms, a public
sub-processor list with 30 days' notice of changes, a data subject request tool for every business,
and AI disclosure on every call. We do not currently hold SOC 2 or ISO 27001 certification.
[Update when that changes.]
