import pytest

from research.day_missed_move_audit_v1 import audit_blocked_candidate, audit_spec


def test_barrier_blocked_long_records_large_missed_favorable_move():
    candidate = {
        "symbol": "BTCUSDC",
        "side": "long",
        "decision": "WAIT",
        "setup_state": "VALID",
        "entry_state": "BLOCKED_BY_BARRIER",
        "reference_entry": 69_961.4,
        "stop": 69_569.8,
    }
    future = [
        {"high": 70_500.0, "low": 69_800.0},
        {"high": 71_500.0, "low": 70_300.0},
        {"high": 72_844.0, "low": 71_100.0},
    ]
    result = audit_blocked_candidate(candidate, future)
    assert result["blocker"] == "BLOCKED_BY_BARRIER"
    assert result["mfe_r"] > 7.0
    assert result["favorable_move_pct"] > 4.0
    assert result["reached_favorable_pct"] == {"1%": True, "2%": True, "3%": True}


def test_audit_rejects_executed_trade_snapshots():
    with pytest.raises(ValueError):
        audit_blocked_candidate(
            {
                "symbol": "BTCUSDC",
                "side": "long",
                "decision": "TRADE",
                "reference_entry": 100.0,
                "stop": 99.0,
            },
            [],
        )


def test_audit_is_explicitly_offline_outcome_bearing():
    spec = audit_spec()
    assert spec["research_only"] is True
    assert spec["live_import_allowed"] is False
    assert spec["outcome_bearing"] is True
    assert spec["blocker_frozen_at_decision_time"] is True
