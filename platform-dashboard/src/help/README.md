# Team guides (internal)

**Internal. Never publish this folder.** These are guides for the LaudLine team, shown only in the
platform app under Help → Guides. The practice-facing articles are in `packages/shared/src/help/`.

The platform app is a static site: its JavaScript can be downloaded without signing in. So these
guides hold **no secrets, credentials, customer names or customer data**, nothing you wouldn't put
in the app's own button labels. Anything sensitive goes in `docs/RUNBOOK.md` or the team's password
manager instead.

Add a row to `src/lib/help-internal.ts` when you add a file here. Same rules as the public guides:
plain Markdown, no front-matter or HTML, AU/UK spelling, short steps.
