# Phase 7 — pilot, launch and sales: what's built and how to test it

What Phase 7 added to the product (ROADMAP §12). The non-code half lives in its own playbooks:

| Task | Document |
|---|---|
| 7.1 Pilot | [PILOT_PLAYBOOK.md](PILOT_PLAYBOOK.md) |
| 7.2 Launch checklist | [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md) |
| 7.2 Support process, status page | [SUPPORT.md](SUPPORT.md) |
| 7.2 Help centre | [packages/shared/src/help/](../packages/shared/src/help/README.md) |
| 7.3 Sales motion, demo script, partners · 7.4 what to build next | [SALES_PLAYBOOK.md](SALES_PLAYBOOK.md) |

## What's in the code

| ROADMAP item | Where |
|---|---|
| 7.1 Pilot metrics | `core/services/insights.value_report`: calls answered, **would have been missed**, bookings and their estimated value, messages and job requests, transfers, % handled without staff, median response time, unanswered questions, owner's latest score. Owners see it under **Results** (`GET /t/insights?days=7/30/90`) |
| "Would have been missed" | Every call record now stores `after_hours` (the business was closed when the call came in). Each business sets **Settings → Which calls reach your assistant?** (`answering_mode`). Overflow or after-hours forwarding: every answered call counts. Otherwise only calls while closed count. Test calls, busy-line refusals and unassigned numbers are never counted |
| 7.1 Owner satisfaction | **Results → How is your assistant doing?** 1–10 score, comment, and a testimonial checkbox (`POST /t/feedback` → `owner_feedback`) |
| 7.1 Pilot businesses | `tenant.pilot` (active, dates, contact, weekly call slot, case-study consent, notes), set by platform admins only (**Pilots** page, or `PATCH /platform/tenants/{id}` with `pilot`). `GET /platform/pilots`: every pilot with its last-7-days scorecard and review counts |
| 7.1 Weekly pilot report | Tenant jobs, Mondays at `REPORT_HOUR` business-local, once per ISO week (claimed atomically). Sent to the owner and to you (`PILOT_REPORT_BCC`) |
| 7.1 Review every transcript, then a sample | **Transcript review → Queue** (`GET /platform/reviews/queue`): every call from pilot businesses, plus a fixed `REVIEW_SAMPLE_PCT` sample of everyone else, chosen by call-id hash so the same call is always in or out. Open a call, read the transcript, mark it **Good** or **Something went wrong** with issue categories (prompt, tool, knowledge, latency, speech, transfer, hallucination, other) and notes (`POST /platform/reviews` → `call_reviews`) |
| 7.1 Each failure → a new eval test case | Tick **Make it an eval test case** in the review. `core/services/reviews.case_from_call` saves the caller's turns (phone numbers, e-mails, long digit runs, street numbers and the caller's name redacted), the business's facts, services and FAQs, plus your rubric and must / must-not tools, as a **draft**. Approve it under **Eval cases**, then `uv run python -m scripts.export_eval_cases` writes `tests/evals/cases/<id>.json`. `tests/evals/test_case_evals.py` replays every case against the real LLM (`RUN_EVALS=1`); file validity is checked in every CI run. Four hand-written `seed_*` cases ship with it |
| 7.2 Monitoring of margin per tenant | **Unit economics → Margin per business** (`GET /platform/margins?days=30`): prorated list price plus overage, minus call cost and the fixed per-business cost. Worst margin first; paying businesses at a loss are flagged; manual (invoiced) businesses show cost only |
| 7.2 Demo number, status page, support, help | `DEMO_PHONE_NUMBER`, `STATUS_PAGE_URL`, `SUPPORT_EMAIL`, `HELP_CENTER_URL` → `GET /public/site` for the marketing site; the dashboard Help menu shows Help centre / Contact support / Service status when set |
| 7.3 Retention: "your assistant this month" | Tenant jobs, on the 1st at `REPORT_HOUR`, once per month (claimed atomically), for the previous calendar month in business-local time. Owners can switch it off (**Settings → Notifications → Monthly results e-mail**), and `MONTHLY_REPORT_ENABLED=false` turns it off everywhere. Not sent in the month a business signed up |

**Not built, deliberately:**
- **Marketing site, per-vertical landing pages, status page itself.** They are separate from this
  server; a status page hosted here would go down with it. Setup steps: SUPPORT.md.
- **In-app chat widget.** Use Crisp or Intercom's snippet when you pick one.
- **Agency dashboard / revenue share.** Memberships already let one user hold many businesses.
  Partner payouts are handled by hand until volume justifies building them (SALES_PLAYBOOK.md).
- **Missed-call audits** of prospects. This is a sales activity, described in the playbook.

## Try it locally

1. Platform app → **Pilots → Start a pilot** → pick a business. On the client dashboard, open
   **Results**: it shows the Pilot banner.
2. Make two test calls from a softphone (docs/TELEPHONY_TESTING.md), one outside the business hours.
   **Results** shows 2 answered and 1 would-have-missed. Switch **Settings → Which calls reach your
   assistant?** to "Only calls we can't pick up" and it shows 2.
3. Give a 1–10 score on **Results**. It appears on the platform **Pilots** page.
4. Platform → **Transcript review** → open a call → **Something went wrong** → tick **Make it an
   eval test case**, fill in a title and "What it should have done" → **Save** → **Eval cases** →
   **Approve** → `uv run python -m scripts.export_eval_cases` → a new file in `tests/evals/cases/`.
5. Monthly and weekly e-mails without waiting. With no `RESEND_API_KEY` they are written to the log.
   Run one tick and pretend it's the morning of the 1st, or a Monday, in the business's timezone:

   ```bash
   uv run python -m scripts.tenant_jobs_tick --at 2026-12-01T15:00:00Z   # 09:00 in Chicago, 1 December
   uv run python -m scripts.tenant_jobs_tick --at 2026-11-23T15:00:00Z   # a Monday: pilot report
   ```

   The output counts `monthly_reports` and `pilot_reports`. Run it again and both are 0, because
   each is sent once per period.
6. **Unit economics** → **Margin per business**.

## Acceptance

| Check | How |
|---|---|
| Pilot scorecard numbers are right | `tests/test_phase7_launch.py::test_value_report_counts_what_sells` (CI); compare **Results** with the Calls list for one pilot week |
| Weekly and monthly reports go out once, on time, to the right people | `test_monthly_report_is_sent_once_on_the_first`, `test_pilot_report_goes_out_on_mondays_to_pilots_only`; on staging, check the first Monday and the first 1st |
| Every pilot call gets reviewed in weeks 1–2 | Queue is empty at the end of each week |
| Each failure became an eval case, and evals pass at 100 % | `RUN_EVALS=1 pytest tests/evals` before every release (CI's eval job) |
| Margin per business visible; no paying business at a loss | **Unit economics**; investigate any `loss` flag |
| Launch checklist all green | [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md) |
| 2–3 case studies with real numbers | PILOT_PLAYBOOK.md §6 |
