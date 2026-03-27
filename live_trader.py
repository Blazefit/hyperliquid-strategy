"""
Live trader for Hyperliquid. Runs the same Strategy.on_bar() as the backtest.

Usage:
    python live_trader.py              # paper mode (default)
    HYPERLIQUID_LIVE=1 python live_trader.py   # real capital

Requires .env with:
    HL_PRIVATE_KEY=0x...
    HL_WALLET_ADDRESS=0x...
"""

import os
import sys
import time
import json
import signal
import atexit
import logging
import traceback
import resource
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from strategy import Strategy
from prepare import Signal, PortfolioState, BarData, LOOKBACK_BARS
import db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LIVE_MODE = os.environ.get("HYPERLIQUID_LIVE", "0") == "1"
PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "")
WALLET_ADDRESS = os.environ.get("HL_WALLET_ADDRESS", "")
PID_FILE = Path(__file__).parent / "trader.pid"
LOG_FILE = Path(__file__).parent / "trader.log"

SYMBOLS = ["BTC", "ETH", "SOL"]
COIN_MAP = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}
BAR_INTERVAL = "1h"

MAX_POSITION_USD = 50_000
MAX_PER_SYMBOL_PCT = 0.10
MAX_TOTAL_EXPOSURE_PCT = 0.25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("trader")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
info = None
exchange = None
strategy = None
running = True


def shutdown(signum, frame):
    global running
    log.info("Shutdown signal received, will exit after current cycle")
    running = False


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


# ---------------------------------------------------------------------------
# Hyperliquid helpers
# ---------------------------------------------------------------------------
def init_clients():
    global info, exchange
    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    if LIVE_MODE and PRIVATE_KEY:
        from eth_account import Account
        wallet = Account.from_key(PRIVATE_KEY)
        exchange = Exchange(
            wallet=wallet,
            base_url=constants.MAINNET_API_URL,
            account_address=WALLET_ADDRESS or None,
        )
        log.info("LIVE MODE: Exchange initialized with real wallet")
    else:
        exchange = None
        log.info("PAPER MODE: No orders will be sent")


def refresh_sdk_sessions():
    """Close and recreate SDK HTTP sessions to prevent stale connection buildup.
    Called every 6 hours to keep the connection pool clean."""
    global info, exchange
    # Close existing sessions
    for client in (info, exchange):
        if client and hasattr(client, "session"):
            try:
                client.session.close()
            except Exception:
                pass
    # Reinitialize
    init_clients()
    log.info("SDK sessions refreshed")


def log_connection_health():
    """Log open file descriptor count for monitoring."""
    try:
        fd_count = len(os.listdir(f"/dev/fd"))
        log.info(f"Open file descriptors: {fd_count}")
        if fd_count > 200:
            log.warning(f"HIGH FD COUNT: {fd_count} — possible connection leak")
    except Exception:
        pass


def _cleanup_on_exit():
    """Close SDK sessions on process exit."""
    for client in (info, exchange):
        if client and hasattr(client, "session"):
            try:
                client.session.close()
            except Exception:
                pass


atexit.register(_cleanup_on_exit)


def fetch_candles(symbol, lookback=LOOKBACK_BARS):
    """Fetch recent hourly candles from Hyperliquid."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - lookback * 3600 * 1000

    try:
        raw = info.candles_snapshot(COIN_MAP[symbol], BAR_INTERVAL, start_ms, end_ms)
    except Exception as e:
        log.error(f"Failed to fetch candles for {symbol}: {e}")
        return None

    if not raw:
        return None

    rows = []
    for c in raw:
        rows.append({
            "timestamp": int(c["t"]),
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c["v"]),
        })

    df = pd.DataFrame(rows).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def fetch_funding(symbol):
    """Fetch recent funding rates."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - 30 * 24 * 3600 * 1000
    try:
        body = {
            "type": "fundingHistory",
            "coin": COIN_MAP[symbol],
            "startTime": start_ms,
            "endTime": end_ms,
        }
        data = info.post("/info", body)
        if not data:
            return pd.DataFrame(columns=["timestamp", "funding_rate"])
        rows = [{"timestamp": int(r["time"]), "funding_rate": float(r["fundingRate"])} for r in data]
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame(columns=["timestamp", "funding_rate"])


def get_account_state():
    """Get current account equity and positions from Hyperliquid."""
    if not WALLET_ADDRESS:
        return 100_000.0, {}

    try:
        state = info.user_state(WALLET_ADDRESS)
        margin = state.get("marginSummary", {})
        equity = float(margin.get("accountValue", 0))
        # Paper mode with no funds: use default equity
        if equity == 0 and not LIVE_MODE:
            equity = 100_000.0

        positions = {}
        for pos in state.get("assetPositions", []):
            p = pos.get("position", {})
            coin = p.get("coin", "")
            sz = float(p.get("szi", 0))
            entry = float(p.get("entryPx", 0))
            if coin in SYMBOLS and sz != 0:
                notional = sz * entry
                positions[coin] = notional

        return equity, positions
    except Exception as e:
        log.error(f"Failed to get account state: {e}")
        return 100_000.0, {}


def get_mid_price(symbol):
    """Get current mid price."""
    try:
        mids = info.all_mids()
        return float(mids.get(COIN_MAP[symbol], 0))
    except Exception:
        return 0.0


MIN_ORDER_USD = 11  # Hyperliquid requires $10 min; use $11 for rounding buffer

def execute_order(symbol, target_usd, current_usd):
    """Execute a trade on Hyperliquid."""
    delta = target_usd - current_usd
    if abs(delta) < 1:
        return

    # Bump up to Hyperliquid minimum if opening/increasing a position
    if abs(delta) < MIN_ORDER_USD:
        if current_usd == 0:
            # New entry -- scale up to minimum
            delta = MIN_ORDER_USD if delta > 0 else -MIN_ORDER_USD
            target_usd = delta
            log.info(f"  Bumped {symbol} order to ${abs(delta)} (HL minimum)")
        else:
            log.info(f"  Skipping {symbol} order: ${abs(delta):.2f} below HL $10 minimum")
            return

    SZ_DECIMALS = {"BTC": 5, "ETH": 4, "SOL": 2}

    mid = get_mid_price(symbol)
    if mid <= 0:
        log.error(f"Cannot get price for {symbol}, skipping order")
        return

    decimals = SZ_DECIMALS.get(symbol, 4)
    size_coins = round(abs(delta) / mid, decimals)
    if size_coins <= 0:
        log.info(f"  {symbol} size rounds to 0, skipping")
        return
    is_buy = delta > 0

    log.info(
        f"ORDER: {symbol} {'BUY' if is_buy else 'SELL'} "
        f"${abs(delta):.0f} ({size_coins:.6f} coins @ ${mid:.2f})"
    )

    if LIVE_MODE and exchange:
        try:
            if target_usd != 0:
                result = exchange.market_open(COIN_MAP[symbol], is_buy, size_coins, None)
            else:
                result = exchange.market_close(COIN_MAP[symbol])

            log.info(f"Order result: {result}")

            # Check if actually filled
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            has_error = any("error" in str(s) for s in statuses)
            if result.get("status") == "ok" and not has_error:
                status = "filled"
            else:
                log.warning(f"Order not filled: {statuses}")
                status = "rejected"
        except Exception as e:
            log.error(f"Order failed: {e}")
            status = "failed"
    else:
        log.info(f"PAPER: Would {'buy' if is_buy else 'sell'} {size_coins:.6f} {symbol} @ ${mid:.2f}")
        status = "paper"

    if status in ("filled", "paper"):
        side = "buy" if is_buy else "sell"

        # Calculate P&L when closing or flipping
        pnl = 0.0
        if current_usd != 0:
            # Look up entry price from DB position
            db_positions = db.get_positions()
            for p in db_positions:
                if p["symbol"] == symbol:
                    entry_px = p["entry_price"]
                    if p["side"] == "long":
                        pnl = (mid - entry_px) / entry_px * abs(current_usd)
                    else:
                        pnl = (entry_px - mid) / entry_px * abs(current_usd)
                    break

        db.log_trade(symbol, side, abs(delta), mid, pnl=round(pnl, 4), notes=status)

        # Log the actual entry/exit to signal log
        if target_usd == 0:
            action = "EXIT"
            reason = f"Closed {symbol} @ ${mid:,.2f} | PnL: ${pnl:+.4f}"
        elif current_usd == 0:
            action = "ENTRY_LONG" if is_buy else "ENTRY_SHORT"
            reason = f"Opened {'long' if is_buy else 'short'} ${abs(delta):.0f} @ ${mid:,.2f}"
        else:
            action = "ADJUST"
            reason = f"Adjusted to ${target_usd:.0f} @ ${mid:,.2f} | PnL on close: ${pnl:+.4f}"
        db.log_signal(symbol, mid, action, reason, {"status": status, "size_coins": size_coins, "pnl": pnl})

    if target_usd == 0:
        db.clear_position(symbol)
    elif status in ("filled", "paper"):
        side_label = "long" if target_usd > 0 else "short"
        db.update_position(symbol, side_label, abs(target_usd), mid, mid, 0.0)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def build_bar_data():
    """Fetch live candles and build BarData for each symbol."""
    bar_data = {}

    for symbol in SYMBOLS:
        df = fetch_candles(symbol)
        if df is None or len(df) < 50:
            log.warning(f"{symbol}: insufficient candle data ({0 if df is None else len(df)} bars)")
            continue

        # Fetch and merge funding
        funding = fetch_funding(symbol)
        if not funding.empty:
            funding = funding.drop_duplicates("timestamp").sort_values("timestamp")
            df = pd.merge_asof(df, funding, on="timestamp", direction="backward")
        if "funding_rate" not in df.columns:
            df["funding_rate"] = 0.0
        df["funding_rate"] = df["funding_rate"].fillna(0.0)

        last = df.iloc[-1]
        bar_data[symbol] = BarData(
            symbol=symbol,
            timestamp=int(last["timestamp"]),
            open=last["open"],
            high=last["high"],
            low=last["low"],
            close=last["close"],
            volume=last["volume"],
            funding_rate=last.get("funding_rate", 0.0),
            history=df.tail(LOOKBACK_BARS).reset_index(drop=True),
        )

    return bar_data


def reconcile_hl_fills():
    """Sync recent Hyperliquid fills into our DB so P&L is always accurate."""
    if not WALLET_ADDRESS or not info:
        return
    try:
        fills = info.user_fills(WALLET_ADDRESS)
        # Only look at fills from the last 2 hours
        cutoff = (time.time() - 7200) * 1000
        recent = [f for f in fills if f.get("time", 0) > cutoff]
        if not recent:
            return

        # Get the timestamp of our last recorded trade to avoid duplicates
        last_trades = db.get_recent_trades(1)
        last_ts = last_trades[0]["timestamp"] if last_trades else 0

        for f in sorted(recent, key=lambda x: x["time"]):
            fill_ts = f["time"] / 1000
            # Skip if we already have this fill (within 1 second)
            if fill_ts <= last_ts + 1:
                continue

            coin = f.get("coin", "")
            side_hl = f.get("side", "")  # A = sell, B = buy
            sz = f.get("sz", "0")
            px = f.get("px", "0")
            closed_pnl = float(f.get("closedPnl", "0"))
            direction = f.get("dir", "")

            side = "sell" if side_hl == "A" else "buy"
            size_usd = float(sz) * float(px)

            # Determine action
            if "Close" in direction:
                action = "EXIT"
                reason = f"Closed {coin} @ ${float(px):,.2f} | PnL: ${closed_pnl:+.4f}"
                notes = "filled"
            elif "Open" in direction:
                action = "ENTRY_LONG" if "Long" in direction else "ENTRY_SHORT"
                reason = f"Opened {direction.lower()} ${size_usd:.0f} @ ${float(px):,.2f}"
                notes = "filled"
            else:
                action = "TRADE"
                reason = f"{direction} @ ${float(px):,.2f}"
                notes = "filled"

            # Log the trade
            db.log_trade(coin, side, round(size_usd, 2), float(px), pnl=round(closed_pnl, 6), notes=notes)
            db.log_signal(coin, float(px), action, reason, {"closedPnl": closed_pnl, "sz": sz, "dir": direction})

            if "Close" in direction:
                db.clear_position(coin)
                log.info(f"  Reconciled: {coin} {direction} ${size_usd:.2f} @ ${float(px)} PnL=${closed_pnl:+.6f}")
            elif "Open" in direction:
                side_label = "long" if "Long" in direction else "short"
                db.update_position(coin, side_label, round(size_usd, 2), float(px), float(px), 0.0)

    except Exception as e:
        log.warning(f"Fill reconciliation failed: {e}")


def run_cycle():
    """Run one strategy cycle."""
    log.info("=" * 60)
    log.info(f"Cycle start: {datetime.now(timezone.utc).isoformat()}")

    # Reconcile HL fills first so our DB is accurate
    reconcile_hl_fills()

    equity, live_positions = get_account_state()

    portfolio = PortfolioState(
        cash=equity - sum(abs(v) for v in live_positions.values()),
        positions=dict(live_positions),
        entry_prices={},
        equity=equity,
        timestamp=int(time.time() * 1000),
    )

    bar_data = build_bar_data()
    if not bar_data:
        log.warning("No bar data available, skipping cycle")
        return

    log.info(f"Equity: ${equity:,.2f} | Positions: {live_positions}")

    # Run strategy
    try:
        signals = strategy.on_bar(bar_data, portfolio)
    except Exception as e:
        log.error(f"Strategy error: {e}\n{traceback.format_exc()}")
        return

    # Log diagnostics for every symbol
    for symbol, diag in getattr(strategy, 'last_diagnostics', {}).items():
        votes = diag.get("votes", {})
        vote_str = " ".join(f"{k}={v}" for k, v in votes.items())
        bull_v = diag.get("bull_votes", 0)
        bear_v = diag.get("bear_votes", 0)
        pos = diag.get("current_pos", 0)

        # Determine what happened and why
        if pos == 0:
            if diag.get("bullish"):
                action = "SIGNAL_LONG"
                reason = f"{bull_v}/5 bull votes + BB OK"
            elif diag.get("bearish"):
                action = "SIGNAL_SHORT"
                reason = f"{bear_v}/5 bear votes + BB OK"
            elif diag.get("in_cooldown"):
                action = "NO_ENTRY"
                reason = "In cooldown"
            elif not diag.get("bb_ok"):
                best = max(bull_v, bear_v)
                direction = "bull" if bull_v >= bear_v else "bear"
                reason = f"BB not compressed ({diag.get('bb_pctile', 0):.0f}th pctile >= 85)"
                if best >= 4:
                    reason = f"{best}/5 {direction} votes BUT " + reason
                else:
                    reason = f"Only {best}/5 {direction} votes AND " + reason
                action = "NO_ENTRY"
            elif not diag.get("btc_confirm"):
                action = "NO_ENTRY"
                reason = f"BTC opposing (mom={diag.get('btc_momentum', 0):.1f}%)"
            else:
                best = max(bull_v, bear_v)
                action = "NO_ENTRY"
                reason = f"Only {best}/5 votes (need 3)"
        else:
            action = "HOLDING"
            side = "LONG" if pos > 0 else "SHORT"
            reason = f"{side} ${abs(pos):.0f}"

        log.info(f"  {symbol} ${diag.get('price', 0):,.2f} | {action} | {reason} | {vote_str} | BB={diag.get('bb_pctile', 0):.0f} RSI={diag.get('rsi', 0):.0f}")

        db.log_signal(
            symbol=symbol,
            price=diag.get("price", 0),
            action=action,
            reason=reason,
            details=diag,
        )

    if not signals:
        log.info("No signals this bar")
    else:
        for sig in signals:
            current = live_positions.get(sig.symbol, 0.0)
            log.info(f"Signal: {sig.symbol} target=${sig.target_position:.0f} (current=${current:.0f})")
            execute_order(sig.symbol, sig.target_position, current)

    # Update equity snapshot and refresh DB positions with current prices
    positions_dict = {}
    for symbol in SYMBOLS:
        mid = get_mid_price(symbol)
        pos = live_positions.get(symbol, 0.0)
        if pos != 0 and mid > 0:
            positions_dict[symbol] = {"notional": pos, "price": mid}

        # Update DB positions with live prices and unrealized P&L
        db_positions = db.get_positions()
        for p in db_positions:
            if p["symbol"] == symbol and mid > 0:
                entry_px = p["entry_price"]
                if p["side"] == "long":
                    unrealized = (mid - entry_px) / entry_px * p["size_usd"]
                else:
                    unrealized = (entry_px - mid) / entry_px * p["size_usd"]
                db.update_position(symbol, p["side"], p["size_usd"], entry_px, mid, round(unrealized, 4))

    db.log_equity(equity, portfolio.cash, positions_dict)
    db.set_state("last_run", datetime.now(timezone.utc).isoformat())
    db.set_state("mode", "LIVE" if LIVE_MODE else "PAPER")
    db.set_state("equity", equity)

    log.info(f"Cycle complete. Next run in ~1h")


def wait_for_next_bar():
    """Sleep until the next hour boundary + 30s buffer."""
    now = datetime.now(timezone.utc)
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=30, microsecond=0)
    sleep_secs = (next_hour - now).total_seconds()
    if sleep_secs < 0:
        sleep_secs += 3600

    log.info(f"Sleeping {sleep_secs:.0f}s until {next_hour.isoformat()}")

    # Sleep in small increments so we can respond to shutdown signals
    end_time = time.time() + sleep_secs
    while running and time.time() < end_time:
        time.sleep(min(10, end_time - time.time()))


def check_system_resources():
    """Check that we won't interfere with other processes on this machine."""
    import subprocess

    # Set ourselves to low priority so we never starve other apps
    try:
        os.nice(10)
        log.info("Process priority lowered (nice +10)")
    except OSError:
        pass

    # Limit our own memory to 1GB to prevent runaway usage
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_RSS)
        mem_limit = 1024 * 1024 * 1024  # 1 GB
        resource.setrlimit(resource.RLIMIT_RSS, (mem_limit, hard))
        log.info("Memory limit set to 1GB")
    except (ValueError, OSError):
        pass  # Not all systems support RLIMIT_RSS

    # Check available memory (free + inactive + purgeable = truly available on macOS)
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
        )
        total_mem = int(result.stdout.strip())

        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        page_size = 16384
        pages = {}
        for line in lines:
            if "page size of" in line:
                page_size = int(line.split("of")[-1].strip().split()[0])
            for key in ["Pages free", "Pages inactive", "Pages purgeable", "Pages speculative"]:
                if key in line:
                    pages[key] = int(line.split(":")[1].strip().rstrip("."))

        avail_pages = sum(pages.get(k, 0) for k in ["Pages free", "Pages inactive", "Pages purgeable", "Pages speculative"])
        avail_mem = avail_pages * page_size
        avail_pct = avail_mem / total_mem * 100
        log.info(f"System memory: {total_mem / 1e9:.1f}GB total, ~{avail_mem / 1e9:.1f}GB available ({avail_pct:.0f}%)")

        if avail_pct < 10:
            log.warning("LOW MEMORY: <10% available -- trader will still run at low priority (nice +10)")
    except Exception as e:
        log.info(f"Could not check memory: {e}")

    # Check CPU load
    try:
        result = subprocess.run(
            ["sysctl", "-n", "vm.loadavg"], capture_output=True, text=True, timeout=5
        )
        load_str = result.stdout.strip().strip("{}").split()
        load_1m = float(load_str[0])
        ncpu = os.cpu_count() or 1
        log.info(f"CPU load: {load_1m:.2f} (1m avg), {ncpu} cores")

        if load_1m > ncpu * 0.9:
            log.warning(f"HIGH CPU LOAD: {load_1m:.1f} across {ncpu} cores -- trader runs at low priority")
    except Exception as e:
        log.info(f"Could not check CPU load: {e}")

    # Check disk space
    try:
        stat = os.statvfs(str(Path(__file__).parent))
        free_gb = (stat.f_bavail * stat.f_frsize) / 1e9
        log.info(f"Disk space: {free_gb:.1f}GB free")
        if free_gb < 1:
            log.warning("LOW DISK: <1GB free")
    except Exception:
        pass


def main():
    global strategy

    log.info("=" * 60)
    log.info(f"Trader starting ({'LIVE' if LIVE_MODE else 'PAPER'} mode)")
    log.info(f"Wallet: {WALLET_ADDRESS[:10]}..." if WALLET_ADDRESS else "Wallet: not set")
    log.info("=" * 60)

    check_system_resources()

    # Write PID file
    PID_FILE.write_text(str(os.getpid()))

    init_clients()
    strategy = Strategy()
    db.set_state("status", "running")
    db.set_state("started_at", datetime.now(timezone.utc).isoformat())
    db.set_state("mode", "LIVE" if LIVE_MODE else "PAPER")

    try:
        # Run immediately on start, then wait for bar boundaries
        cycle_count = 0
        run_cycle()
        cycle_count += 1
        while running:
            wait_for_next_bar()
            if running:
                # Refresh SDK sessions every 6 hours to prevent stale connections
                if cycle_count > 0 and cycle_count % 6 == 0:
                    refresh_sdk_sessions()
                log_connection_health()
                run_cycle()
                cycle_count += 1
    except Exception as e:
        log.error(f"Fatal error: {e}\n{traceback.format_exc()}")
    finally:
        db.set_state("status", "stopped")
        db.set_state("stopped_at", datetime.now(timezone.utc).isoformat())
        PID_FILE.unlink(missing_ok=True)
        log.info("Trader stopped")


if __name__ == "__main__":
    main()
