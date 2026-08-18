from __future__ import annotations

from scripts.production_microstructure_effect_status import validate_effect_status


def _spec() -> dict:
    return {
        "effect_spec_version": "microstructure-effect-test-v1",
        "promotion_rule": "Never promote from this forward sample alone; require a subsequent untouched validation period.",
    }


def _base(status: str) -> dict:
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "effect_spec": _spec(),
        "status": status,
    }


def test_waiting_effect_status_is_semantically_valid() -> None:
    assert validate_effect_status(_base("WAITING_FOR_SAMPLE")) == (True, "ok")
    assert validate_effect_status(_base("WAITING_FOR_CLOSED_OUTCOMES")) == (True, "ok")


def test_complete_effect_status_requires_frozen_hypothesis_contract() -> None:
    payload = _base("COMPLETE")
    payload.update({
        "results": [
            {"id": "H1", "feature": "flow_book_concordance_60s", "expected_direction": "positive", "verdict": "INCONCLUSIVE"},
            {"id": "H2", "feature": "side_microprice_displacement_bps_15s", "expected_direction": "positive", "verdict": "SUPPORTED"},
            {"id": "H3", "feature": "side_book_pressure_ratio_60s", "expected_direction": "positive", "verdict": "INCONCLUSIVE"},
            {"id": "H4", "feature": "spread_bps_mean_15s", "expected_direction": "negative", "verdict": "INCONCLUSIVE"},
        ],
        "promotion_decision": "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION",
    })
    assert validate_effect_status(payload) == (True, "ok")


def test_complete_effect_status_fails_if_direction_is_mutated() -> None:
    payload = _base("COMPLETE")
    payload.update({
        "results": [
            {"id": "H1", "feature": "flow_book_concordance_60s", "expected_direction": "negative", "verdict": "INCONCLUSIVE"},
            {"id": "H2", "feature": "side_microprice_displacement_bps_15s", "expected_direction": "positive", "verdict": "INCONCLUSIVE"},
            {"id": "H3", "feature": "side_book_pressure_ratio_60s", "expected_direction": "positive", "verdict": "INCONCLUSIVE"},
            {"id": "H4", "feature": "spread_bps_mean_15s", "expected_direction": "negative", "verdict": "INCONCLUSIVE"},
        ],
        "promotion_decision": "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION",
    })
    assert validate_effect_status(payload)[0] is False
