# Front Desk Dashboard

React 19 + TypeScript + Vite + Tailwind v4 + shadcn/ui (Base UI) + TanStack Query.
Talks to the AI Receptionist backend in `../AI-receptionist-1`.

## Pages
| Route | What it does |
|---|---|
| `/` Overview | Occupancy, revenue, bookings, conversion, charts, today's arrivals/departures with one-click check-in/out, "needs attention" list |
| `/bookings` | Search, filter, sort, paginate. Detail drawer: confirm / check in / check out / no-show / cancel (with fee policy), payment status, edit guest, notes, resend WhatsApp confirmation, calendar link. **New booking** dialog (availability search → pick room → guest) |
| `/calendar` | Room × day grid. Click a booking to open it, an empty night to start a booking |
| `/rooms` | Create / edit rooms, pricing, amenities; maintenance and deactivate/reactivate |
| `/conversations` | Every call and WhatsApp chat with transcript and the tools the AI used |
| `/guests` | Guest list, profile editing, VIP tier, history, send WhatsApp |
| `/calls` | Live calls (5 s refresh), outbound call, WhatsApp message |
| `/reminders` | 1-day check-in reminder queue, failures, "Run now" |
| `/settings` | Read-only server config, integration status (no secrets), webhook URLs |

## Run
```bash
cp .env.example .env        # set VITE_API_URL
npm install
npm run dev                 # http://localhost:5173
npm run build               # type-check + production build -> dist/
npm run lint
```

## Backend setup required
In `AI-receptionist-1/.env`:
```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<choose a strong password>   # login is disabled while this is empty
SECRET_KEY=<non-default random string>      # signs the login token; login is refused if left at the default
CORS_ORIGINS=http://localhost:5173          # your dashboard origin(s)
```
Then restart the backend. All `/bookings`, `/rooms`, `/admin` and `/debug` routes require the login token.
