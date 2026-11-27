# Support process — Task 7.2

How customers reach you, how fast you answer, and what to do with each kind of problem. Technical
procedures are in [RUNBOOK.md](RUNBOOK.md); customer-facing help articles in [packages/shared/src/help/](../packages/shared/src/help/README.md).

## 1. Channels

| Channel | Setup | Who sees it |
|---|---|---|
| E-mail | `SUPPORT_EMAIL` (shared inbox, e.g. Help Scout, Front or Gmail with labels) | Dashboard sidebar "Contact support", SMS HELP reply, Terms |
| In-app chat (optional) | Crisp or Intercom snippet in the dashboard | Logged-in owners |
| Help centre | Publish [packages/shared/src/help/](../packages/shared/src/help/README.md) at `HELP_CENTER_URL` (Notion, HelpScout Docs, or a static site) | Help menu in both dashboards ("Guides" in-app; "Help centre" links out) |
| Status page | `STATUS_PAGE_URL` (Better Stack, Instatus or Atlassian Statuspage), **not** on your own servers | Dashboard sidebar "Service status", SLA, incident updates |

## 2. Targets

| Severity | Example | First response | Updates |
|---|---|---|---|
| 1 — calls not answered | Every call fails, rings out or is silent | 1 hour, any time (Pro: SLA) | Status page within 15 min, then every 30 min |
| 2 — a feature broken | Bookings fail, transfers fail, texts not sent | 4 business hours | Daily |
| 3 — question or minor issue | A wrong answer, how do I…, billing question | **< 4 business hours** (launch target) | When resolved |

Business hours: [Mon–Fri 9:00–18:00, time zone]. Out of hours, only severity 1 pages you.

## 3. Triage — the first five minutes

1. **Which business?** Find it on the platform **Businesses** page. Open it (the "open as" link):
   every action you take is in its audit log.
2. **Is it everyone?** Check the status page, Sentry, and the latency dashboard. If several businesses
   are affected, it's an incident: follow [RUNBOOK → Incident response](RUNBOOK.md#incident-response).
3. **One business?** Most common causes, in order:
   - call forwarding was turned off or changed on their phone → [help/call-forwarding.md](../packages/shared/src/help/call-forwarding.md);
   - plan paused or minutes capped → **Plan & billing** banner;
   - wrong hours or prices in Settings → fix together;
   - the assistant gave a wrong answer → open the call in **Calls & messages**, read the transcript,
     add the answer to **Knowledge**, and if it's a product bug, review it in **Transcript review** so it becomes an eval case.
4. Reply with what you found and what happens next, even if it isn't fixed yet.

## 4. Canned replies (keep in your inbox tool)

1. Forwarding check: how to test forwarding from another phone, carrier codes.
2. "It said something wrong": thanks; the fix (Knowledge entry); offer a call.
3. Change of plan / cancel: Plan & billing → Manage billing; data kept 30 days after cancelling.
4. Export my data: Plan & billing → Export all data.
5. A caller wants their data deleted: Legal & privacy → find the number → Delete ([help/caller-privacy.md](../packages/shared/src/help/caller-privacy.md)).
6. Recording: only on Pro; announced at the start of the call; retention under Assistant.
7. Number porting: we forward instead; porting is a manual process with your carrier.
8. Calendar sync not updating: reconnect Google under Settings → Integrations.
9. Spam calls: we limit concurrent calls; send us the numbers to block.
10. "Is it legal to have an AI answer my phone?": it discloses it's an AI on every call; we're not your lawyer, but here is our compliance overview (security page / DPA).

## 5. Data protection requests that reach you

- **From a caller:** reply within 2 business days: we process data for the business; we have passed
  your request to them. Forward it to the business owner with a link to [help/caller-privacy.md](../packages/shared/src/help/caller-privacy.md),
  and note the date. The business answers. Help them if asked; acting yourself needs their written instruction.
- **From a business owner about their own account:** handle it yourself (`PRIVACY_CONTACT_EMAIL`).
- **From an authority:** don't answer the substance yourself. Contact counsel the same day.

## 6. Feedback loop

Tag every ticket (forwarding, wrong answer, billing, feature request, bug). Once a month:
- the top three tags become fixes or help articles;
- feature requests go into the table in [SALES_PLAYBOOK.md §7](SALES_PLAYBOOK.md#7-what-to-build-next-task-74--by-customer-demand-not-by-guess);
- wrong-answer tickets should already be eval cases. Check that they are.
