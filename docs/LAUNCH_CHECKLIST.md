# Launch checklist — Task 7.2

Tick every box before you open sign-ups (`SIGNUP_ENABLED=true`) or start paid marketing. Each line
links to where the detail lives.

## Product and infrastructure

- [ ] Phases 0–3 acceptance checklists all green ([PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md)).
- [ ] Phase 5 at least manually workable: you can take a payment, provision a number and cancel
      ([PHASE_5_TESTING.md](PHASE_5_TESTING.md)).
- [ ] Evals pass at 100 % on the production model, including the cases from pilot calls
      (`RUN_EVALS=1 pytest tests/evals`).
- [ ] Load test passed at your expected peak concurrent calls ([PHASE_3_TESTING.md](PHASE_3_TESTING.md)).
- [ ] Rollback rehearsed on staging within the last month ([RUNBOOK.md](RUNBOOK.md#a-deploy-went-bad)).

## Legal (Phase 6)

- [ ] Terms, Privacy Policy, DPA, AUP, sub-processor list, SLA and security overview reviewed by
      counsel and published. `TERMS_URL`, `PRIVACY_URL`, `DPA_URL`, `AUP_URL`, `SUBPROCESSORS_URL`,
      `SLA_URL` and `SECURITY_URL` are set ([legal/](legal/README.md)).
- [ ] Every 🧑‍⚖️ item for your launch markets signed off ([COMPLIANCE.md](COMPLIANCE.md)).
- [ ] Records of processing and the DPIA filled in (COMPLIANCE.md §9–10) if you serve the EU/UK.
- [ ] A2P 10DLC (or toll-free verification) approved for the SMS sender; inbound SMS webhook set to
      `https://<api>/telephony/sms/inbound`.
- [ ] Zero-retention / no-training confirmed in writing with each AI vendor.
- [ ] Cyber and tech E&O insurance in place; trademark search done.

## Marketing site

- [ ] Per-vertical landing pages for your launch verticals.
- [ ] The **live demo number** (`DEMO_PHONE_NUMBER`) prominent on every page. It's a number
      assigned to a business set up to show off the product (a convincing example in your main
      vertical, with knowledge about your own product and pricing), and someone has called it today.
- [ ] Pricing page matching `core/billing/plans.py` and the Stripe prices.
- [ ] FAQ, security page (from [legal/SECURITY.md](legal/SECURITY.md)), status page link.
- [ ] Every "Start free trial" button goes to `<dashboard>/signup?plan=growth`.
- [ ] Cookie banner if the site sets non-essential cookies (EU/UK).
- [ ] 2–3 case studies from pilots, with real numbers ([PILOT_PLAYBOOK.md](PILOT_PLAYBOOK.md#6-ending-the-pilot)).

## Help and support ([SUPPORT.md](SUPPORT.md))

- [ ] Help centre published from [help/](help/README.md) at `HELP_CENTER_URL`: setup guide, call
      forwarding per carrier, calendar connection, editing FAQs, troubleshooting, caller data requests.
- [ ] Support e-mail (`SUPPORT_EMAIL`) answered, with in-app chat (Crisp or Intercom) if you want
      it. Target: first response in under 4 business hours.
- [ ] Status page live (`STATUS_PAGE_URL`), on a separate provider, with monitors on `/readyz` and
      the demo number.
- [ ] Canned replies ready for the top 10 questions (SUPPORT.md §4).

## Operations

- [ ] Alerts route to your phone; on-call contacts filled in at the bottom of the runbook.
- [ ] **Unit economics → Margin per business** checked; no paying business at a loss.
- [ ] Monthly report e-mail tested on staging (`scripts.tenant_jobs_tick --at <the 1st>`).
- [ ] Backups restored once on staging (RUNBOOK → Mongo failover / restore).

## Launch day

- [ ] `SIGNUP_ENABLED=true`, `NUMBER_PROVISIONING_ENABLED=true` in production; production boots
      (the guard checks Stripe, e-mail and legal URLs).
- [ ] You sign up as a stranger on a fresh browser, get a number, call it. Under 15 minutes.
- [ ] Watch Sentry, the latency dashboard and **Transcript review** closely for the first week.
