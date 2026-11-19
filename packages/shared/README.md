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
