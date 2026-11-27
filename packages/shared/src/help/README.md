# Help centre

Articles for business owners and their teams. One source, two places:

- **In the dashboards**: Help → Guides (`/help`), and "Learn more" links on each page. The list of
  articles, their titles and who each is for is in [`../lib/help.ts`](../lib/help.ts): add a row there
  when you add a file here.
- **The external help centre**: publish this folder at `HELP_CENTER_URL` (any docs tool works).

Write for a busy owner on a phone: short steps, no jargon, AU/UK spelling ("e-mail", "centre").
Plain Markdown only: headings, paragraphs, lists, tables, quotes, `code`, **bold**, *italic* and links.
No front-matter or HTML (a test checks). Link other articles as `[text](slug.md)`.

Platform-team guides are **not** here: they're in `platform-dashboard/src/help/` and never published.

| Article | For |
|---|---|
| [Getting started](getting-started.md) | The first 15 minutes, from sign-up to the first real call |
| [Forward your calls](call-forwarding.md) | Codes for mobile carriers, landlines and VoIP phones |
| [Connect your calendar](calendar.md) | Google Calendar two-way sync |
| [Teach your assistant (Knowledge)](knowledge.md) | FAQs, unanswered questions, the website import |
| [Results and reports](results.md) | What the numbers mean, the monthly e-mail |
| [Troubleshooting](troubleshooting.md) | Calls not answered, wrong answers, texts not arriving |
| [When a caller asks about their data](caller-privacy.md) | Copy or deletion requests, opt-outs, recording |
| [The inbox](inbox.md) | Callbacks and requests that need a person |
| [Your team and their roles](team-and-roles.md) | Owner, admin, front desk and dentist; inviting people |
| [Your first day: front desk](first-day-front-desk.md) | Staff who answer the phones and call people back |
| [Your first day: practice manager](first-day-practice-manager.md) | Admins who run the assistant's settings |
| [Your first day: dentists](first-day-dentist.md) | Dentists who see their own day |
