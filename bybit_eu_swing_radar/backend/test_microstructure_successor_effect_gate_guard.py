from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parent / "scripts" / "production_microstructure_successor_effect_gate_guard.py"
spec = importlib.util.spec_from_file_location("successor_effect_gate_guard", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
validate_successor_gate = module.validate_successor_gate


def payload(version: str, *, ready: bool = False) -> dict:
    sample = {
        "ready_for_preregistered_effect_test": ready,
        "reasons": [] if ready else ["minimum_total_not_met"],
        "total_signals": 60 if ready else 16,
        "per_symbol": {"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20} if ready else {"BTCUSDC": 6, "ETHUSDC": 0, "SOLUSDC": 10},
        "minimum_total": 60,
        "minimum_per_symbol": 10,
    }
    result = {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "post_signal_data_used": False,
        "outcome_visible": False,
        "promotion_allowed": False,
        "strategy_version_isolated": True,
        "preregistered_strategy_version": version,
        "data_quality_ready": True,
        "alignment_coverage_ready": True,
        "ready_for_preregistered_effect_test": ready,
        "sample": sample,
        "alignment_coverage": {
            "status": "ALIGNED",
            "reason": "all_forward_signals_aligned",
            "journal_signal_count": sample["total_signals"],
            "aligned_signal_count": sample["total_signals"],
            "unaligned_signal_count": 0,
            "per_symbol": {},
        },
        "spec": {
            "outcome_visible": False,
            "promotion_allowed": False,
        },
    }
    if version == "0.7.5":
        result["threshold_search_allowed"] = False
        result["spec"]["threshold_search_allowed"] = False
    return result


def test_waiting_successor_gate_remains_label_blind_and_closed() -> None:
    ok, reason = validate_successor_gate(payload("0.7.4"), "0.7.4")
    assert ok is True
    assert reason == "waiting_outcomes_closed"


def test_sample_ready_does_not_open_outcomes_or_promotion() -> None:
    ok, reason = validate_successor_gate(payload("0.7.5", ready=True), "0.7.5")
    assert ok is True
    assert reason == "sample_ready_outcomes_still_closed"


def test_outcome_exposure_fails_closed() -> None:
    candidate = payload("0.7.4")
    candidate["outcome_visible"] = True
    assert validate_successor_gate(candidate, "0.7.4") == (False, "unexpected_outcome_visible")


def test_v075_threshold_search_exposure_fails_closed() -> None:
    candidate = payload("0.7.5")
    candidate["threshold_search_allowed"] = True
    assert validate_successor_gate(candidate, "0.7.5") == (False, "threshold_search_gate_open")


def test_effect_readiness_must_match_quality_coverage_and_sample() -> None:
    candidate = payload("0.7.4", ready=True)
    candidate["alignment_coverage_ready"] = False
    assert validate_successor_gate(candidate, "0.7.4") == (False, "effect_readiness_inconsistent")
