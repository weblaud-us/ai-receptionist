# Platform Admin

The platform operator's app: every business on the platform, phone-number routing, unit
economics and the outbound sales dialer. Platform administrators only (with two-factor on),
served on its own origin (e.g. `admin.example.com`), separate from the client dashboard
(`../dashboard`) that businesses use.

| Route | What it does |
|---|---|
| `/` Businesses | Every business: status, numbers, 30-day calls/minutes, concurrent-call limit. Provision a business + owner, assign/release numbers. **Open** hands the business to the client dashboard (`/impersonate/<id>`, new tab), acting as its owner; every change is audited |
| `/economics` | Cost per minute, margin per plan, last 30 days' cost |
| `/dialer` | Outbound sales dialer: campaigns, leads, live calls (listen in / take over), results, compliance |

Shares its UI kit, API client, auth and sign-in pages with the client dashboard through
`@receptionist/shared` (`../packages/shared`).

## Run
From the repo root (npm workspaces):
```bash
cp platform-dashboard/.env.example platform-dashboard/.env
npm install
npm run dev:platform        # http://localhost:5174
```

The backend must allow this origin: `CORS_ORIGINS=http://localhost:5173,http://localhost:5174`.
Signing in here and in the client dashboard is one session: the refresh cookie belongs to the API.

## Deploy
`docker build -f platform-dashboard/Dockerfile .` from the repo root (see the Dockerfile header);
`deploy/` runs it behind Caddy on `ADMIN_DOMAIN`. Add the LiveKit `wss://` URL to its
`CSP_CONNECT_SRC` (`LIVEKIT_WSS_URL` in the `.deploy` file) for the dialer's listen-in.
