"""Tests for consensus data gathering and overlay logic."""
import os
import sys
import json
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["TRADER_DB_PATH"] = _tmp.name
_tmp.close()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db

# Seed some test data
import time
db.log_trade("BTC", "buy", 50.0, 87000.0, pnl=0.0, notes="filled")
db.log_trade("BTC", "sell", 50.0, 87500.0, pnl=2.87, notes="filled")
db.log_trade("ETH", "buy", 30.0, 2100.0, pnl=0.0, notes="filled")
db.log_signal("BTC", 87000.0, "ENTRY_LONG", "4/5 bull votes", {"bull_votes": 4, "bear_votes": 1})
db.log_signal("BTC", 87500.0, "EXIT", "RSI overbought", {"rsi": 72})
db.log_equity(5002.87, 4952.87, {"BTC": {"notional": 50, "price": 87500}})
db.set_state("mode", "LIVE")
db.set_state("equity", 5002.87)


def test_gather_trading_data():
    from consensus import gather_trading_data
    data = gather_trading_data()
    assert "recent_trades" in data
    assert "signal_log" in data
    assert "equity_history" in data
    assert "current_positions" in data
    assert "mode" in data
    assert "equity" in data
    assert len(data["recent_trades"]) >= 2
    assert len(data["signal_log"]) >= 2


def test_build_analysis_prompt():
    from consensus import gather_trading_data, build_opus_analysis_prompt
    data = gather_trading_data()
    prompt = build_opus_analysis_prompt(data)
    assert "BTC" in prompt
    assert "position_scale" in prompt
    assert "pause_new_entries" in prompt
    assert "tighten_stops" in prompt
    assert len(prompt) > 200


def test_parse_overlay_response():
    from consensus import parse_overlay_response
    # Valid response
    response = json.dumps({
        "position_scale": 0.75,
        "pause_new_entries": False,
        "tighten_stops": 0.9,
        "reasoning": "Elevated volatility warrants 25% position reduction",
        "proposals": []
    })
    overlay = parse_overlay_response(response)
    assert overlay["position_scale"] == 0.75
    assert overlay["tighten_stops"] == 0.9

    # Out of bounds should clamp
    response = json.dumps({
        "position_scale": 0.1,
        "pause_new_entries": False,
        "tighten_stops": 0.5,
        "reasoning": "Testing clamp",
        "proposals": []
    })
    overlay = parse_overlay_response(response)
    assert overlay["position_scale"] == 0.25  # clamped to min
    assert overlay["tighten_stops"] == 0.7   # clamped to min


def test_parse_overlay_response_no_increase():
    from consensus import parse_overlay_response
    response = json.dumps({
        "position_scale": 1.5,
        "pause_new_entries": False,
        "tighten_stops": 1.2,
        "reasoning": "Trying to increase risk",
        "proposals": []
    })
    overlay = parse_overlay_response(response)
    assert overlay["position_scale"] == 1.0  # clamped to max
    assert overlay["tighten_stops"] == 1.0   # clamped to max


if __name__ == "__main__":
    test_gather_trading_data()
    test_build_analysis_prompt()
    test_parse_overlay_response()
    test_parse_overlay_response_no_increase()
    print("All consensus tests passed!")
    os.unlink(_tmp.name)
