"""AI consensus orchestrator — Opus 4.6 + MiniMax M1 chain for risk overlay."""

import os
import sys
import json
import time
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from hl_utils import load_dotenv, close_sdk_sessions

# Load .env (setdefault — explicit env vars override .env)
load_dotenv(Path(__file__).parent / ".env", force=False)

import db

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
OVERLAY_PATH = BASE_DIR / "risk_overlay.json"
PID_FILE = BASE_DIR / "trader.pid"
LOG_FILE = BASE_DIR / "consensus.log"

# Risk overlay bounds — consensus can only reduce risk, never increase it
POSITION_SCALE_MIN = 0.25
POSITION_SCALE_MAX = 1.0
TIGHTEN_STOPS_MIN = 0.7
TIGHTEN_STOPS_MAX = 1.0

SYMBOLS = ["BTC", "ETH", "SOL"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("consensus")


# ---------------------------------------------------------------------------
# Helper: is the trader process running?
# ---------------------------------------------------------------------------
def is_trader_running():
    """Check if trader.pid exists and the process is alive."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = check existence
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------
def gather_trading_data():
    """Collect trading data from the SQLite database."""
    recent_trades = db.get_recent_trades(50)
    signal_log = db.get_signal_log(100)
    equity_history = db.get_equity_history(168)
    positions = db.get_positions()

    # Summary stats
    realized_pnl = sum(t.get("pnl", 0) for t in recent_trades)
    wins = sum(1 for t in recent_trades if t.get("pnl", 0) > 0)
    losses = sum(1 for t in recent_trades if t.get("pnl", 0) < 0)
    win_rate = wins / max(wins + losses, 1)

    # 24h PnL from equity history
    now = time.time()
    cutoff_24h = now - 86400
    recent_equity = [e for e in equity_history if e.get("timestamp", 0) >= cutoff_24h]
    if len(recent_equity) >= 2:
        pnl_24h = recent_equity[-1].get("equity", 0) - recent_equity[0].get("equity", 0)
    else:
        pnl_24h = 0.0

    # Current overlay if exists
    current_overlay = None
    if OVERLAY_PATH.exists():
        try:
            current_overlay = json.loads(OVERLAY_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Previous consensus log
    prev_consensus = db.get_consensus_log(1)
    prev_consensus = prev_consensus[0] if prev_consensus else None

    mode = db.get_state("mode", "UNKNOWN")
    equity = db.get_state("equity", 0.0)

    return {
        "recent_trades": recent_trades,
        "signal_log": signal_log,
        "equity_history": equity_history,
        "current_positions": positions,
        "realized_pnl": realized_pnl,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl_24h": pnl_24h,
        "current_overlay": current_overlay,
        "prev_consensus": prev_consensus,
        "mode": mode,
        "equity": equity,
    }


def gather_market_data():
    """Fetch live market data from Hyperliquid API."""
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
    except ImportError:
        log.warning("Hyperliquid SDK not available — skipping market data")
        return {"prices": {}, "funding_rates": {}, "account": {}}

    info = None
    try:
        info = Info(constants.MAINNET_API_URL, skip_ws=True)

        # Current prices
        all_mids = info.all_mids()
        prices = {}
        for sym in SYMBOLS:
            prices[sym] = float(all_mids.get(sym, 0))

        # Funding rates (last 7 days) via POST to /info
        import httpx
        funding_rates = {}
        now_ms = int(time.time() * 1000)
        week_ago_ms = now_ms - 7 * 86400 * 1000
        for sym in SYMBOLS:
            try:
                resp = httpx.post(
                    f"{constants.MAINNET_API_URL}/info",
                    json={"type": "fundingHistory", "coin": sym,
                          "startTime": week_ago_ms, "endTime": now_ms},
                    timeout=15,
                )
                if resp.status_code == 200:
                    funding_rates[sym] = resp.json()
            except Exception as e:
                log.warning(f"Failed to fetch funding for {sym}: {e}")

        # Account state
        wallet = os.environ.get("HL_WALLET_ADDRESS", "")
        account = {}
        if wallet:
            try:
                user_state = info.user_state(wallet)
                account = {
                    "equity": float(user_state.get("marginSummary", {}).get("accountValue", 0)),
                    "margin_used": float(user_state.get("marginSummary", {}).get("totalMarginUsed", 0)),
                    "positions": user_state.get("assetPositions", []),
                }
            except Exception as e:
                log.warning(f"Failed to fetch account state: {e}")

        return {"prices": prices, "funding_rates": funding_rates, "account": account}

    except Exception as e:
        log.error(f"Error gathering market data: {e}")
        return {"prices": {}, "funding_rates": {}, "account": {}}
    finally:
        if info:
            close_sdk_sessions(info)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def build_opus_analysis_prompt(trading_data, market_data=None):
    """Build analysis prompt for Opus 4.6."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Format recent trades
    trades_text = ""
    for t in trading_data["recent_trades"][:20]:
        ts = datetime.fromtimestamp(t.get("timestamp", 0), tz=timezone.utc).strftime("%m/%d %H:%M")
        trades_text += f"  {ts} {t.get('symbol','')} {t.get('side','')} ${t.get('size_usd',0):.0f} @ {t.get('price',0):.2f} PnL={t.get('pnl',0):.2f}\n"

    # Format signals
    signals_text = ""
    for s in trading_data["signal_log"][:20]:
        ts = datetime.fromtimestamp(s.get("timestamp", 0), tz=timezone.utc).strftime("%m/%d %H:%M")
        signals_text += f"  {ts} {s.get('symbol','')} {s.get('action','')} — {s.get('reason','')}\n"

    # Format equity
    equity_text = ""
    for e in trading_data["equity_history"][-10:]:
        ts = datetime.fromtimestamp(e.get("timestamp", 0), tz=timezone.utc).strftime("%m/%d %H:%M")
        equity_text += f"  {ts} equity=${e.get('equity',0):.2f}\n"

    # Previous consensus
    prev_text = "None"
    if trading_data.get("prev_consensus"):
        pc = trading_data["prev_consensus"]
        prev_text = (f"scale={pc.get('position_scale',1.0)}, pause={pc.get('pause_new_entries',0)}, "
                     f"tighten={pc.get('tighten_stops',1.0)}\n  Reasoning: {pc.get('reasoning','')}")

    # Market data section
    market_text = "Not available"
    if market_data and market_data.get("prices"):
        market_text = ""
        for sym, price in market_data["prices"].items():
            market_text += f"  {sym}: ${price:,.2f}\n"
        if market_data.get("account", {}).get("equity"):
            market_text += f"  Account equity: ${market_data['account']['equity']:,.2f}\n"
            market_text += f"  Margin used: ${market_data['account'].get('margin_used', 0):,.2f}\n"

    # Current overlay
    overlay_text = "None (defaults: scale=1.0, no pause, tighten=1.0)"
    if trading_data.get("current_overlay"):
        ov = trading_data["current_overlay"]
        overlay_text = json.dumps(ov, indent=2)

    prompt = f"""You are the risk oversight AI for an automated crypto trading system on Hyperliquid.

SYSTEM CONTEXT:
- Strategy: 6-signal ensemble (RSI, MACD, Bollinger, Volume, Trend, Momentum) with Sharpe 21.4 in backtests
- Symbols: BTC, ETH, SOL (perpetual futures)
- Current time: {now_str}
- Mode: {trading_data.get('mode', 'UNKNOWN')}
- Account equity: ${trading_data.get('equity', 0):.2f}

PERFORMANCE SUMMARY (last 50 trades):
- Realized PnL: ${trading_data['realized_pnl']:.2f}
- Win/Loss: {trading_data['wins']}W / {trading_data['losses']}L ({trading_data['win_rate']:.1%} win rate)
- 24h PnL: ${trading_data['pnl_24h']:.2f}

RECENT TRADES:
{trades_text if trades_text else '  No trades yet'}

RECENT SIGNALS:
{signals_text if signals_text else '  No signals yet'}

EQUITY HISTORY (last 10 snapshots):
{equity_text if equity_text else '  No history'}

CURRENT POSITIONS:
{json.dumps(trading_data.get('current_positions', []), indent=2) if trading_data.get('current_positions') else '  None'}

LIVE MARKET DATA:
{market_text}

CURRENT RISK OVERLAY:
{overlay_text}

PREVIOUS CONSENSUS:
{prev_text}

YOUR TASK:
Analyze the trading performance, market conditions, and risk posture. Return a JSON object with:

1. "position_scale" (float 0.25–1.0): Scale factor for new position sizes. 1.0 = full size, 0.25 = minimum.
   Reduce if: drawdown, high volatility, poor win rate, correlated losses.
2. "pause_new_entries" (bool): Pause all new position entries.
   True if: severe drawdown, system malfunction suspected, extreme market conditions.
3. "tighten_stops" (float 0.7–1.0): Multiplier for stop distances. 1.0 = normal, 0.7 = tightest.
   Tighten if: profit protection needed, rising volatility, trend reversal signals.
4. "reasoning" (string): 2-3 sentence explanation of your decision.
5. "market_assessment" (string): Brief assessment of current market conditions.
6. "proposals" (array): Any strategic proposals for the human operator. Each with:
   - "type": "parameter_change" | "strategy_adjustment" | "risk_alert"
   - "title": Short title
   - "reasoning": Why this is proposed
   - "suggested_changes": Object with specific changes

Return ONLY valid JSON, no markdown wrapping.
"""
    return prompt


def build_minimax_review_prompt(opus_analysis, trading_data):
    """Build challenge prompt for MiniMax M1."""
    return f"""You are a contrarian risk reviewer for a crypto trading system. Your job is to CHALLENGE the following risk analysis and find flaws, blind spots, or overly conservative/aggressive recommendations.

ORIGINAL ANALYSIS:
{opus_analysis}

CONTEXT:
- Win rate: {trading_data['win_rate']:.1%}
- 24h PnL: ${trading_data['pnl_24h']:.2f}
- Open positions: {len(trading_data.get('current_positions', []))}

YOUR TASK:
1. Identify any flaws or blind spots in the analysis
2. Challenge assumptions — is it too conservative or too aggressive?
3. Check if the position_scale and tighten_stops values are justified by the data
4. Look for risks the original analysis missed
5. Suggest specific adjustments if warranted

Be direct and specific. Focus on actionable critique, not general commentary.
Return your review as plain text (not JSON).
"""


def build_opus_final_prompt(opus_analysis, minimax_review, trading_data):
    """Build finalization prompt for Opus 4.6 second pass."""
    return f"""You previously analyzed a crypto trading system's risk posture. A contrarian reviewer has challenged your analysis. Consider their feedback and produce your FINAL risk overlay decision.

YOUR ORIGINAL ANALYSIS:
{opus_analysis}

REVIEWER CHALLENGE:
{minimax_review}

CONTEXT:
- Account equity: ${trading_data.get('equity', 0):.2f}
- Win rate: {trading_data['win_rate']:.1%}
- 24h PnL: ${trading_data['pnl_24h']:.2f}

INSTRUCTIONS:
Consider the reviewer's points carefully. Adjust your recommendations if their critique is valid, or defend your original position if you believe it was correct. Either way, explain your reasoning.

Return ONLY valid JSON with these fields:
- "position_scale" (float 0.25–1.0)
- "pause_new_entries" (bool)
- "tighten_stops" (float 0.7–1.0)
- "reasoning" (string): Final reasoning incorporating reviewer feedback
- "market_assessment" (string)
- "proposals" (array): Strategic proposals (can be empty)
  Each: {{"type": "...", "title": "...", "reasoning": "...", "suggested_changes": {{}}}}

Return ONLY valid JSON, no markdown wrapping.
"""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def parse_overlay_response(response_text):
    """Parse JSON from LLM response, handling ```json blocks. Clamp values to bounds."""
    text = response_text.strip()

    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    data = json.loads(text)

    # Clamp position_scale
    ps = float(data.get("position_scale", 1.0))
    data["position_scale"] = max(POSITION_SCALE_MIN, min(POSITION_SCALE_MAX, ps))

    # Clamp tighten_stops
    ts = float(data.get("tighten_stops", 1.0))
    data["tighten_stops"] = max(TIGHTEN_STOPS_MIN, min(TIGHTEN_STOPS_MAX, ts))

    # Ensure pause_new_entries is bool
    data["pause_new_entries"] = bool(data.get("pause_new_entries", False))

    # Ensure reasoning exists
    data.setdefault("reasoning", "No reasoning provided")
    data.setdefault("market_assessment", "")
    data.setdefault("proposals", [])

    return data


# ---------------------------------------------------------------------------
# LLM API calls
# ---------------------------------------------------------------------------
def call_opus(prompt):
    """Call Anthropic API — Opus 4.6."""
    import httpx

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()
    return result["content"][0]["text"]


def call_minimax(prompt):
    """Call MiniMax API — M1 model."""
    import httpx

    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set")

    resp = httpx.post(
        "https://api.minimax.io/v1/text/chatcompletion_v2",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "MiniMax-M1",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()
    return result["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Overlay writer
# ---------------------------------------------------------------------------
def write_overlay(overlay_data):
    """Write risk_overlay.json with the consensus decision."""
    output = {
        "position_scale": overlay_data["position_scale"],
        "pause_new_entries": overlay_data["pause_new_entries"],
        "tighten_stops": overlay_data["tighten_stops"],
        "reasoning": overlay_data["reasoning"],
        "market_assessment": overlay_data.get("market_assessment", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    OVERLAY_PATH.write_text(json.dumps(output, indent=2))
    log.info(f"Wrote overlay: scale={output['position_scale']}, "
             f"pause={output['pause_new_entries']}, tighten={output['tighten_stops']}")
    return output


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_consensus(dry_run=False):
    """Run the full consensus pipeline: Opus → MiniMax → Opus → overlay."""
    log.info("=" * 60)
    log.info("Starting consensus run%s", " (DRY RUN)" if dry_run else "")

    # Check trader is running
    if not is_trader_running():
        log.warning("Trader is not running — skipping consensus")
        return None

    # Check if consensus is enabled
    if not db.get_state("consensus_enabled", True):
        log.info("Consensus disabled via state — skipping")
        return None

    # Gather data
    log.info("Gathering trading data...")
    trading_data = gather_trading_data()

    log.info("Gathering market data...")
    market_data = gather_market_data()

    # Step 1: Opus analysis
    log.info("Calling Opus 4.6 for initial analysis...")
    analysis_prompt = build_opus_analysis_prompt(trading_data, market_data)
    opus_analysis = call_opus(analysis_prompt)
    log.info("Opus analysis received (%d chars)", len(opus_analysis))

    # Step 2: MiniMax review
    log.info("Calling MiniMax M1 for contrarian review...")
    review_prompt = build_minimax_review_prompt(opus_analysis, trading_data)
    minimax_review = call_minimax(review_prompt)
    log.info("MiniMax review received (%d chars)", len(minimax_review))

    # Step 3: Opus final decision
    log.info("Calling Opus 4.6 for final decision...")
    final_prompt = build_opus_final_prompt(opus_analysis, minimax_review, trading_data)
    opus_final = call_opus(final_prompt)
    log.info("Opus final decision received (%d chars)", len(opus_final))

    # Parse the final response
    try:
        overlay = parse_overlay_response(opus_final)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.error(f"Failed to parse Opus final response: {e}")
        log.error(f"Response was: {opus_final[:500]}")
        return None

    if dry_run:
        log.info("DRY RUN — overlay would be: %s", json.dumps(overlay, indent=2))
        return overlay

    # Write overlay
    write_overlay(overlay)

    # Log to DB
    data_snapshot = {
        "trading": {
            "realized_pnl": trading_data["realized_pnl"],
            "win_rate": trading_data["win_rate"],
            "pnl_24h": trading_data["pnl_24h"],
            "position_count": len(trading_data.get("current_positions", [])),
        },
        "market": {
            "prices": market_data.get("prices", {}),
        },
    }

    consensus_id = db.log_consensus(
        position_scale=overlay["position_scale"],
        pause_new_entries=overlay["pause_new_entries"],
        tighten_stops=overlay["tighten_stops"],
        reasoning=overlay["reasoning"],
        opus_analysis=opus_analysis,
        minimax_review=minimax_review,
        opus_final=opus_final,
        data_snapshot=data_snapshot,
    )
    log.info(f"Consensus logged (id={consensus_id})")

    # Log proposals
    for p in overlay.get("proposals", []):
        try:
            prop_id = db.log_consensus_proposal(
                proposal_type=p.get("type", "risk_alert"),
                title=p.get("title", "Untitled"),
                reasoning=p.get("reasoning", ""),
                suggested_changes=p.get("suggested_changes", {}),
            )
            log.info(f"Proposal logged (id={prop_id}): {p.get('title', '')}")
        except Exception as e:
            log.warning(f"Failed to log proposal: {e}")

    log.info("Consensus run complete")
    return overlay


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run_consensus(dry_run=dry_run)
