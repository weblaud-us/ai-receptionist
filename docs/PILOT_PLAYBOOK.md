# Pilot playbook — Task 7.1

**Goal:** prove the assistant on 3–5 real businesses and leave with numbers, testimonials and an
eval suite built from real calls. **Start** as soon as Phase 1 and a minimal Phase 2 work. You
don't need billing or self-serve signup for this.

Product side (what each screen does): [PHASE_7_TESTING.md](PHASE_7_TESTING.md).

## 1. Pick the businesses

- **3–5 businesses** in your two launch verticals (ROADMAP Task 2.1), ideally ones you can visit.
- They get a lot of calls and miss some: one person running the shop, a busy front desk, trades out on jobs.
- The owner answers e-mail and will spend 15 minutes a week on a call.
- Not US healthcare (no BAAs yet). Not a business whose calls are mostly emergencies.

## 2. The offer (one page, signed)

> Free for [30–60] days. In return: a 15-minute feedback call every week, permission for us to read
> call transcripts to improve the assistant, and — if it works for you — a testimonial or a short
> case study with your numbers. Either side can stop at any time. Your callers' data is handled under
> our DPA; you can export or delete it whenever you like.

They also accept the Terms and DPA (Legal & privacy page). Ask about the testimonial up front; it's
much harder to ask at the end. Tick **case study OK** on the pilot once they agree.

## 3. Set them up (about an hour, with them)

1. Platform app → **Businesses → Provision**. Leave the plan as **manual**, so the business is never
   billed and never capped. Send the owner the set-password link.
2. **Pilots → Start a pilot**: contact name, weekly call slot, start and end dates.
3. With the owner on a screen share: the onboarding wizard (hours, services and prices, FAQs from
   their website, transfer number, voice). Use the website import for FAQs, then fix them together.
4. Start with **overflow only**. On their phone, forward busy / no-answer / unreachable to the
   assistant's number ([help/call-forwarding.md](help/call-forwarding.md)), and set **Settings →
   Which calls reach your assistant?** to "Only calls we can't pick up". This is lower risk for them,
   and every call it answers is one they'd have missed.
5. Make three test calls together: book something, ask a question it can't answer, ask for a person.
6. Put their daily-digest e-mail on, and agree the weekly call time.

## 4. Every week

| When | What |
|---|---|
| Daily, weeks 1–2 | Open **Transcript review**, which lists every pilot call. Read all of them. Mark each one **Good** or **Something went wrong** |
| From week 3 | Read failed outcomes, transfers and anything over 3 minutes, plus a sample |
| For each failure | Fix it the same day: **prompt** (base or vertical rules), **tool** (code), **knowledge** (add the FAQ to the business), **settings** (hours, prices). Then tick **Make it an eval test case**, approve it, export (`python -m scripts.export_eval_cases`), and commit the file |
| Monday morning | The weekly report goes out by itself (you're in copy via `PILOT_REPORT_BCC`). Look at **Pilots** for anything red |
| Weekly call (15 min) | Go through the report, ask for their 1–10 score (or have them click it under **Results**), take notes, agree one improvement |
| Before each release | `RUN_EVALS=1 pytest tests/evals`. 100 % must pass |

Keep a short log per pilot: date, what went wrong, what you changed. It becomes the case study.

## 5. What to measure (and share every week)

| Metric | Where | Why it sells |
|---|---|---|
| Calls answered that would have been missed | **Results** / weekly report | The headline ROI number |
| Bookings and job requests captured, and their value | **Results** | Revenue attributable to you |
| % of calls fully handled without a human | **Results** | Time saved |
| Transfers and messages | **Results**, Calls & messages | Proof nothing fell through |
| Typical response time (p50) | **Results** | Proves it "sounds human" |
| Owner satisfaction (1–10) | **Results → How is your assistant doing?** | Testimonial material |

## 6. Ending the pilot

- **Week before the end:** show the totals. Offer the plan that fits their minutes, with the first
  month at [50 %] off if they convert before the end date. Use Plan & billing; their data and
  number stay.
- **Converted:** end the pilot in **Pilots** (their data stays) and switch them to a self-serve plan.
- **Not converted:** ask why. Write it down: it's the most useful feedback you'll get. End the pilot,
  release the number, and their data is deleted on the normal schedule.
- **Case study** (with written approval of the final text): the business, the problem, setup time,
  4 weeks of numbers from **Results**, one quote, a photo. You want 2–3 before launch.

## 7. Exit criteria for the pilot phase

- [ ] At least 3 businesses ran ≥ 4 weeks.
- [ ] Median owner score ≥ 8/10; no unresolved "it embarrassed us" incident.
- [ ] Every failure found in review has a fix and an eval case; evals pass at 100 %.
- [ ] Median response time ≤ 1.0 s on real calls (Phase 1 target).
- [ ] 2–3 case studies approved for publication.
- [ ] At least 1 pilot converted to paid.
