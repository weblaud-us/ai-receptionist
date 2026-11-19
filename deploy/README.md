# Deploying with containers (staging and production)

The stack in [docker-compose.prod.yml](docker-compose.prod.yml) runs on one Docker host per
environment. Releases are git tags; GitHub Actions builds the images and runs
[deploy.sh](deploy.sh) on the host over SSH. This replaces the systemd setup in
`AI-receptionist-1/DEPLOY_GUIDE.md` (kept only for the existing VPS until it is moved).

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
REDIS_URL=redis://redis:6379/0          # or the managed Redis URL
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
