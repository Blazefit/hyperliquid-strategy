"""Tests for risk overlay application in live trader."""
import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_load_overlay_default():
    """No overlay file = all defaults (1.0, False, 1.0)."""
    from live_trader import load_risk_overlay
    overlay = load_risk_overlay(Path("/tmp/nonexistent_overlay_test.json"))
    assert overlay["position_scale"] == 1.0
    assert overlay["pause_new_entries"] is False
    assert overlay["tighten_stops"] == 1.0


def test_load_overlay_from_file():
    """Reads overlay values from JSON file."""
    from live_trader import load_risk_overlay
    tmp = tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False)
    json.dump({
        "position_scale": 0.5,
        "pause_new_entries": True,
        "tighten_stops": 0.8,
        "reasoning": "Test overlay",
        "updated_at": "2026-03-27T00:00:00Z",
    }, tmp)
    tmp.close()
    overlay = load_risk_overlay(Path(tmp.name))
    assert overlay["position_scale"] == 0.5
    assert overlay["pause_new_entries"] is True
    assert overlay["tighten_stops"] == 0.8
    os.unlink(tmp.name)


def test_load_overlay_corrupt_file():
    """Corrupt overlay file = safe defaults."""
    from live_trader import load_risk_overlay
    tmp = tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False)
    tmp.write("not valid json{{{")
    tmp.close()
    overlay = load_risk_overlay(Path(tmp.name))
    assert overlay["position_scale"] == 1.0
    os.unlink(tmp.name)


def test_apply_position_scale():
    """Position scale reduces target_usd proportionally."""
    from live_trader import apply_overlay_to_target
    result = apply_overlay_to_target(50.0, {"position_scale": 0.5, "pause_new_entries": False, "tighten_stops": 1.0}, is_new_entry=False)
    assert result == 25.0
    result = apply_overlay_to_target(50.0, {"position_scale": 1.0, "pause_new_entries": False, "tighten_stops": 1.0}, is_new_entry=False)
    assert result == 50.0


def test_apply_pause_new_entries():
    """Pause blocks new entries but allows existing position adjustments and exits."""
    from live_trader import apply_overlay_to_target
    # New entry blocked
    result = apply_overlay_to_target(50.0, {"position_scale": 1.0, "pause_new_entries": True, "tighten_stops": 1.0}, is_new_entry=True)
    assert result == 0.0
    # Existing position adjustment still works
    result = apply_overlay_to_target(50.0, {"position_scale": 1.0, "pause_new_entries": True, "tighten_stops": 1.0}, is_new_entry=False)
    assert result == 50.0
    # Exit always passes through
    result = apply_overlay_to_target(0.0, {"position_scale": 1.0, "pause_new_entries": True, "tighten_stops": 1.0}, is_new_entry=False)
    assert result == 0.0


if __name__ == "__main__":
    test_load_overlay_default()
    test_load_overlay_from_file()
    test_load_overlay_corrupt_file()
    test_apply_position_scale()
    test_apply_pause_new_entries()
    print("All overlay tests passed!")
