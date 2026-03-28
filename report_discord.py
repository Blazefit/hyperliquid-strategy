"""
Send trading status report to Discord.

Reads current state from trader.db + Hyperliquid API + risk_overlay.json,
formats a summary, and posts to Discord via bot token.

Usage:
    python report_discord.py              # send report
    python report_discord.py --dry-run    # print without sending

Requires .env:
    DISCORD_BOT_TOKEN=...
    DISCORD_CHANNEL_ID=...  (defaults to 1475854510806012067)
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from hl_utils import load_dotenv

load_dotenv(Path(__file__).parent / ".env", force=True)

# Also load hermes .env for Discord token
hermes_env = Path.home() / ".hermes" / ".env"
if hermes_env.exists():
    load_dotenv(hermes_env, force=False)

import db

PROJECT_DIR = Path(__file__).parent
OVERLAY_PATH = PROJECT_DIR / "risk_overlay.json"
PID_FILE = PROJECT_DIR / "trader.pid"

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL = os.environ.get("DISCORD_CHANNEL_ID", "1475854510806012067")


def is_trader_running():
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def get_overlay():
    if OVERLAY_PATH.exists():
        try:
            return json.loads(OVERLAY_PATH.read_text())
        except Exception:
            pass
    return None


def build_report():
    """Build a Discord-formatted trading status report."""
    mode = db.get_state("mode", "PAPER")
    equity = db.get_state("equity", 0)
    last_run = db.get_state("last_run", "N/A")
    trader_running = is_trader_running()
    overlay = get_overlay()

    # Recent trades (last 5)
    recent = db.get_recent_trades(5)
    all_trades = db.get_all_trades_chronological()
    closed = [t for t in all_trades if t["pnl"] != 0]
    realized_pnl = sum(t["pnl"] for t in closed)
    wins = sum(1 for t in closed if t["pnl"] > 0)
    losses = sum(1 for t in closed if t["pnl"] < 0)
    win_rate = (wins / len(closed) * 100) if closed else 0

    # Recent signals (last 3 actual trades)
    signals = db.get_signal_log(20)
    trade_signals = [s for s in signals if s["action"] in ("ENTRY_LONG", "ENTRY_SHORT", "EXIT", "ADJUST")][:3]

    # Positions
    positions = db.get_positions()

    # Overlay status
    if overlay and overlay.get("pause_new_entries"):
        status_emoji = "⏸️"
        status_text = "PAUSED"
    elif overlay and overlay.get("position_scale", 1.0) < 1.0:
        status_emoji = "⚠️"
        status_text = f"REDUCED ({overlay['position_scale']*100:.0f}% scale)"
    else:
        status_emoji = "🟢"
        status_text = "ACTIVE"

    # Build message
    lines = []
    lines.append(f"📊 **Exp19 Trading Report** — {datetime.now(timezone.utc).strftime('%b %d, %H:%M UTC')}")
    lines.append("")
    lines.append(f"{status_emoji} **Status:** {status_text} | **Mode:** {mode} | **Trader:** {'Running' if trader_running else 'Stopped'}")
    lines.append(f"💰 **Equity:** ${equity:,.2f} | **Realized P&L:** ${realized_pnl:+.4f}")
    lines.append(f"📈 **Win Rate:** {win_rate:.1f}% ({wins}W / {losses}L / {len(closed)} closed)")

    if overlay and overlay.get("reasoning"):
        reason = overlay["reasoning"][:150]
        lines.append(f"🤖 **AI Overlay:** {reason}...")

    if positions:
        lines.append("")
        lines.append("**Open Positions:**")
        for p in positions:
            lines.append(f"• {p['symbol']} {p['side'].upper()} ${p['size_usd']:.2f} @ ${p['entry_price']:.2f}")

    if trade_signals:
        lines.append("")
        lines.append("**Recent Activity:**")
        for s in trade_signals:
            ts = datetime.fromtimestamp(s["timestamp"], tz=timezone.utc).strftime("%H:%M")
            lines.append(f"• `{ts}` {s['symbol']} {s['action']} — {s['reason'][:80]}")

    if not trade_signals and not positions:
        lines.append("")
        lines.append("_No trades or positions this cycle._")

    lines.append("")
    lines.append(f"🔗 Dashboard: http://100.94.160.75:8181")

    return "\n".join(lines)


def send_to_discord(message):
    """Send message to Discord channel via bot token."""
    import urllib.request

    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set")
        return False

    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages"
    data = json.dumps({"content": message}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bot {DISCORD_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                print(f"Report sent to Discord channel {DISCORD_CHANNEL}")
                return True
            else:
                print(f"Discord API returned {resp.status}")
                return False
    except Exception as e:
        print(f"Failed to send to Discord: {e}")
        return False


if __name__ == "__main__":
    report = build_report()

    if "--dry-run" in sys.argv:
        print("=== DRY RUN ===")
        print(report)
    else:
        print(report)
        print("---")
        send_to_discord(report)
