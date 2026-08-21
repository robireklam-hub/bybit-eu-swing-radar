from copy import deepcopy

from research.day_barrier_clear_rearm_v1 import (
    observe_closed_5m_barrier_clear,
    parent_event_eligibility,
)
from research.research_governance import trial_fingerprint, trial_manifest


def _parent(**overrides):
    item = {
        "strategy_version": "0.7.5",
        "symbol": "BTCUSDC",
        "side": "long",
        "category": "WATCH_ONLY",
        "decision": "NO_TRADE",
        "tradeable": True,
        "shortable": True,
        "execution_status": "DAY_TRADE_EXECUTABLE",
        "setup_score": 71.88,
        "expansion_score": 62.05,
        "side_direction_score": 57.6,
        "quality_score": 99.99,
        "entry_zone": {"low": 69863.5, "high": 69877.6},
        "stop": 69500.0,
        "targets": [70200.0, 70500.0],
        "trigger": {
            "triggered": True,
            "route": "CLOSED_5M_RANGE_BREAKOUT",
            "event_bar_time": "2026-08-20T07:05:00+00:00",
        },
        "metrics": {
            "nearest_structural_barrier": 69998.4,
            "barrier_before_tp2": True,
            "target_path_valid": False,
            "expected_rr_without_barrier": 1.8,
        },
        "derivatives": {},
    }
    item.update(overrides)
    return item


def test_trial_manifest_is_frozen_to_v075_and_spot_execution_invariants():
    manifest = trial_manifest("day-barrier-clear-rearm-v1")
    assert manifest["frozen"] is True
    assert manifest["parent_strategy_version"] == "0.7.5"
    assert manifest["quote_asset"] == "USDC"
    assert manifest["long_execution"] == "USDC_SPOT"
    assert manifest["short_execution"] == "VERIFIED_BORROWABLE_USDC_SPOT_MARGIN_ONLY"
    assert manifest["perpetual_execution"] is False
    assert manifest["derivatives_context_only"] is True
    assert manifest["missing_derivatives_hard_gate"] is False
    assert len(trial_fingerprint("day-barrier-clear-rearm-v1")) == 64


def test_v075_barrier_blocked_parent_is_eligible_and_derivatives_are_not_a_gate():
    missing = _parent(derivatives={})
    enriched = _parent(derivatives={"oi": 123, "funding": 0.001})
    assert parent_event_eligibility(missing)["eligible"] is True
    assert parent_event_eligibility(enriched)["eligible"] is True
    assert parent_event_eligibility(missing)["checks"] == parent_event_eligibility(enriched)["checks"]
    assert parent_event_eligibility(missing)["hard_gate_from_derivatives"] is False


def test_parent_cohort_is_pinned_to_v075_and_non_usdc_is_rejected():
    assert parent_event_eligibility(_parent(strategy_version="0.7.6"))["eligible"] is False
    assert parent_event_eligibility(_parent(strategy_version="0.7.4"))["eligible"] is False
    assert parent_event_eligibility(_parent(symbol="BTCUSDT"))["eligible"] is False


def test_short_requires_verified_borrowability():
    blocked = _parent(side="short", shortable=False)
    blocked["metrics"]["nearest_structural_barrier"] = 69252.4
    assert parent_event_eligibility(blocked)["eligible"] is False
    allowed = deepcopy(blocked)
    allowed["shortable"] = True
    assert parent_event_eligibility(allowed)["eligible"] is True


def test_only_closed_5m_can_clear_long_barrier_and_geometry_is_never_inherited():
    parent = _parent()
    rows = [
        {"time": "t1", "close": 70010.0, "closed": False},
        {"time": "t2", "close": 69990.0, "closed": True},
        {"time": "t3", "close": 70020.0, "closed": True},
    ]
    result = observe_closed_5m_barrier_clear(
        parent, rows, original_boundary_held=True, atr_5m=100.0,
        context={"volume_ratio_5m": 1.7, "outcome": "should-be-removed"},
    )
    assert result["barrier_cleared"] is True
    assert result["bars_to_clear"] == 3
    assert result["clear_bar_time"] == "t3"
    assert round(result["clearance_atr_5m"], 6) == round((70020.0 - 69998.4) / 100.0, 6)
    assert result["fresh_geometry_required"] is True
    assert result["inherited_parent_geometry"] is None
    assert "entry_zone" not in result
    assert "stop" not in result
    assert "targets" not in result
    assert "outcome" not in result["context"]
    assert result["execution_authorized"] is False


def test_short_clear_uses_closed_price_below_barrier():
    parent = _parent(side="short")
    parent["metrics"]["nearest_structural_barrier"] = 69252.4
    rows = [
        {"time": "t1", "close": 69260.0, "closed": True},
        {"time": "t2", "close": 69240.0, "closed": True},
    ]
    result = observe_closed_5m_barrier_clear(parent, rows, original_boundary_held=True)
    assert result["barrier_cleared"] is True
    assert result["bars_to_clear"] == 2
    assert result["clear_close"] == 69240.0


def test_barrier_clear_is_not_admitted_after_original_boundary_failure():
    result = observe_closed_5m_barrier_clear(
        _parent(),
        [{"time": "t1", "close": 70100.0, "closed": True}],
        original_boundary_held=False,
    )
    assert result["parent_eligible"] is True
    assert result["barrier_cleared"] is False
    assert result["bars_to_clear"] is None


def test_capture_contract_contains_no_forward_outcome_labels():
    result = observe_closed_5m_barrier_clear(
        _parent(), [], original_boundary_held=True,
        context={
            "forward_return": 0.03,
            "mfe": 2.0,
            "mae": -0.5,
            "pnl": 100,
            "win": True,
            "session": "US",
        },
    )
    assert result["context"] == {"session": "US"}
    assert result["outcome_visibility"] == "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE"
    assert result["live_strategy_mutation"] is False
