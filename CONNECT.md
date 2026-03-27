# Exp108 Trader — Connection & Integration Guide

## Dashboard Access

- **Primary (daneelsbrain)**: `http://100.94.160.75:8181` (Tailscale, gunicorn)
- **Fallback (Mac Mini)**: `http://100.118.109.64:8181` (Tailscale)
- **Auth**: Basic HTTP auth (credentials in `.env`, disabled if not set)

## API Endpoints

All endpoints require Basic auth header.

### GET /api/status
Quick status check: mode, equity, positions, recent trades.

### GET /api/export
Full data export for analysis. Returns JSON with:
- `account` — live equity, margin, withdrawable balance
- `performance` — total PnL, win rate, wins/losses
- `strategy` — current parameters (votes, BB threshold, position sizing)
- `positions` — open Hyperliquid positions with entry/current/PnL
- `trades` — full trade history with realized PnL per trade
- `equity_history` — hourly equity snapshots
- `signal_log` — every signal evaluation with vote counts, indicators, and reasons

### POST /api/open_position
Open a position. Form params: `coin` (BTC/ETH/SOL), `side` (long/short), `size_usd`.

### POST /api/close_position
Close a position. Form param: `coin`.

### POST /api/start / POST /api/stop
Start/stop the trader. Start accepts form param `mode` (paper/live).

### GET /health
No auth required. Returns `{"ok": true, "open_fds": N, "pid": N}`.
Use `open_fds` to monitor for connection leaks — baseline is ~13, warn at >200.

## Example: Fetch Export Data

```bash
curl -u "admin:PASSWORD" http://100.94.160.75:8181/api/export | jq .
```

## SSH Access

### daneelsbrain (primary — Ubuntu 24.04, x86_64)

```bash
ssh daneelsbrain   # configured in ~/.ssh/config
```

- **Host**: 100.94.160.75 (Tailscale)
- **User**: daneelbrain
- **Project dir**: `~/auto-researchtrading/`
- **Control script**: `./ctl.sh status|start|stop|dashboard|stop-dashboard|health|logs`
- **Dashboard**: gunicorn (worker recycling, connection-safe)

### Mac Mini (fallback)

```bash
ssh daneel   # configured in ~/.ssh/config
```

- **Host**: 100.118.109.64 (Tailscale)
- **User**: daneel
- **Project dir**: `~/auto-researchtrading/`

## Key Files

| File | Purpose |
|------|---------|
| `.env` | API keys, wallet, dashboard credentials |
| `live_trader.py` | Trading daemon (runs hourly) |
| `dashboard.py` | Flask web dashboard |
| `strategy.py` | Trading strategy (votes, BB, position sizing) |
| `db.py` | SQLite persistence layer |
| `trader.db` | Trade history, equity snapshots, signal log |
| `trader.log` | Full trader logs |
| `ctl.sh` | CLI control script |

## Strategy Parameters (current)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `MIN_VOTES` | 3/5 | Directional signals needed to enter |
| `BB_COMPRESS_PCTILE` | 85 | Bollinger Band compression gate |
| `BASE_POSITION_PCT` | 8% | Base position size as % of equity |
| `MAX_PER_SYMBOL_PCT` | 10% | Max exposure per coin |
| `MAX_TOTAL_EXPOSURE_PCT` | 25% | Max total portfolio exposure |
| Symbols | BTC, ETH, SOL | Traded assets |
| Timeframe | 1 hour | Bar interval |
