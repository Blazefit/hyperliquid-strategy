"""
Flask dashboard for the Hyperliquid trader.
Accessible over Tailscale with basic auth.
Includes start/stop and paper/live controls.
"""

import os
import sys
import json
import signal
import atexit
import functools
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, render_template, request, Response, redirect, url_for

from hl_utils import close_sdk_sessions, count_open_fds, load_dotenv, SZ_DECIMALS

# Load .env (force-set so dashboard always picks up .env values)
load_dotenv(Path(__file__).parent / ".env", force=True)

import db

# Hyperliquid live data
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

HL_WALLET = os.environ.get("HL_WALLET_ADDRESS", "")
HL_PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "")
hl_info = None
hl_exchange = None

def get_hl_info():
    global hl_info
    if hl_info is None:
        hl_info = Info(constants.MAINNET_API_URL, skip_ws=True)
    return hl_info

def get_hl_exchange():
    global hl_exchange
    if hl_exchange is None and HL_PRIVATE_KEY:
        from eth_account import Account
        wallet = Account.from_key(HL_PRIVATE_KEY)
        hl_exchange = Exchange(
            wallet=wallet,
            base_url=constants.MAINNET_API_URL,
            account_address=HL_WALLET or None,
        )
    return hl_exchange

def fetch_live_account():
    """Fetch live account state directly from Hyperliquid API."""
    if not HL_WALLET:
        return None, [], {}
    try:
        info = get_hl_info()
        state = info.user_state(HL_WALLET)
        margin = state.get("marginSummary", {})
        equity = float(margin.get("accountValue", 0))
        total_ntl = float(margin.get("totalNtlPos", 0))
        margin_used = float(margin.get("totalMarginUsed", 0))

        positions = []
        for p in state.get("assetPositions", []):
            pos = p.get("position", {})
            coin = pos.get("coin", "")
            szi = float(pos.get("szi", 0))
            entry_px = float(pos.get("entryPx", 0))
            liq_px = pos.get("liquidationPx")
            unrealized = float(pos.get("unrealizedPnl", 0))
            leverage = pos.get("leverage", {})
            if szi != 0:
                positions.append({
                    "coin": coin,
                    "side": "LONG" if szi > 0 else "SHORT",
                    "size": abs(szi),
                    "size_usd": abs(szi * entry_px),
                    "entry_price": entry_px,
                    "unrealized_pnl": unrealized,
                    "liquidation_px": float(liq_px) if liq_px else None,
                    "leverage": leverage,
                })

        # Fetch current mid prices
        mids = info.all_mids()
        for pos in positions:
            mid = float(mids.get(pos["coin"], 0))
            pos["current_price"] = mid
            if mid > 0 and pos["entry_price"] > 0:
                if pos["side"] == "LONG":
                    pos["pnl_pct"] = (mid - pos["entry_price"]) / pos["entry_price"] * 100
                else:
                    pos["pnl_pct"] = (pos["entry_price"] - mid) / pos["entry_price"] * 100

        account = {
            "equity": equity,
            "total_ntl": total_ntl,
            "margin_used": margin_used,
            "withdrawable": float(state.get("withdrawable", 0)),
        }
        return account, positions, mids
    except Exception as e:
        print(f"[WARN] Hyperliquid fetch error: {e}")
        return None, [], {}

app = Flask(__name__)
PROJECT_DIR = Path(__file__).parent
PID_FILE = PROJECT_DIR / "trader.pid"
OVERLAY_PATH = PROJECT_DIR / "risk_overlay.json"
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"

DASH_USER = os.environ.get("HL_DASH_USER", "")
DASH_PASS = os.environ.get("HL_DASH_PASS", "")


def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not DASH_USER or not DASH_PASS:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.username != DASH_USER or auth.password != DASH_PASS:
            return Response(
                "Login required.\n", 401,
                {"WWW-Authenticate": 'Basic realm="Exp108 Trader"'},
            )
        return f(*args, **kwargs)
    return decorated


def is_trader_running():
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def get_trader_pid():
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def start_trader(live=False):
    if is_trader_running():
        return False, "Trader is already running"
    env = os.environ.copy()
    if live:
        env["HYPERLIQUID_LIVE"] = "1"
    else:
        env["HYPERLIQUID_LIVE"] = "0"
    log_fh = open(PROJECT_DIR / "trader.log", "a")
    proc = subprocess.Popen(
        ["nice", "-n", "10", str(VENV_PYTHON), "live_trader.py"],
        cwd=str(PROJECT_DIR),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_fh.close()  # child process has its own fd copy
    PID_FILE.write_text(str(proc.pid))
    return True, f"Trader started (PID {proc.pid}, {'LIVE' if live else 'PAPER'})"


def stop_trader():
    pid = get_trader_pid()
    if pid is None:
        return False, "Trader is not running"
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for it to stop
        import time
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        PID_FILE.unlink(missing_ok=True)
        return True, "Trader stopped"
    except Exception as e:
        return False, f"Failed to stop: {e}"


@app.route("/")
@require_auth
def index():
    # Fetch live data from Hyperliquid
    hl_account, hl_positions, hl_mids = fetch_live_account()

    positions = db.get_positions()
    trades_raw = db.get_recent_trades(10)
    all_trades = db.get_all_trades_chronological()

    status = "running" if is_trader_running() else "stopped"
    mode = db.get_state("mode", "PAPER")
    equity = hl_account["equity"] if hl_account else db.get_state("equity", 100000.0)
    last_run = db.get_state("last_run", None)
    started_at = db.get_state("started_at", None)

    trades = []
    for t in trades_raw:
        t["time_str"] = datetime.fromtimestamp(t["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        trades.append(t)

    # Realized P&L — only from closed trades (non-zero pnl)
    closed_trades = [t for t in all_trades if t["pnl"] != 0]
    realized_pnl = sum(t["pnl"] for t in closed_trades)

    # Unrealized P&L from live Hyperliquid positions
    unrealized_pnl = sum(p.get("unrealized_pnl", 0) for p in (hl_positions or []))

    total_pnl = realized_pnl + unrealized_pnl
    total_trades = len(all_trades)

    # Win rate — ONLY closed trades with realized PnL
    wins = sum(1 for t in closed_trades if t["pnl"] > 0)
    losses = sum(1 for t in closed_trades if t["pnl"] < 0)
    win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0

    # Additional stats
    win_pnls = [t["pnl"] for t in closed_trades if t["pnl"] > 0]
    loss_pnls = [t["pnl"] for t in closed_trades if t["pnl"] < 0]
    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
    profit_factor = abs(sum(win_pnls) / sum(loss_pnls)) if loss_pnls and sum(loss_pnls) != 0 else 0.0

    # Cumulative realized PnL curve (from closed trades only — ignores deposits)
    cum_pnl = 0.0
    pnl_curve = []
    for t in all_trades:
        if t["pnl"] != 0:
            cum_pnl += t["pnl"]
            pnl_curve.append({
                "time": datetime.fromtimestamp(t["timestamp"], tz=timezone.utc).strftime("%m/%d %H:%M"),
                "pnl": round(cum_pnl, 4),
            })
    pnl_curve_json = json.dumps(pnl_curve)

    if last_run:
        try:
            last_run = datetime.fromisoformat(last_run).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass
    if started_at:
        try:
            started_at = datetime.fromisoformat(started_at).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass

    # Check if wallet is configured
    has_wallet = bool(os.environ.get("HL_PRIVATE_KEY", "").startswith("0x"))

    # Signal activity log
    signal_log_raw = db.get_signal_log(60)
    signal_log = []
    for s in signal_log_raw:
        s["time_str"] = datetime.fromtimestamp(s["timestamp"], tz=timezone.utc).strftime("%m/%d %H:%M")
        s["details"] = json.loads(s.get("details_json", "{}"))
        signal_log.append(s)

    # AI Consensus data
    consensus_log = db.get_consensus_log(limit=7)
    for entry in consensus_log:
        entry["data_snapshot"] = json.loads(entry.get("data_snapshot", "{}"))
        entry["time_str"] = datetime.fromtimestamp(entry["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pending_proposals = db.get_consensus_proposals(status="pending")
    for p in pending_proposals:
        p["suggested_changes"] = json.loads(p.get("suggested_changes", "{}"))
        p["time_str"] = datetime.fromtimestamp(p["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    current_overlay = None
    if OVERLAY_PATH.exists():
        try:
            current_overlay = json.loads(OVERLAY_PATH.read_text())
        except Exception:
            pass

    consensus_enabled = db.get_state("consensus_enabled", True)

    return render_template(
        "dashboard.html",
        positions=positions,
        trades=trades,
        pnl_curve_json=pnl_curve_json,
        status=status,
        mode=mode,
        equity=equity,
        last_run=last_run,
        started_at=started_at,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        total_pnl=total_pnl,
        total_trades=total_trades,
        closed_trades_count=len(closed_trades),
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        trader_running=is_trader_running(),
        has_wallet=has_wallet,
        signal_log=signal_log,
        hl_account=hl_account,
        hl_positions=hl_positions,
        consensus_log=consensus_log,
        pending_proposals=pending_proposals,
        current_overlay=current_overlay,
        consensus_enabled=consensus_enabled,
    )


@app.route("/api/start", methods=["POST"])
@require_auth
def api_start():
    live = request.form.get("mode", "paper") == "live"
    ok, msg = start_trader(live=live)
    return redirect(url_for("index"))


@app.route("/api/stop", methods=["POST"])
@require_auth
def api_stop():
    ok, msg = stop_trader()
    return redirect(url_for("index"))


@app.route("/api/status")
@require_auth
def api_status():
    hl_account, hl_positions, _ = fetch_live_account()
    return {
        "status": "running" if is_trader_running() else "stopped",
        "mode": db.get_state("mode", "PAPER"),
        "equity": hl_account["equity"] if hl_account else db.get_state("equity", 100000.0),
        "last_run": db.get_state("last_run"),
        "hl_account": hl_account,
        "hl_positions": hl_positions,
        "db_positions": db.get_positions(),
        "recent_trades": db.get_recent_trades(10),
    }


@app.route("/api/open_position", methods=["POST"])
@require_auth
def api_open_position():
    """Open a position on Hyperliquid."""
    ex = get_hl_exchange()
    if not ex:
        return {"ok": False, "error": "Exchange not configured (no private key)"}, 400

    coin = request.form.get("coin", "BTC")
    side = request.form.get("side", "long")
    size_usd = float(request.form.get("size_usd", "5"))

    try:
        info = get_hl_info()
        mids = info.all_mids()
        mid = float(mids.get(coin, 0))
        if mid <= 0:
            return {"ok": False, "error": f"Cannot get price for {coin}"}, 400

        decimals = SZ_DECIMALS.get(coin, 4)
        size_coins = round(size_usd / mid, decimals)
        if size_coins <= 0:
            return {"ok": False, "error": f"Position too small (${size_usd} = {size_coins} {coin})"}, 400

        is_buy = side == "long"

        result = ex.market_open(coin, is_buy, size_coins)
        status_type = result.get("status", "")
        if status_type == "ok":
            fills = result.get("response", {}).get("data", {}).get("statuses", [])
            return {"ok": True, "result": str(fills), "coin": coin, "side": side, "size_usd": size_usd, "price": mid, "size_coins": size_coins}
        else:
            return {"ok": False, "error": str(result)}, 400
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/close_position", methods=["POST"])
@require_auth
def api_close_position():
    """Close a position on Hyperliquid."""
    ex = get_hl_exchange()
    if not ex:
        return {"ok": False, "error": "Exchange not configured"}, 400

    coin = request.form.get("coin", "")
    if not coin:
        return {"ok": False, "error": "No coin specified"}, 400

    try:
        result = ex.market_close(coin)
        status_type = result.get("status", "")
        if status_type == "ok":
            return {"ok": True, "result": str(result), "coin": coin}
        else:
            return {"ok": False, "error": str(result)}, 400
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/export")
@require_auth
def api_export():
    """Full data export for external analysis systems."""
    hl_account, hl_positions, hl_mids = fetch_live_account()

    trades_raw = db.get_recent_trades(500)
    equity_hist = db.get_equity_history(5000)
    signal_log = db.get_signal_log(500)

    # Parse signal details
    for s in signal_log:
        s["details"] = json.loads(s.pop("details_json", "{}"))

    # Compute stats
    closed_pnls = [t["pnl"] for t in trades_raw if t["pnl"] != 0]
    total_realized = sum(closed_pnls)
    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in (hl_positions or []))
    wins = sum(1 for p in closed_pnls if p > 0)
    losses = sum(1 for p in closed_pnls if p < 0)

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {
            "equity": hl_account["equity"] if hl_account else db.get_state("equity", 0),
            "margin_used": hl_account["margin_used"] if hl_account else 0,
            "withdrawable": hl_account["withdrawable"] if hl_account else 0,
            "wallet": HL_WALLET[:10] + "..." if HL_WALLET else None,
        },
        "performance": {
            "total_trades": len(trades_raw),
            "closed_trades": len(closed_pnls),
            "realized_pnl": round(total_realized, 6),
            "unrealized_pnl": round(total_unrealized, 6),
            "total_pnl": round(total_realized + total_unrealized, 6),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(closed_pnls) * 100, 1) if closed_pnls else 0,
        },
        "strategy": {
            "mode": db.get_state("mode", "PAPER"),
            "last_run": db.get_state("last_run"),
            "symbols": ["BTC", "ETH", "SOL"],
            "min_votes": 3,
            "bb_threshold": 85,
            "base_position_pct": 0.08,
            "max_per_symbol_pct": 0.10,
            "max_total_exposure_pct": 0.25,
        },
        "positions": hl_positions or [],
        "trades": trades_raw,
        "equity_history": equity_hist,
        "signal_log": signal_log,
    }


@app.route("/api/consensus")
@require_auth
def api_consensus():
    """Get consensus log and current overlay."""
    consensus_log = db.get_consensus_log(limit=7)
    proposals = db.get_consensus_proposals()

    for entry in consensus_log:
        entry["data_snapshot"] = json.loads(entry.get("data_snapshot", "{}"))

    for p in proposals:
        p["suggested_changes"] = json.loads(p.get("suggested_changes", "{}"))

    overlay = None
    if OVERLAY_PATH.exists():
        try:
            overlay = json.loads(OVERLAY_PATH.read_text())
        except Exception:
            pass

    return {
        "overlay": overlay,
        "log": consensus_log,
        "proposals": [p for p in proposals if p["status"] == "pending"],
        "proposals_history": [p for p in proposals if p["status"] != "pending"],
    }


@app.route("/api/consensus/toggle", methods=["POST"])
@require_auth
def api_consensus_toggle():
    """Enable/disable AI consensus."""
    enabled = request.form.get("enabled", "true") == "true"
    db.set_state("consensus_enabled", enabled)
    return {"ok": True, "consensus_enabled": enabled}


@app.route("/api/consensus/proposal/<int:proposal_id>", methods=["POST"])
@require_auth
def api_consensus_proposal_action(proposal_id):
    """Approve or dismiss a proposal."""
    action = request.form.get("action", "")
    if action not in ("approved", "dismissed"):
        return {"ok": False, "error": "action must be 'approved' or 'dismissed'"}, 400
    db.update_consensus_proposal(proposal_id, status=action)
    return {"ok": True, "proposal_id": proposal_id, "status": action}


@app.route("/api/consensus/run", methods=["POST"])
@require_auth
def api_consensus_run():
    """Trigger consensus run manually."""
    try:
        result = subprocess.Popen(
            [sys.executable, "consensus.py"],
            cwd=str(PROJECT_DIR),
            stdout=open(PROJECT_DIR / "consensus.log", "a"),
            stderr=subprocess.STDOUT,
        )
        return {"ok": True, "pid": result.pid}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.route("/health")
def health():
    return {"ok": True, "open_fds": count_open_fds(), "pid": os.getpid()}


def _cleanup_sdk_sessions():
    """Close SDK HTTP sessions on shutdown to prevent leaked sockets."""
    global hl_info, hl_exchange
    close_sdk_sessions(hl_info, hl_exchange)
    hl_info = None
    hl_exchange = None


atexit.register(_cleanup_sdk_sessions)


if __name__ == "__main__":
    port = int(os.environ.get("HL_DASH_PORT", 8181))
    auth_status = "enabled" if (DASH_USER and DASH_PASS) else "disabled"
    print(f"Dashboard running on port {port} (auth: {auth_status})")
    print(f"  Local:     http://localhost:{port}")
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"  Tailscale: http://{result.stdout.strip()}:{port}")
    except Exception:
        pass
    app.run(host="0.0.0.0", port=port, debug=False)
