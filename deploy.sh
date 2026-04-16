#!/usr/bin/env bash
# =============================================================
# deploy.sh — VPS-এ manually deploy করার script
# =============================================================
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
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
echo "📥 [1/4] Pulling latest code from GitHub..."
git pull origin main 2>/dev/null || git pull origin master
echo "✅ Code updated"

# 2. Activate venv + install packages
echo ""
echo "📦 [2/4] Installing Python dependencies..."
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
    echo "   Created new virtualenv"
fi
source .venv/bin/activate
pip install -r requirements.txt --quiet --upgrade
echo "✅ Dependencies installed"

# 3. Restart systemd service
echo ""
echo "🔄 [3/4] Restarting service..."
sudo systemctl restart ai-receptionist
sleep 2
echo "✅ Service restarted"

# 4. Health check
echo ""
echo "🩺 [4/4] Health check..."
sleep 2
if sudo systemctl is-active --quiet ai-receptionist; then
    echo "✅ Service is RUNNING"
    echo ""
    echo "═══════════════════════════════════════"
    echo "  Deploy SUCCESS! 🚀"
    echo "═══════════════════════════════════════"
else
    echo "❌ Service FAILED to start!"
    echo ""
    sudo systemctl status ai-receptionist --no-pager
    exit 1
fi
