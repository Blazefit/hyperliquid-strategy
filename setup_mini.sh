#!/bin/bash
# One-shot setup for Mac Mini. SSH in and run:
#   bash setup_mini.sh
#
# Safe: runs at low priority, won't touch other processes.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== Exp108 Trader Setup ==="
echo "Directory: $DIR"
echo ""

# 1. Check Python
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install with: brew install python3"
  exit 1
fi
echo "Python: $(python3 --version)"

# 2. Install uv if missing
if ! command -v uv &>/dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv: $(uv --version)"

# 3. Create venv and install deps
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  uv venv
fi
echo "Installing dependencies..."
uv sync --quiet
uv add hyperliquid-python-sdk flask --quiet 2>/dev/null || true
echo "Dependencies installed"

# 4. Create .env if missing
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "*** IMPORTANT: Edit .env with your credentials ***"
  echo "    nano $DIR/.env"
  echo ""
  echo "    Required:"
  echo "      HL_PRIVATE_KEY=0x..."
  echo "      HL_WALLET_ADDRESS=0x..."
  echo "      HL_DASH_USER=admin"
  echo "      HL_DASH_PASS=<pick a strong password>"
  echo ""
else
  echo ".env already exists"
fi

# 5. Initialize the database
.venv/bin/python -c "import db; print('Database ready:', db.DB_PATH)"

# 6. Download market data if not cached
echo "Checking market data cache..."
.venv/bin/python -c "
from prepare import download_data
download_data()
" 2>&1 | grep -v "^$"

# 7. Make ctl.sh executable
chmod +x ctl.sh

# 8. Verify everything loads
echo ""
echo "Running import check..."
.venv/bin/python -c "
import strategy, live_trader, dashboard, db
print('All modules OK')
"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Commands:"
echo "  ./ctl.sh start          # Start paper trading"
echo "  ./ctl.sh dashboard      # Start web dashboard"
echo "  ./ctl.sh status         # Check status"
echo "  ./ctl.sh health         # System resource check"
echo "  ./ctl.sh logs           # Watch live logs"
echo "  ./ctl.sh stop           # Stop trader"
echo ""
echo "After editing .env, run:"
echo "  ./ctl.sh start && ./ctl.sh dashboard"
echo ""

# Show Tailscale IP if available
TS_IP=$(tailscale ip -4 2>/dev/null)
if [ -n "$TS_IP" ]; then
  echo "Tailscale IP: $TS_IP"
  echo "Dashboard will be at: http://$TS_IP:8181"
fi
