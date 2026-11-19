# Phase 5 — billing and self-serve onboarding: what's built and how to test it

What Phase 5 added (ROADMAP.md §10), how to try it locally, and how to check each acceptance item.
Production steps: [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md#phase-5--billing-and-self-serve-signup).
When something goes wrong: [RUNBOOK.md](RUNBOOK.md#billing-and-numbers).

## Safe by default

- `SIGNUP_ENABLED=false`: there is no public sign-up until you switch it on.
- `NUMBER_PROVISIONING_ENABLED=false`: nothing is bought on Twilio until you switch it on.
- Without `STRIPE_SECRET_KEY`, billing is off (development only). Sign-ups then get a local trial and
  nothing is charged. Production won't boot with `SIGNUP_ENABLED=true` and no Stripe keys, webhook
  secret, plan price, e-mail or `DASHBOARD_URL`.
- Businesses you created by hand (`/platform`) have `billing.plan = "manual"`. They are never capped,
  never synced with Stripe and see no plan picker. Nothing changes for existing customers.

## What's in the code

| Roadmap task | Where |
|---|---|
| 5.1 Cost per minute | `core/billing/costs.py`: every rate as data (low/high + where it came from), cost per minute with and without recording / outbound, margin per plan. Each call's `cost_usd` (on `calls` and `usage_events`) uses the midpoint. Platform admins: **Unit economics** page (`GET /platform/costs`): cost per minute, margin per plan, list MRR, minutes and estimated cost over the last 30 days |
| 5.2 Pricing | `core/billing/plans.py`: Starter $69 / 150 min, Growth $149 / 400, Pro $299 / 1,000, Agency (custom, not self-serve), $0.25/min overage, annual = 10 × monthly, 14-day / 50-minute trial with card required. Plan → `tenant.limits` (minutes, users, numbers, concurrent calls). Recording only on Pro; invites stop at the plan's user limit |
| 5.3 Stripe | `core/billing/stripe_api.py`: REST over httpx (no SDK), Checkout (subscription + metered overage price + optional setup fee, trial, card always, Stripe Tax, tax-ID collection, billing address), Customer Portal, meter events, webhook signature check (HMAC, 5-minute tolerance). Reads subscriptions in both API shapes (the period moved to subscription items in 2025) |
| 5.3 Webhooks | `core/billing/lifecycle.py`, `POST /billing/stripe/webhook`: `checkout.session.completed`, `customer.subscription.created/updated/deleted`, `invoice.paid`, `invoice.payment_failed`. Each event is claimed in `stripe_events` by id. A handler that fails releases its claim and answers 500, so Stripe retries. A suspension we set holds until a payment. An old subscription can't overwrite the current one. A deleted business is never revived |
| 5.3 Usage metering | At call end (`agent/persistence.py`) one `usage_events` row per call, **rounded up to the second**, `billable: false` for your own sales calls and for calls answered as "unavailable". The tenant's `billing.period_seconds` is refreshed so the next call sees a cap. `core/billing/metering.py` sends overage to the Stripe meter once a day, and every tick in the last 2 h of a period. Exactly once: the report is saved as `overage_pending` before it is sent, its identifier is `tenant:period:cumulative_total`, and a pending report is re-sent unchanged |
| 5.3 Alerts, cap, suspension | `core/billing/jobs.py` (`billing_tick`, every 15 min; in the API process without Redis). E-mails at 80 % and 100 % of included minutes, once each per period. Default at 100 % = keep answering and bill overage; owners can choose a **hard cap**. The trial is always capped. Past due for `PAST_DUE_SUSPEND_DAYS` → suspended |
| 5.3 Never silence | `core/billing/usage.gate()` at call start (from the cached tenant, no DB round trip). Suspended, cancelled-in-grace or capped businesses → `ServiceUnavailableAgent` (`agent/receptionist.py`): forwarded to the transfer number when it's open, otherwise "please call back later" — the caller is never told why |
| 5.4 Signup | `routers/signup.py`: `GET /public/plans`, `POST /signup` (per-IP limit, terms required, password policy, creates owner + business with status `incomplete`, signs in), e-mail verification (`/signup/verify-email`, `/signup/resend-verification`). Dashboard: `/signup` (plan picker, monthly/yearly) → Stripe Checkout → `/onboarding?checkout=success` |
| 5.4 Wizard | `Onboarding.tsx`: business type → basics → hours → services → transfers → voice → **browser test call** → **get a number** (search by area code, buy, live progress) or keep your number and forward. A business without a plan, paused or cancelled lands on **Plan & billing**; their data endpoints stay locked, billing and export don't |
| 5.4 Numbers | `core/services/numbers.py`: Twilio search; an order walks purchased → Elastic SIP trunk → LiveKit inbound trunk (`ListUpdate add`) → routing table (`pending`) → synthetic call through the phone network → `active`. Each step is recorded and idempotent; the `provision_number` job retries 5× with backoff; platform admins can retry (`POST /platform/number-orders/{id}/retry`). Non-US/CA orders stop at `needs_documents` until you approve (`…/approve`). Requires a verified e-mail and the plan's number limit |
| 5.4 Abuse prevention | `core/billing/policy.py` on every owner settings change: no premium-rate transfer numbers (`TRANSFER_BLOCKED_PREFIXES`), no international transfers unless the plan allows it (US↔CA is one zone), **trial transfers only to the owner's number verified by SMS code** (`/t/billing/verify-phone`). Plus: card required, trial minute cap, per-business concurrent-call limit (Phase 3) |
| 5.5 Export | `core/services/offboarding.py`, `GET /t/export` (one zip) or `/t/export/{calls.csv,transcripts.json,contacts.csv,appointments.csv,appointments.ics,messages.csv}`. Owners only, audited, 10/hour. CSV cells starting with `= + - @` are defused (spreadsheet formula injection) |
| 5.5 Offboarding | Cancel (Stripe Portal) → `cancelled`, e-mail with the export link. Day 27: final warning. Day 30: numbers released (LiveKit trunk, Twilio), then every tenant collection deleted, recordings deleted from storage, members removed (users with no other business are deleted). A tombstone tenant row and `deletion_log` remain. Re-subscribing before day 30 restores everything |

**Not built, deliberately:**
- **Porting a number in** (option c in the wizard) is manual: the LOA process is with the carrier.
  The wizard offers forwarding instead.
- **Regulatory bundles** (UK/EU/UAE numbers) are collected outside the app for now; the order waits
  at `needs_documents` and the wizard says so.
- **Plan changes and cancellation** happen in Stripe's Customer Portal, not in our screens (Task 5.3).

## Try it locally (no Stripe, no Twilio)

```bash
# AI-receptionist-1/.env (development)
SIGNUP_ENABLED=true
NUMBER_PROVISIONING_ENABLED=true
TELEPHONY_PROVIDER=softphone          # fake +1555 numbers
```

1. `uv run uvicorn main:app --port 8002` and `npm run dev` in `dashboard/`.
2. Open `/signup`, pick Growth, create an account. With no Stripe key you go straight to the wizard
   on a 14-day local trial.
3. The verification e-mail is logged (no `RESEND_API_KEY`). Copy the `/verify-email?token=…` link
   from the API log, or confirm in Mongo:
   `db.users.updateOne({email: "…"}, {$set: {email_verified_at: new Date()}})`.
4. Finish the wizard. On **Get a phone number**, search any area code and pick a `+1555…` number;
   it goes live at once (mapping only). Call it from Linphone (docs/TELEPHONY_TESTING.md).
5. **Plan & billing**: usage, hard cap, verify a phone (the code is shown in a toast when no SMS
   provider is set up), **Export all data**.

## Try it with Stripe (test mode)

1. In the Stripe dashboard (test mode):
   - **Billing → Meters**: create a meter with event name `overage_minutes`, aggregation *Sum*, value
     key `value`, customer mapping `stripe_customer_id`.
   - **Products**: Starter / Growth / Pro, each with a monthly and a yearly recurring price; one
     "Extra minutes" product with a **usage-based** price of $0.25 per unit on that meter.
   - **Customer Portal** settings: allow card updates, plan switching between your three products,
     cancellation (at period end), invoice history.
   - **Tax**: enable Stripe Tax (and add your registrations) or set `STRIPE_AUTOMATIC_TAX=false`.
2. Put the `price_…` ids and `sk_test_…` in `.env`. Forward webhooks:
   `stripe listen --forward-to localhost:8002/billing/stripe/webhook` and set the printed
   `whsec_…` as `STRIPE_WEBHOOK_SECRET`.
3. Sign up → Checkout opens → card `4242 4242 4242 4242` → you return to the wizard with status
   **Free trial**.

## Acceptance checks (ROADMAP §10)

**1. A stranger signs up, pays, configures, gets a number and receives a real call in < 15 min.**
Staging with Stripe test mode, `NUMBER_PROVISIONING_ENABLED=true` and the real Twilio trunk. Hand
the signup URL to someone who has never seen the product; time them with no help. Pass = their first
call shows under **Calls** in under 15 minutes. Note where they hesitated and fix the wording.

**2. Overage minutes appear correctly on the next Stripe invoice (test clock).**
1. Stripe → **Billing → Test clocks** → new clock; create a customer on it, then set that customer's
   id on a test tenant (`billing.stripe_customer_id`) and start a Growth subscription for it through
   Checkout (or the dashboard with the same customer).
2. Insert usage over the included minutes, e.g. 410 minutes of `usage_events`
   (`{tenant_id, kind: "voice_minute", seconds: 24600, billable: true, at: <now>}`).
3. Run one billing tick: `uv run python -m scripts.billing_tick` (or restart the jobs worker —
   `billing_tick` runs at startup). `tenant.billing.overage_reported` now holds `{<period>: 10}`.
4. Advance the test clock past the period end. The new invoice has **10 × $0.25** on the extra-minutes
   line. Running the tick again sends nothing more (same total).

**3. Failed payment → past_due → suspended → calls forwarded, never silence.**
Test card `4000 0000 0000 0341` (attaches, then fails), advance the test clock to the renewal:
`invoice.payment_failed` → status **Payment failed**, banner and e-mail. Advance 7 more days (or set
`billing.past_due_since` back 8 days) and let the billing tick run → **Paused**. Call the number: with
a transfer number set and open you're connected to it; otherwise you hear "We can't take your call
right now. Please call back a little later." Update the card in the Portal → `invoice.paid` →
**Active**, the next call is answered by the assistant.
Automated: `tests/test_phase5_billing.py::test_failed_payment_past_due_suspended_then_paid`.

**4. Cancel → export works → data deleted after retention.**
Cancel in the Portal (immediately, for the test) → **Cancelled**, e-mail with the deletion date.
**Export all data** still works and contains every file. Set `billing.delete_after` (and
`number_release_after`) to the past, let the billing tick run → numbers released, data gone,
`deletion_log` row written, owner e-mailed.
Automated: `test_cancel_export_then_deleted_after_retention`.

## Automated tests

`uv run pytest tests/test_phase5_billing.py` — 34 tests, no network: signature checks, every webhook
transition, idempotency and retry, the call gate, alerts once per threshold, exactly-once metering
across a failed send, plan rules, signup, checkout, number orders resuming from the failed step,
export, deletion and reactivation.
