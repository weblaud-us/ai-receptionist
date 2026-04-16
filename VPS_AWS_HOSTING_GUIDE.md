# VPS / AWS Hosting Guide (Ubuntu + Nginx + Systemd)

This project can run on a single Ubuntu server (AWS EC2, Lightsail, or any VPS).

## 1) Server prerequisites

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git ffmpeg
```

MongoDB options:
1. Use **MongoDB Atlas** (recommended): set `MONGODB_URI` in `.env`.
2. Or install MongoDB on the server and use local URI.

## 2) Clone and prepare app

```bash
cd /opt
sudo git clone <your-repo-url> hotel-ai
sudo chown -R $USER:$USER /opt/hotel-ai
cd /opt/hotel-ai

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` for production:
1. `APP_ENV=production`
2. `DEBUG=false`
3. `WEBHOOK_BASE_URL=https://your-domain.com`
4. Set real secrets and API keys (`OPENAI_API_KEY`, `META_*`, `TWILIO_*`, `LIVEKIT_*`, `ADMIN_SECRET_KEY`, `SECRET_KEY`, `WEBHOOK_SECRET`)

Optional first-time room seed:

```bash
source .venv/bin/activate
python -m utils.seed_rooms
```

## 3) Run API with systemd

Create `/etc/systemd/system/hotel-ai-api.service`:

```ini
[Unit]
Description=Hotel AI FastAPI Service
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/hotel-ai
Environment="PATH=/opt/hotel-ai/.venv/bin"
ExecStart=/opt/hotel-ai/.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8002
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hotel-ai-api
sudo systemctl status hotel-ai-api
```

## 4) (Optional) Run LiveKit worker with systemd

If you use LiveKit voice agent, create `/etc/systemd/system/hotel-ai-livekit.service`:

```ini
[Unit]
Description=Hotel AI LiveKit Worker
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/hotel-ai
Environment="PATH=/opt/hotel-ai/.venv/bin"
ExecStart=/opt/hotel-ai/.venv/bin/python livekit_agent.py start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hotel-ai-livekit
sudo systemctl status hotel-ai-livekit
```

## 5) Nginx reverse proxy

Create `/etc/nginx/sites-available/hotel-ai`:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /admin/ws {
        proxy_pass http://127.0.0.1:8002/admin/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    location /voice/stream {
        proxy_pass http://127.0.0.1:8002/voice/stream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

Enable:

```bash
sudo ln -s /etc/nginx/sites-available/hotel-ai /etc/nginx/sites-enabled/hotel-ai
sudo nginx -t
sudo systemctl reload nginx
```

## 6) HTTPS (required for webhooks)

```bash
sudo certbot --nginx -d your-domain.com
```

## 7) Firewall / AWS Security Group

Open ports:
1. `22` (SSH)
2. `80` (HTTP)
3. `443` (HTTPS)

Do **not** expose `8002` publicly when Nginx is used.

## 8) Configure webhook URLs

Set these public URLs in Twilio / Meta:
1. Meta verify + inbound: `https://your-domain.com/webhook`
2. Twilio voice: `https://your-domain.com/voice/incoming`
3. Twilio status callback: `https://your-domain.com/voice/status`

## 9) Update deployment

```bash
cd /opt/hotel-ai
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart hotel-ai-api
sudo systemctl restart hotel-ai-livekit
```

## Notes about fixes included in this repo

1. `.env.example` now matches runtime settings used by code (`LLM_PROVIDER` values and missing production keys added).
2. `start.sh` added for Linux startup (VPS/AWS friendly).
