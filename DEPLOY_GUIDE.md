# 🚀 VPS Auto-Deploy Setup Guide

GitHub-এ push করলেই VPS server automatically update হবে।

---

## ধাপ ১ — VPS-এ একবার Manual Setup

### SSH দিয়ে VPS-এ login করুন
```bash
ssh ubuntu@YOUR_VPS_IP
```

### Project clone করুন (যদি না থাকে)
```bash
git clone https://github.com/ruhul-cse-duet/Hotel-AI-Receptionist-Agent-voice-and-message.git
cd AI-receptionist
```

### Python venv তৈরি করুন
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### .env file তৈরি করুন (GitHub-এ push হয় না)
```bash
cp .env.example .env
nano .env   # সব API keys দিন
```

---

## ধাপ ২ — systemd Service Setup (Server auto-start)

### Service file copy করুন
```bash
# ai-receptionist.service ফাইলে আপনার username ও path ঠিক করুন
nano ai-receptionist.service

# তারপর copy করুন
sudo cp ai-receptionist.service /etc/systemd/system/

# Enable ও start করুন
sudo systemctl daemon-reload
sudo systemctl enable ai-receptionist
sudo systemctl start ai-receptionist

# Status check
sudo systemctl status ai-receptionist
```

### Deploy script permission দিন
```bash
chmod +x deploy.sh
```

---

## ধাপ ৩ — GitHub Secrets Setup

GitHub repo → Settings → Secrets and variables → Actions → **New repository secret**

| Secret Name | Value | Example |
|---|---|---|
| `VPS_HOST` | আপনার VPS IP | `123.456.789.10` |
| `VPS_USER` | VPS username | `ubuntu` |
| `VPS_SSH_KEY` | SSH private key | (নিচে দেখুন) |
| `VPS_PORT` | SSH port | `22` |
| `APP_DIR` | Project path on VPS | `/home/ubuntu/AI-receptionist` |

### SSH Key তৈরি (যদি না থাকে)

**VPS-এ run করুন:**
```bash
ssh-keygen -t ed25519 -C "github-deploy" -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

**Private key copy করুন GitHub Secret-এ:**
```bash
cat ~/.ssh/github_deploy
# এই পুরো output কপি করে VPS_SSH_KEY secret-এ paste করুন
```

---

## ধাপ ৪ — Test করুন

**Local machine থেকে:**
```bash
git add .
git commit -m "test: auto-deploy"
git push origin main
```

**GitHub → Actions tab দেখুন** → Deploy job running দেখাবে

✅ সফল হলে VPS automatically update হয়ে যাবে!

---

## Manual Deploy (যেকোনো সময়)

VPS-এ SSH করে:
```bash
cd /home/ubuntu/AI-receptionist
./deploy.sh
```

---

## Logs দেখুন

```bash
# App logs
sudo journalctl -u ai-receptionist -f

# শেষ ৫০ লাইন
sudo journalctl -u ai-receptionist -n 50

# Error logs only
sudo journalctl -u ai-receptionist -p err
```

---

## Nginx Reverse Proxy (Recommended)

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN.com;

    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300;
    }
}
```

```bash
sudo nano /etc/nginx/sites-available/ai-receptionist
# উপরের config paste করুন

sudo ln -s /etc/nginx/sites-available/ai-receptionist /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## Troubleshooting

**Service fail করলে:**
```bash
sudo systemctl status ai-receptionist
sudo journalctl -u ai-receptionist -n 100
```

**Permission error:**
```bash
sudo chown -R ubuntu:ubuntu /home/ubuntu/AI-receptionist
chmod +x deploy.sh
```

**Git pull fail:**
```bash
git remote -v   # remote check
git fetch --all
git reset --hard origin/main
```
