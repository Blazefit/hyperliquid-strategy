#!/bin/bash
# Control script for the Hyperliquid trader
# Usage: ./ctl.sh start|stop|status|dashboard|stop-dashboard|health|logs

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/trader.pid"
DASH_PID_FILE="$DIR/dashboard.pid"
VENV="$DIR/.venv/bin/python"
DASH_PORT="${HL_DASH_PORT:-8181}"

check_port() {
  local port=$1
  local name=$2
  if lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    local blocking_pid=$(lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
    local blocking_name=$(ps -p "$blocking_pid" -o comm= 2>/dev/null)
    echo "ERROR: Port $port already in use by $blocking_name (PID $blocking_pid)"
    echo "  Either stop that process or set HL_DASH_PORT to a different port"
    return 1
  fi
  return 0
}

check_stale_pid() {
  local pidfile=$1
  local name=$2
  if [ -f "$pidfile" ]; then
    local pid=$(cat "$pidfile")
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Cleaning up stale $name PID file (process $pid no longer running)"
      rm -f "$pidfile"
    fi
  fi
}

case "$1" in
  start)
    check_stale_pid "$PID_FILE" "trader"

    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Trader already running (PID $(cat "$PID_FILE"))"
      exit 1
    fi

    echo "=== Pre-flight checks ==="

    # Check system resources
    MEM_FREE=$(vm_stat 2>/dev/null | awk '/Pages free/ {gsub(/\./,"",$3); print $3}')
    if [ -n "$MEM_FREE" ] && [ "$MEM_FREE" -lt 10000 ]; then
      echo "  WARNING: Low free memory (~$((MEM_FREE * 16 / 1024))MB) -- trader will run at low priority"
    else
      echo "  Memory: OK"
    fi

    LOAD=$(sysctl -n vm.loadavg 2>/dev/null | awk '{gsub(/[{}]/,""); print $1}')
    NCPU=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
    echo "  CPU load: $LOAD (${NCPU} cores)"

    DISK_FREE=$(df -g "$DIR" 2>/dev/null | tail -1 | awk '{print $4}')
    if [ -n "$DISK_FREE" ] && [ "$DISK_FREE" -lt 1 ]; then
      echo "  WARNING: Low disk space (${DISK_FREE}GB free)"
    else
      echo "  Disk: ${DISK_FREE}GB free -- OK"
    fi

    echo ""
    echo "Starting trader in ${HYPERLIQUID_LIVE:+LIVE}${HYPERLIQUID_LIVE:-PAPER} mode..."
    echo "  Process will run at nice +10 (low priority, won't interfere with other apps)"
    cd "$DIR"
    nohup nice -n 10 "$VENV" live_trader.py >> "$DIR/trader.log" 2>&1 &
    echo $! > "$PID_FILE"
    echo "Trader started (PID $!)"
    echo "Logs: tail -f $DIR/trader.log"
    ;;

  stop)
    check_stale_pid "$PID_FILE" "trader"

    if [ ! -f "$PID_FILE" ]; then
      echo "Trader not running (no PID file)"
      exit 1
    fi

    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Stopping trader (PID $PID)..."
      kill -TERM "$PID"
      for i in $(seq 1 30); do
        if ! kill -0 "$PID" 2>/dev/null; then
          break
        fi
        sleep 1
      done
      if kill -0 "$PID" 2>/dev/null; then
        echo "Force killing..."
        kill -9 "$PID"
      fi
      rm -f "$PID_FILE"
      echo "Trader stopped"
    else
      echo "Trader not running (stale PID file)"
      rm -f "$PID_FILE"
    fi
    ;;

  status)
    echo "=== Trader Status ==="
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      PID=$(cat "$PID_FILE")
      echo "  Status:  RUNNING (PID $PID)"
      # Show resource usage of the trader process
      RSS=$(ps -p "$PID" -o rss= 2>/dev/null)
      CPU=$(ps -p "$PID" -o %cpu= 2>/dev/null)
      if [ -n "$RSS" ]; then
        echo "  Memory:  $((RSS / 1024))MB RSS"
      fi
      if [ -n "$CPU" ]; then
        echo "  CPU:     ${CPU}%"
      fi
    else
      echo "  Status:  STOPPED"
    fi

    if [ -f "$DASH_PID_FILE" ] && kill -0 "$(cat "$DASH_PID_FILE")" 2>/dev/null; then
      echo "  Dashboard: RUNNING on port $DASH_PORT (PID $(cat "$DASH_PID_FILE"))"
    else
      echo "  Dashboard: STOPPED"
    fi

    "$VENV" -c "
import db, json
from datetime import datetime, timezone
status = db.get_state('status', 'unknown')
mode = db.get_state('mode', 'PAPER')
equity = db.get_state('equity', 0)
last_run = db.get_state('last_run', 'never')
print(f'  Mode:    {mode}')
print(f'  Equity:  \${equity:,.2f}' if isinstance(equity, (int, float)) else f'  Equity:  {equity}')
print(f'  Last run: {last_run}')
positions = db.get_positions()
if positions:
    print(f'  Positions:')
    for p in positions:
        print(f'    {p[\"symbol\"]}: {p[\"side\"]} \${p[\"size_usd\"]:.0f} (entry \${p[\"entry_price\"]:.2f})')
else:
    print(f'  Positions: none')
trades = db.get_recent_trades(5)
if trades:
    print(f'  Recent trades:')
    for t in trades:
        ts = datetime.fromtimestamp(t['timestamp'], tz=timezone.utc).strftime('%m/%d %H:%M')
        print(f'    {ts} {t[\"side\"]:4s} {t[\"symbol\"]} \${t[\"size_usd\"]:.0f} @ \${t[\"price\"]:.2f}  {t[\"notes\"]}')
" 2>/dev/null
    ;;

  dashboard)
    check_stale_pid "$DASH_PID_FILE" "dashboard"

    if [ -f "$DASH_PID_FILE" ] && kill -0 "$(cat "$DASH_PID_FILE")" 2>/dev/null; then
      echo "Dashboard already running (PID $(cat "$DASH_PID_FILE"))"
      echo "http://localhost:$DASH_PORT"
      exit 0
    fi

    # Check port availability
    if ! check_port "$DASH_PORT" "dashboard"; then
      exit 1
    fi

    echo "Starting dashboard on port $DASH_PORT (gunicorn) ..."
    echo "  Local:     http://localhost:$DASH_PORT"

    # Detect Tailscale IP
    TS_IP=$(tailscale ip -4 2>/dev/null)
    if [ -n "$TS_IP" ]; then
      echo "  Tailscale: http://$TS_IP:$DASH_PORT"
    fi

    # Show auth status
    if [ -n "$HL_DASH_USER" ] && [ -n "$HL_DASH_PASS" ]; then
      echo "  Auth:      enabled (user: $HL_DASH_USER)"
    else
      echo "  Auth:      disabled (set HL_DASH_USER and HL_DASH_PASS in .env for remote access)"
    fi

    cd "$DIR"
    GUNICORN="$DIR/.venv/bin/gunicorn"
    if [ -x "$GUNICORN" ]; then
      # Production: gunicorn with connection-safe config
      nohup nice -n 10 "$GUNICORN" -c gunicorn.conf.py dashboard:app >> "$DIR/dashboard.log" 2>&1 &
      echo $! > "$DASH_PID_FILE"
      echo "Dashboard started via gunicorn (PID $!)"
      echo "  Worker recycling: every 1000 requests"
      echo "  Keep-alive: 5s idle timeout"
    else
      # Fallback: Flask dev server (install gunicorn with: uv add gunicorn)
      echo "  WARNING: gunicorn not found, falling back to Flask dev server"
      echo "  Install with: uv add gunicorn"
      nohup nice -n 10 "$VENV" dashboard.py >> "$DIR/dashboard.log" 2>&1 &
      echo $! > "$DASH_PID_FILE"
      echo "Dashboard started via Flask dev server (PID $!)"
    fi
    ;;

  stop-dashboard)
    if [ -f "$DASH_PID_FILE" ]; then
      PID=$(cat "$DASH_PID_FILE")
      kill "$PID" 2>/dev/null
      rm -f "$DASH_PID_FILE"
      echo "Dashboard stopped"
    else
      echo "Dashboard not running"
    fi
    ;;

  health)
    echo "=== System Health ==="

    # CPU
    LOAD=$(sysctl -n vm.loadavg 2>/dev/null | awk '{gsub(/[{}]/,""); print $1}')
    NCPU=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
    echo "  CPU:     $LOAD load avg / $NCPU cores"

    # Memory
    TOTAL_MEM=$(sysctl -n hw.memsize 2>/dev/null)
    if [ -n "$TOTAL_MEM" ]; then
      TOTAL_GB=$((TOTAL_MEM / 1073741824))
      echo "  Memory:  ${TOTAL_GB}GB total"
    fi

    # Disk
    DISK_FREE=$(df -g "$DIR" 2>/dev/null | tail -1 | awk '{print $4}')
    echo "  Disk:    ${DISK_FREE}GB free"

    # Tailscale
    TS_STATUS=$(tailscale status 2>/dev/null | head -1)
    TS_IP=$(tailscale ip -4 2>/dev/null)
    if [ -n "$TS_IP" ]; then
      echo "  Tailscale: $TS_IP (connected)"
    else
      echo "  Tailscale: not connected"
    fi

    # Trader process
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      PID=$(cat "$PID_FILE")
      RSS=$(ps -p "$PID" -o rss= 2>/dev/null)
      CPU=$(ps -p "$PID" -o %cpu= 2>/dev/null)
      echo "  Trader:  PID $PID, ${CPU}% CPU, $((RSS / 1024))MB RAM"
    else
      echo "  Trader:  not running"
    fi

    # Dashboard
    if [ -f "$DASH_PID_FILE" ] && kill -0 "$(cat "$DASH_PID_FILE")" 2>/dev/null; then
      echo "  Dashboard: running on port $DASH_PORT"
    else
      echo "  Dashboard: not running"
    fi

    # Check if our processes are top consumers
    echo ""
    echo "=== Top processes by CPU ==="
    ps aux --sort=-%cpu 2>/dev/null | head -6 || ps -eo pid,pcpu,pmem,comm -r 2>/dev/null | head -6
    ;;

  logs)
    tail -f "$DIR/trader.log"
    ;;

  *)
    echo "Usage: $0 {start|stop|status|dashboard|stop-dashboard|health|logs}"
    echo ""
    echo "  start          Start the trader (paper mode by default)"
    echo "  stop           Stop the trader gracefully"
    echo "  status         Show trader status, positions, recent trades"
    echo "  dashboard      Start the web dashboard"
    echo "  stop-dashboard Stop the web dashboard"
    echo "  health         System resource check (CPU, memory, disk, Tailscale)"
    echo "  logs           Tail the trader log"
    echo ""
    echo "Environment:"
    echo "  HYPERLIQUID_LIVE=1       Enable real trading"
    echo "  HL_DASH_PORT=8181        Dashboard port (default: 8181)"
    echo "  HL_DASH_USER=admin       Dashboard login user"
    echo "  HL_DASH_PASS=secret      Dashboard login password"
    exit 1
    ;;
esac
