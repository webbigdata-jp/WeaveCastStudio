#!/bin/bash
# =============================================================================
# WeaveCastStudio GCE Startup Script
#
# GCEインスタンスの初回起動時に自動実行される。
# - システムパッケージのインストール
# - uv (Python パッケージマネージャ) のインストール
# - リポジトリのクローンは手動（Private リポジトリのため認証が必要）
#
# ログ確認: sudo journalctl -u google-startup-scripts.service -f
# =============================================================================

set -euo pipefail

LOG_FILE="/var/log/weavecast-startup.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=========================================="
echo " WeaveCastStudio GCE Setup"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# --- System packages ---
echo "[1/4] Installing system packages..."
apt-get update -y
apt-get install -y \
  chromium-browser \
  fonts-noto-cjk \
  xvfb \
  git \
  curl \
  sqlite3 \
  jq

echo "  Chromium path: $(which chromium-browser || echo 'NOT FOUND')"

# --- uv (for the default user) ---
echo "[2/4] Installing uv..."
DEFAULT_USER=$(getent passwd 1000 | cut -d: -f1 || echo "")
if [ -z "$DEFAULT_USER" ]; then
  echo "  Warning: No UID 1000 user found. uv will be installed for root."
  curl -LsSf https://astral.sh/uv/install.sh | sh
else
  echo "  Installing uv for user: $DEFAULT_USER"
  sudo -u "$DEFAULT_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

# --- Working directories ---
echo "[3/4] Creating working directories..."
if [ -n "$DEFAULT_USER" ]; then
  HOME_DIR="/home/$DEFAULT_USER"
  sudo -u "$DEFAULT_USER" mkdir -p "$HOME_DIR/WeaveCastStudio/logs"
fi

# --- Summary ---
echo "[4/4] Setup complete!"
echo ""
echo "  Next steps (manual):"
echo "  1. SSH into the instance"
echo "  2. Clone the repository:"
echo "     git clone https://<TOKEN>@github.com/webbigdata-jp/WeaveCastStudio.git"
echo "  3. cd WeaveCastStudio && uv sync"
echo "  4. Set up .env files"
echo "  5. Run tests"
echo "  6. Set up cron jobs"
echo ""
echo "=========================================="
echo " Startup script finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
