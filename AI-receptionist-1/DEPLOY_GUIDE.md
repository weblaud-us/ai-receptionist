# Deploy guide (VPS, systemd) — legacy

> **Superseded by containers (Phase 3): see [../deploy/README.md](../deploy/README.md).** This guide is
> kept only for an existing systemd server until it is moved. The systemd API must stay at one
> worker unless `REDIS_URL` is set and `python -m jobs` runs as a third service.

Push to `main` → **CI** (lint, types, tests, dashboard build) → if green, **deploy** SSHes to the
server, checks out that exact commit and runs `AI-receptionist-1/deploy.sh`, which installs the
locked dependencies, restarts both services and waits for `/readyz`.

Server layout:

```
/opt/receptionist/                    ← clone of the monorepo (weblaud-us/ai-receptionist)
/opt/receptionist/AI-receptionist-1/  ← backend (API + LiveKit worker), .venv created by uv
/etc/receptionist/prod.env            ← secrets (NOT in git), chmod 640, owner root:receptionist
```

---

## 1. One-time server setup

```bash
# A dedicated, unprivileged user owns and runs the app
sudo useradd --system --create-home --shell /bin/bash receptionist
sudo mkdir -p /opt/receptionist /etc/receptionist
sudo chown receptionist:receptionist /opt/receptionist

# uv (Python + dependency manager) for that user
sudo -iu receptionist bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo ln -sf /home/receptionist/.local/bin/uv /usr/local/bin/uv

# The repo is private: give the server a read-only deploy key
sudo -iu receptionist ssh-keygen -t ed25519 -N "" -f ~receptionist/.ssh/id_ed25519
sudo cat ~receptionist/.ssh/id_ed25519.pub
#   → GitHub repo → Settings → Deploy keys → Add (read-only)
sudo -iu receptionist git clone git@github.com:weblaud-us/ai-receptionist.git /opt/receptionist

# Install dependencies (Python 3.12 is pinned in .python-version; uv downloads it)
sudo -iu receptionist bash -c 'cd /opt/receptionist/AI-receptionist-1 && uv sync --frozen --no-dev'
```

### Secrets

```bash
sudo cp /opt/receptionist/AI-receptionist-1/.env.example /etc/receptionist/prod.env
sudo nano /etc/receptionist/prod.env       # fill in the production values
sudo chown root:receptionist /etc/receptionist/prod.env
sudo chmod 640 /etc/receptionist/prod.env
```

Production refuses to boot with unsafe settings (`DEBUG=true`, default `SECRET_KEY`, `CORS_ORIGINS=*`,
no `META_APP_SECRET`, …) — read the error in `journalctl` and fix the file.
Once Doppler/Infisical is set up (Task 0.3), the unit files run `doppler run -- …` instead.

### Services

```bash
sudo cp /opt/receptionist/AI-receptionist-1/deploy/systemd/receptionist-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now receptionist-api receptionist-agent
```

Let the deploy user restart them without a password (and nothing else):

```bash
echo 'receptionist ALL=(root) NOPASSWD: /usr/bin/systemctl restart receptionist-api, /usr/bin/systemctl restart receptionist-agent, /usr/bin/journalctl -u receptionist-api *' \
  | sudo tee /etc/sudoers.d/receptionist-deploy
sudo chmod 440 /etc/sudoers.d/receptionist-deploy
```

### First data migration (once, after the first Phase 0 deploy)

```bash
sudo -iu receptionist bash -c 'cd /opt/receptionist/AI-receptionist-1 && set -a && . /etc/receptionist/prod.env && set +a && \
  .venv/bin/python -m migrations.m0001_room_nights'          # dry run: lists existing double bookings
# fix any CONFLICT lines from the dashboard, then:
#   … .venv/bin/python -m migrations.m0001_room_nights --apply
```

---

## 2. Nginx + TLS

```nginx
server {
    listen 443 ssl http2;
    server_name api.YOUR_DOMAIN.com;
    # certificates: sudo certbot --nginx -d api.YOUR_DOMAIN.com

    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;          # Twilio media stream WebSocket
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;   # real client IP
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }
}
server {
    listen 80;
    server_name api.YOUR_DOMAIN.com;
    return 301 https://$host$request_uri;
}
```

The API unit runs uvicorn with `--proxy-headers --forwarded-allow-ips=127.0.0.1`, so with the
`X-Forwarded-For` header above the login rate limiter sees real client IPs (before, every client
looked like `127.0.0.1`). `WEBHOOK_BASE_URL` must be exactly the public `https://` URL Twilio and
Meta call — signature checks are computed over it.

---

## 3. GitHub secrets (repo → Settings → Secrets and variables → Actions)

Create an environment called `production` (Settings → Environments) and add:

| Secret | Value |
|---|---|
| `VPS_HOST` | server IP or hostname |
| `VPS_USER` | `receptionist` |
| `VPS_SSH_KEY` | private key whose public half is in `~receptionist/.ssh/authorized_keys` |
| `VPS_PORT` | `22` |
| `APP_DIR` | `/opt/receptionist` |

---

## 4. Day-to-day

```bash
# Manual deploy of whatever is checked out
sudo -iu receptionist /opt/receptionist/AI-receptionist-1/deploy.sh

# Logs (JSON lines; phone numbers and e-mails are already masked)
sudo journalctl -u receptionist-api -f
sudo journalctl -u receptionist-agent -f

# Health
curl -s http://127.0.0.1:8002/livez     # process up
curl -s http://127.0.0.1:8002/readyz    # dependencies OK (503 if not)
```

Restarting `receptionist-agent` waits up to 10 minutes for active calls to finish — that is
intended, not a hang.
