# @receptionist/shared

UI kit (shadcn/ui on Base UI), API client, auth, sign-in pages and helpers used by both frontends:

- `dashboard/` — the client dashboard businesses use
- `platform-dashboard/` — the platform admin app

It is source-only (no build step): each app's Vite build compiles it, and each app's `tsc -b`
type-checks it. Import it by subpath:

```ts
import { Button } from '@receptionist/shared/components/ui/button'
import { api } from '@receptionist/shared/lib/api'
import { useAuth } from '@receptionist/shared/auth/auth'
```

Each app's `src/index.css` imports `@receptionist/shared/styles/globals.css` (design tokens, and an
`@source` so Tailwind scans these components).

Inside this package, import with relative paths (`../lib/utils`), never `@/…` — `@/` means the
consuming app's `src/`.

Adding a shadcn component: run `npx shadcn@latest add <name>` from this folder, then change the
`@receptionist/shared/…` imports in the new file to relative ones.

## Tutorials and help

New people and new businesses get three things, in both apps:

- **Welcome tours**: `components/tour.tsx` (engine) and `lib/tour.ts` (steps, placement). Mark an
  element `data-tour="<id>"` and point a step at it. A step whose first target isn't on the page is
  skipped (that role or business type doesn't have it); one that's there but hidden falls back to
  the next id (on a phone, `menu-button`). The page is `inert` while a tour runs. Each app keeps its
  step lists in `src/tours/` and decides when one starts by itself (`use-auto-tour.ts`).
- **Checklists**: `components/checklist-card.tsx`. The dashboard's "Getting started" and the
  platform's "Launch this business" both tick off from `core/services/getting_started.py`.
- **Guides**: `help/*.md` (public: the help centre publishes the same files), listed in `lib/help.ts`,
  read by `lib/markdown.ts` (no HTML, ever) and shown by `components/help-pages.tsx` at `/help`.
  `PageHeader help="<slug>"` adds a "Learn more" link. The platform's team guides live in
  `platform-dashboard/src/help/` and are never published.

Progress (tours finished or skipped, a hidden checklist) is saved per person on the server:
`hooks/use-tutorials.ts` → `GET/PUT/DELETE /me/tutorials`. The header's Help menu
(`components/help-menu.tsx`) replays the tour and brings a hidden checklist back.
