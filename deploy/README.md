# Deploying with containers (staging and production)

The stack in [docker-compose.prod.yml](docker-compose.prod.yml) runs on one Docker host per
environment. Releases are git tags; GitHub Actions builds the images and runs
[deploy.sh](deploy.sh) on the host over SSH. This replaces the systemd setup in
`AI-receptionist-1/DEPLOY_GUIDE.md` (kept only for the existing VPS until it is moved).

For a **dev server** (one host that builds straight from a clone, no registry, no tags), see
[Dev server](#dev-server-build-from-a-clone) at the end.

## One-time host setup

On a fresh Ubuntu 24.04 host (≥ 4 vCPU / 8 GB for the first ~20 concurrent calls; in the same
region as LiveKit and Atlas):

```bash
# Docker + compose plugin
curl -fsSL https://get.docker.com | sh
sudo useradd -m -G docker deploy                 # the user GitHub Actions logs in as

# The repo, only for deploy/ (read-only deploy key)
sudo mkdir -p /opt/receptionist && sudo chown deploy /opt/receptionist
sudo -u deploy git clone git@github.com:OWNER/REPO.git /opt/receptionist

# Pull access to the images: a GitHub token with read:packages
sudo -u deploy docker login ghcr.io -u OWNER

# Settings (never in git): one app env file + one deploy file per environment
sudo mkdir -p /etc/receptionist && sudo chown root:deploy /etc/receptionist && sudo chmod 750 /etc/receptionist
```

`/etc/receptionist/production.env` (chmod 640) — every app setting, from `AI-receptionist-1/.env.example`.
Production refuses to boot without the safe values ([PRODUCTION_CHECKLIST](../docs/PRODUCTION_CHECKLIST.md) §3).
Phase 3 additions:

```
REDIS_PASSWORD=…                         # 32+ random chars; the bundled Redis requires it
REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/0   # or the managed Redis URL (with its password)
METRICS_TOKEN=…                          # 24+ random chars
LOG_FORMAT=json
LIVEKIT_NUM_IDLE_PROCESSES=2
LIVEKIT_DRAIN_TIMEOUT=900
ANTHROPIC_API_KEY=…                      # LLM fallback
```

`/etc/receptionist/production.deploy` — what the compose file needs:

```
IMAGE_PREFIX=ghcr.io/owner/receptionist
API_DOMAIN=api.example.com
APP_DOMAIN=app.example.com              # client dashboard (businesses)
ADMIN_DOMAIN=admin.example.com          # platform admin app
# SITE_DOMAIN=www.example.com            # public landing page: set on ONE environment only (see below)
WORKERS=2                                # voice worker containers, 2–4
LIVEKIT_WSS_URL=wss://your-project.livekit.cloud   # platform app CSP: dialer listen-in / takeover
# CSP_EXTRA_CONNECT_SRC=https://o123.ingest.sentry.io   # if the dashboards report to Sentry
```

`APP_DOMAIN` and `ADMIN_DOMAIN` must be subdomains of the same site as `API_DOMAIN` (e.g.
app./admin./api.example.com): both apps share the API's refresh cookie with `COOKIE_SAMESITE=lax`.
List both origins in `CORS_ORIGINS` in the env file:
`CORS_ORIGINS=https://app.example.com,https://admin.example.com`.

DNS: `API_DOMAIN`, `APP_DOMAIN` and `ADMIN_DOMAIN` → the host. Firewall: allow 80/443 (and SSH) only. Caddy gets
the certificates on first start. Put Cloudflare (proxied) in front afterwards for WAF/DDoS.

**Landing page (optional).** The public marketing site (`landing-page/`) runs on one environment
only, normally `production`. On that environment:

1. GitHub environment variables: `SITE_URL=https://www.example.com` (turns on the image build), plus
   optionally `BOOK_DEMO_URL`, `DEMO_CALL_URL`, `DEMO_PHONE_AU`, `DEMO_PHONE_UK`, `PRIVACY_URL`,
   `TERMS_URL`, `DPA_URL` and `CLAIM_ZDR` (leave it unset until both vendors have approved Zero Data
   Retention). Its signup and sign-in links use that environment's `APP_URL`.
2. The `.deploy` file: `SITE_DOMAIN=www.example.com`, and a DNS record pointing it at the host.

Environments without `SITE_DOMAIN` don't start the `landing` container, and Caddy's site block falls
back to `http://localhost`, so no certificate is requested. Regional stacks (`production-au`,
`production-uk`) normally leave it unset.

Twilio trunk → **Disaster Recovery URL**: the independent fallback below (not this host).

## Independent telephony fallback

A trunk has one Disaster Recovery URL. Twilio uses it when it can't deliver a call to LiveKit,
which includes "this whole host is down" — so it must not live on this host. Run
`fallback_app.py` on a second provider (a Render / Fly.io web service or a small second VPS),
from the same `api` image:

```
image:   ghcr.io/owner/receptionist-api:<tag>
command: uvicorn fallback_app:app --host 0.0.0.0 --port 8003 --proxy-headers
env:     TELEPHONY_FALLBACK_ONLY=true  APP_ENV=production  ENABLE_WHATSAPP=false
         WEBHOOK_BASE_URL=https://<fallback URL>   # Twilio signs the URL it calls: must be this app's
         TWILIO_AUTH_TOKEN  MONGODB_URI  MONGODB_DB_NAME  SECRET_KEY  CORS_ORIGINS  SENTRY_DSN
```

Allow its outbound IP in Atlas. Then: Twilio console → Elastic SIP Trunking → the trunk →
General → Disaster Recovery URL = `https://<fallback URL>/telephony/fallback` (POST).
It rings the business's transfer number, or tells the caller to try again. If Atlas is also down,
it can't find the business and apologises. Test it: `curl -X POST https://<fallback URL>/telephony/fallback`
must return **403** (unsigned); a real test is to stop LiveKit dispatch in staging and call.

## Releases

```bash
git tag v1.0.0-rc.1 && git push origin v1.0.0-rc.1     # staging
git tag v1.0.0 && git push origin v1.0.0               # staging, then production
```

Rollback: Actions → deploy → Run workflow with the previous tag, or on the host
`bash deploy/deploy.sh v0.9.3 production`. See [docs/RUNBOOK.md](../docs/RUNBOOK.md).

## Why two of everything

`api-a/api-b` and `worker-a/worker-b` are updated one at a time. A worker being replaced stops
taking calls and finishes its live ones (up to `LIVEKIT_DRAIN_TIMEOUT`, 15 min) while the other
answers new calls, so a deploy drops nothing.

## Capacity

- **More workers on this host:** `WORKERS=3` or `4` in `/etc/receptionist/<env>.deploy`, then
  redeploy the current tag. Size from the load test (RAM and CPU per concurrent call, Task 3.9);
  `LIVEKIT_LOAD_THRESHOLD` makes a full worker refuse calls so LiveKit routes them to another.
- **Autoscaling** needs a platform that adds workers on demand; this single-host stack can't.
  When one host is no longer enough, move the `worker` image to **LiveKit Cloud Agents**
  (managed, same network as the media, scales on active jobs) or to ECS / Cloud Run jobs / Fly
  Machines with a scale rule on CPU. The image needs no change: it only makes outbound
  connections and drains on SIGTERM. ⚠ Verify LiveKit Cloud agent-hosting limits and pricing first.

## Regional stacks (AU and UK dental practices)

Dental practices' call data is health data (UK GDPR Article 9; health information under the
Australian Privacy Act). Each country gets its own stack, so the database, the recordings and Redis
stay in that country's AWS region. See [docs/COMPLIANCE.md](../docs/COMPLIANCE.md) §7b and DPA §5.

| Stack | Environment name | `DEPLOYMENT_REGION` | Twilio | Cliniko shard | Profile |
|---|---|---|---|---|---|
| Australia | `production-au` | `ap-southeast-2` (Sydney) | `TWILIO_EDGE=sydney`, `TWILIO_REGION=au1` | `au1` / `au2` | [regions/ap-southeast-2.env.example](regions/ap-southeast-2.env.example) |
| United Kingdom | `production-uk` | `eu-west-2` (London) | `TWILIO_EDGE=dublin`, `TWILIO_REGION=ie1` | `uk1` | [regions/eu-west-2.env.example](regions/eu-west-2.env.example) |

Per stack:

1. **Host and data in the region:** an EC2 host (or equivalent) in that region; a MongoDB Atlas cluster
   in the same AWS region; an S3 bucket there (`RECORDING_S3_REGION`); a managed Redis there (or the
   bundled container).
2. **Settings:** `/etc/receptionist/production-au.env` = `AI-receptionist-1/.env.example` plus the
   region profile above. `/etc/receptionist/production-au.deploy` = the usual compose variables plus
   `DEPLOYMENT_REGION=ap-southeast-2`. `deploy.sh` refuses to run when the two disagree, or when a
   `*-au` / `*-uk` environment has the wrong region.
3. **Twilio:** create API credentials **in that Twilio region** (AU1 / IE1); route the SIP trunk
   through the same edge (`<trunk>.pstn.sydney.twilio.com` / `<trunk>.pstn.dublin.twilio.com` as
   `TWILIO_TERMINATION_URI`). ⚠ VERIFY regional availability of every Twilio product you use.
4. **LiveKit:** a LiveKit Cloud project (or self-hosted server) near the region.
5. **Domains:** e.g. `api.au.example.com` / `app.au.example.com`. Point the signup page for that
   country at that stack's API.
6. **Preflight:** run `docker compose run --rm jobs python -m scripts.check_env` on the host.
   The dental preflight must show no ❌ (encryption key, signature checks, no-training flags,
   Cliniko User-Agent) before the first practice goes live, and the ⚠ lines must be understood (ZDR
   not approved means you don't promise ZDR).
7. **Deploy:** `./deploy.sh v1.2.3 production-au`.

Speech and language models are processed where each vendor runs them (see the sub-processor list),
whatever the stack's region. The region keeps stored data local, not every API call.

## Dev server (build from a clone)

[docker-compose.dev-server.yml](docker-compose.dev-server.yml) builds every image on the host from
the checked-out code and runs one of each service behind Caddy with automatic HTTPS. MongoDB is
Atlas. It is for development and demos, not customers' calls: one voice worker, so a rebuild
waits for live calls to end before replacing it.

**1. DNS.** Point DNS at the server: A records for `laudline.weblaud.com` and `*.laudline.weblaud.com` (or `api.`, `app.` and `admin.laudline.weblaud.com`).
Open ports 80 and 443 (`sudo ufw allow 80,443/tcp`).

**2. Clone and configure.**

```bash
git clone https://github.com/weblaud-us/ai-receptionist.git receptionist && cd receptionist
cp deploy/dev-server.env.example deploy/dev-server.env       # domains + REDIS_PASSWORD
cp AI-receptionist-1/.env.example AI-receptionist-1/.env     # Atlas MONGODB_URI, LiveKit, OpenAI, …
```

The compose file sets everything that depends on the domains itself (`APP_ENV=staging`,
`REDIS_URL`, `WEBHOOK_BASE_URL`, `CALL_BASE_URL`, `DASHBOARD_URL`, `CORS_ORIGINS`,
`COOKIE_SECURE`), so those lines in `AI-receptionist-1/.env` are ignored. Allow the server's IP in
Atlas → Network Access.

**3. Start.**

```bash
./deploy/dev-server.sh                 # git pull, build, start, status (first build ~10 min)
./deploy/dev-server.sh logs api        # follow one service; no name = all
./deploy/dev-server.sh ps | restart worker | down
./deploy/dev-server.sh up --profile dialer                        # plus the outbound sales dialer
./deploy/dev-server.sh exec api python -m scripts.seed_web_demo   # demo practices for the landing page
```

To update, push to `main` and run `./deploy/dev-server.sh` on the server again. Frontend
`VITE_*` values are baked in at build time, so a change in `deploy/dev-server.env` needs that
rebuild too.

**4. Point the providers at it.** LiveKit, Twilio, Meta and Google OAuth callbacks use
`https://<API_DOMAIN>/…`, the same paths as in [docs/TELEPHONY_TESTING.md](../docs/TELEPHONY_TESTING.md).

**Host already has a reverse proxy on 80/443** (e.g. another project's nginx container)? Don't
start our Caddy; fill in the `EDGE_*` block in `deploy/dev-server.env`: the proxy's Docker network
and container, the host folder it includes `*.conf` from, and its certbot volumes. Then:

```bash
./deploy/dev-server.sh               # our services join that network as laudline-api/-app/-admin/-site
./deploy/dev-server.sh edge-setup    # adds laudline.conf to that nginx, gets the certificate, reloads
```

`edge-setup` runs `nginx -t` before each reload and removes our file again if the test fails, so
the proxy's own sites keep working. The upstreams are resolved per request, so that nginx also
still reloads while this stack is down. `./deploy/dev-server.sh edge-remove` takes our site out.
The certificate lives in the proxy's certbot volume, so its existing renewal renews ours too.
