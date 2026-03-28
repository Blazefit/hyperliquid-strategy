"""SQLite trade logging and state persistence."""

import sqlite3
import os
import time
import json

DB_PATH = os.environ.get("TRADER_DB_PATH", os.path.join(os.path.dirname(__file__), "trader.db"))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            size_usd REAL NOT NULL,
            price REAL NOT NULL,
            order_type TEXT DEFAULT 'market',
            status TEXT DEFAULT 'filled',
            pnl REAL DEFAULT 0.0,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            equity REAL NOT NULL,
            cash REAL NOT NULL,
            positions_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            side TEXT NOT NULL,
            size_usd REAL NOT NULL,
            entry_price REAL NOT NULL,
            current_price REAL DEFAULT 0.0,
            unrealized_pnl REAL DEFAULT 0.0,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS strategy_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS signal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            details_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS consensus_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            position_scale REAL NOT NULL DEFAULT 1.0,
            pause_new_entries INTEGER NOT NULL DEFAULT 0,
            tighten_stops REAL NOT NULL DEFAULT 1.0,
            reasoning TEXT NOT NULL,
            opus_analysis TEXT NOT NULL DEFAULT '',
            minimax_review TEXT NOT NULL DEFAULT '',
            opus_final TEXT NOT NULL DEFAULT '',
            data_snapshot TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS consensus_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            proposal_type TEXT NOT NULL,
            title TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            suggested_changes TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending'
        );
    """)
    conn.commit()
    conn.close()


def log_trade(symbol, side, size_usd, price, pnl=0.0, notes=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, side, size_usd, price, pnl, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (time.time(), symbol, side, size_usd, price, pnl, notes),
    )
    conn.commit()
    conn.close()


def log_equity(equity, cash, positions):
    conn = get_conn()
    conn.execute(
        "INSERT INTO equity_snapshots (timestamp, equity, cash, positions_json) "
        "VALUES (?, ?, ?, ?)",
        (time.time(), equity, cash, json.dumps(positions)),
    )
    conn.commit()
    conn.close()


def update_position(symbol, side, size_usd, entry_price, current_price, unrealized_pnl):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO positions (symbol, side, size_usd, entry_price, current_price, unrealized_pnl, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (symbol, side, size_usd, entry_price, current_price, unrealized_pnl, time.time()),
    )
    conn.commit()
    conn.close()


def clear_position(symbol):
    conn = get_conn()
    conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
    conn.commit()
    conn.close()


def get_positions():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM positions").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_trades(limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_trades_chronological():
    """Get all trades in chronological order for cumulative PnL curve."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY timestamp ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_equity_history(limit=2000):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM equity_snapshots ORDER BY timestamp ASC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_state(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO strategy_state (key, value) VALUES (?, ?)",
        (key, json.dumps(value)),
    )
    conn.commit()
    conn.close()


def get_state(key, default=None):
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM strategy_state WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    if row:
        return json.loads(row["value"])
    return default


def log_signal(symbol, price, action, reason, details=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO signal_log (timestamp, symbol, price, action, reason, details_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (time.time(), symbol, price, action, reason, json.dumps(details or {})),
    )
    conn.commit()
    conn.close()


def get_signal_log(limit=100):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM signal_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_consensus(position_scale, pause_new_entries, tighten_stops, reasoning,
                  opus_analysis="", minimax_review="", opus_final="", data_snapshot=None):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO consensus_log (timestamp, position_scale, pause_new_entries, "
        "tighten_stops, reasoning, opus_analysis, minimax_review, opus_final, data_snapshot) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (time.time(), position_scale, int(pause_new_entries), tighten_stops,
         reasoning, opus_analysis, minimax_review, opus_final,
         json.dumps(data_snapshot or {})),
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return entry_id


def get_consensus_log(limit=7):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM consensus_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_consensus_proposal(proposal_type, title, reasoning, suggested_changes=None):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO consensus_proposals (timestamp, proposal_type, title, reasoning, "
        "suggested_changes, status) VALUES (?, ?, ?, ?, ?, 'pending')",
        (time.time(), proposal_type, title, reasoning,
         json.dumps(suggested_changes or {})),
    )
    conn.commit()
    prop_id = cur.lastrowid
    conn.close()
    return prop_id


def get_consensus_proposals(status=None):
    conn = get_conn()
    if status is not None:
        rows = conn.execute(
            "SELECT * FROM consensus_proposals WHERE status = ? ORDER BY timestamp DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM consensus_proposals ORDER BY timestamp DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_consensus_proposal(proposal_id, status):
    conn = get_conn()
    cur = conn.execute(
        "UPDATE consensus_proposals SET status = ? WHERE id = ?",
        (status, proposal_id),
    )
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise ValueError(f"No consensus proposal found with id={proposal_id}")


def _init_chat_table():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def log_chat_message(role, message):
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_messages (timestamp, role, message) VALUES (?, ?, ?)",
        (time.time(), role, message),
    )
    conn.commit()
    conn.close()


def get_chat_history(limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM chat_messages ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def clear_chat_history():
    conn = get_conn()
    conn.execute("DELETE FROM chat_messages")
    conn.commit()
    conn.close()


init_db()
_init_chat_table()
