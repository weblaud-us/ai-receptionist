#!/usr/bin/env bash
# =============================================================
# deploy.sh — VPS deployment script
# =============================================================

set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   AI Receptionist — Deploy Script    ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 1. Git pull
echo "📥 [1/4] Pulling latest code..."
git pull origin main
echo "✅ Code updated"

# 2. Activate venv + install packages
echo ""
echo "📦 [2/4] Installing Python dependencies..."
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
    echo "   Created new virtualenv"
fi
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt --quiet
echo "✅ Dependencies installed"

# 3. Restart PM2 processes
echo ""
echo "🔄 [3/4] Restarting PM2 processes..."
pm2 restart ecosystem.config.js
sleep 2
echo "✅ Services restarted"

# 4. Health check
echo ""
echo "🩺 [4/4] Connection Check..."
pm2 status
echo ""
echo "═══════════════════════════════════════"
echo "  Deploy SUCCESS! 🚀"
echo "  Visit: https://hotel.weblaud.com"
echo "═══════════════════════════════════════"
