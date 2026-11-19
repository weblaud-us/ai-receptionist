# Sales playbook — Tasks 7.3 and 7.4

How to turn pilot proof into a repeatable way of getting customers. In order of what converts best.

## 1. Demo line and inbound funnel

The demo number (`DEMO_PHONE_NUMBER`, on every page of the site, served by `GET /public/site`) is
your best asset. A prospect who talks to the AI for 60 seconds sells themselves.

- Set it up as a convincing business in your main vertical. Its knowledge base also answers "what
  is this, what does it cost, how do I get it?", and it offers to text the signup link (`send_info_link`
  with the booking link set to `<dashboard>/signup`).
- Review its transcripts every week like a pilot (**Transcript review**, filter by its business).
- Next to the number, the "Get a call from our AI" form (Phase 4, `POST /public/demo-call`) records
  consent and calls the prospect back within minutes.

## 2. Missed-call audits

Call 20 target businesses at busy times (lunch, after 5 pm, Saturday). Count the ones that don't
answer, send you to a full voicemail, or put you on hold for more than 60 seconds. Then e-mail or
visit: "I called you 4 times this week; you answered once. Here's what your callers would have
heard instead: [demo number]." Bring a one-page printout with their own numbers.

## 3. AI dialer (US business landlines, compliant)

Phase 4. It books 15-minute demos with you. The compliance gate decides who may be called; keep
the volume ramp and number reputation rules (ROADMAP Task 4.1). Never unblock a state or country
without the COMPLIANCE.md sign-off.

## 4. The 15-minute demo

| Minute | What |
|---|---|
| 0–3 | **Their missed-call cost.** "How many calls do you miss a week? What's a new customer worth?" Multiply on screen: 10 missed calls × 30 % would have booked × $150 = $450 a week |
| 3–8 | **Live call to an assistant set up for *their* business.** Before the demo: provision a trial business, run the website import for their FAQs, add their hours and 3 services. In the demo they call it from their own phone: ask a price, book, ask for a person |
| 8–12 | **Dashboard tour:** the call they just made with its transcript, the booking on the calendar, **Results**, the daily e-mail |
| 12–15 | **Trial link sent during the call.** Setup is 15 minutes; forwarding instructions for their carrier ([help/call-forwarding.md](help/call-forwarding.md)). Agree a follow-up date |

Objections, short answers:
- *"Callers hate robots."* It says it's an AI and they still book. Here's [pilot]'s 9/10, and you heard it yourself.
- *"What if it gets something wrong?"* Every call has a transcript, every booking is confirmed by
  text, and it hands over to you or takes a message when unsure. Start with overflow only.
- *"Price."* Compare with one missed booking a week, or with a part-time receptionist.
- *"My data."* There's a DPA, no training on your data, delete-on-request, and a list of every vendor.

## 5. Partners

Local marketing agencies, web designers, POS / booking-software resellers and telecom resellers who
already sell to these businesses.

- **Offer:** a recurring revenue share ([20–30] % of subscription revenue for [12–24] months) or
  wholesale pricing to resell. Put it in a written reseller agreement; the AUP bans reselling without one.
- **Agency access:** one user can be a member of many businesses (memberships); the business
  switcher in the dashboard already handles it. Give the agency an admin role on each client.
- **Payouts:** track by hand at first (spreadsheet from Stripe revenue per business). Build a
  partner dashboard only when there are more than [10] partners.
- **Middle East:** a local reseller is likely your only practical route (numbers need a local
  entity; COMPLIANCE.md §7).

## 6. Retention

- The monthly **"your assistant this month"** e-mail goes out automatically on the 1st: calls
  answered, missed calls caught, bookings and their value, and unanswered questions to fix.
  Customers who see the value every month don't churn.
- Look at **Results** for every customer once a month in the first quarter. Low call volume usually
  means forwarding broke; fix it before they notice.
- **Unit economics → Margin per business**: talk to anyone flagged before it becomes a habit
  (wrong plan, a spam storm, very long calls).

## 7. What to build next (Task 7.4) — by customer demand, not by guess

Count requests here (customer, date) and build what crosses [5] requests from paying customers.

| Idea | Requests |
|---|---|
| Warm transfer polish, call whisper ("AI call from Joe about a leak") | |
| Industry integrations (Jobber, Housecall Pro, ServiceTitan, Square, Vagaro, Mindbody, OpenTable) | |
| Takeout ordering for restaurants (menus, modifiers, POS; a separate, harder product) | |
| Payments on the call (deposits) by Stripe payment link over SMS, never card numbers by voice | |
| Spanish / Arabic / French voices per business | |
| Consented outbound for customers (ROADMAP Task 4.7) | |
| Mobile app for owners (push notifications for messages and bookings) | |
| White-label for agencies | |
