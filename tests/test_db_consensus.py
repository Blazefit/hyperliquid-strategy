"""Tests for consensus DB tables and functions."""
import os
import sys
import time
import json
import tempfile
import sqlite3

# Point DB to a temp file for tests
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["TRADER_DB_PATH"] = _tmp.name
_tmp.close()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def test_consensus_log_table_exists():
    conn = db.get_conn()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='consensus_log'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_consensus_proposals_table_exists():
    conn = db.get_conn()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='consensus_proposals'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_log_consensus_and_retrieve():
    entry_id = db.log_consensus(
        position_scale=0.75,
        pause_new_entries=False,
        tighten_stops=0.9,
        reasoning="Market volatility elevated, reducing exposure 25%",
        opus_analysis="BTC showing weakness...",
        minimax_review="Agree with reduction but 0.75 may be aggressive...",
        opus_final="Confirmed 0.75 scale after review...",
        data_snapshot={"equity": 5000, "positions": []},
    )
    assert entry_id > 0

    entries = db.get_consensus_log(limit=5)
    assert len(entries) >= 1
    latest = entries[0]
    assert latest["position_scale"] == 0.75
    assert latest["pause_new_entries"] == 0  # SQLite stores as int
    assert latest["tighten_stops"] == 0.9
    assert "volatility" in latest["reasoning"]


def test_log_consensus_proposal_and_update():
    prop_id = db.log_consensus_proposal(
        proposal_type="parameter",
        title="Consider reducing ATR_STOP_MULT to 5.0",
        reasoning="Recent trades showing wider stops than needed...",
        suggested_changes={"ATR_STOP_MULT": {"current": 5.5, "suggested": 5.0}},
    )
    assert prop_id > 0

    proposals = db.get_consensus_proposals(status="pending")
    assert len(proposals) >= 1
    assert proposals[0]["status"] == "pending"

    db.update_consensus_proposal(prop_id, status="approved")
    approved = db.get_consensus_proposals(status="approved")
    assert any(p["id"] == prop_id for p in approved)

    db.update_consensus_proposal(prop_id, status="dismissed")
    dismissed = db.get_consensus_proposals(status="dismissed")
    assert any(p["id"] == prop_id for p in dismissed)


def test_get_consensus_log_empty():
    entries = db.get_consensus_log(limit=0)
    assert entries == []


if __name__ == "__main__":
    test_consensus_log_table_exists()
    test_consensus_proposals_table_exists()
    test_log_consensus_and_retrieve()
    test_log_consensus_proposal_and_update()
    test_get_consensus_log_empty()
    print("All consensus DB tests passed!")
    os.unlink(_tmp.name)
