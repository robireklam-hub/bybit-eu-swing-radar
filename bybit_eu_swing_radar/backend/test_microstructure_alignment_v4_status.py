import os

os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/test")
os.environ.setdefault("RADAR_API_KEY", "test-radar-key")
os.environ.setdefault("COINALYZE_API_KEY", "test-coinalyze-key")

from fastapi import FastAPI

from app.research_microstructure_alignment_v4_api import build_alignment_v4_status
from app.research_prospective_funnel_api import attach_prospective_funnel_research
from research.microstructure import alignment_v4
from scripts.production_microstructure_alignment_status_v4 import validate_alignment_status


SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]


def _features(counts):
    rows = []
    signal_id = 1
    for symbol, count in counts.items():
        for _ in range(count):
            rows.append({"signal_id": signal_id, "symbol": symbol})
            signal_id += 1
    return rows


def _production_payload(counts=None):
    counts = counts or {"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}
    return build_alignment_v4_status(
        {"ready_for_forward_feature_analysis": True},
        _features(counts),
        SYMBOLS,
        counts,
    )


def test_v4_route_is_attached_by_production_research_composition():
    app = FastAPI()

    def require_api_key():
        return None

    attach_prospective_funnel_research(app, require_api_key)
    paths = {route.path for route in app.routes}
    assert "/v1/research/microstructure/alignment-status-v2" in paths
    assert "/v1/research/microstructure/alignment-status-v3" in paths
    assert "/v1/research/microstructure/alignment-status-v4" in paths
    assert "/v1/day-trade/research/prospective-funnel/status" in paths


def test_v4_status_is_v076_label_blind_and_closed_to_promotion():
    payload = _production_payload()
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["label_blind"] is True
    assert payload["post_signal_data_used"] is False
    assert payload["outcome_visible"] is False
    assert payload["promotion_allowed"] is False
    assert payload["threshold_search_allowed"] is False
    assert payload["spec"]["spec_version"] == "microstructure-forward-alignment-v4"
    assert payload["spec"]["parent_spec_version"] == "microstructure-forward-alignment-v3"
    assert payload["spec"]["preregistered_strategy_version"] == "0.7.6"
    assert payload["preregistered_strategy_version"] == "0.7.6"
    assert payload["strategy_version_isolated"] is True
    assert payload["alignment_coverage"]["status"] == "ALIGNED"
    assert payload["sample"]["total_signals"] == 60
    assert payload["ready_for_preregistered_effect_test"] is True
    assert payload["spec"]["cohort_start_at"] == alignment_v4.COHORT_START_AT.isoformat()
    assert payload["spec"]["production_activation_evidence"]["exact_production_verifier_pr"] == 399


def test_v4_coverage_failure_closes_effect_gate():
    payload = build_alignment_v4_status(
        {"ready_for_forward_feature_analysis": True},
        _features({"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}),
        SYMBOLS,
        {"BTCUSDC": 21, "ETHUSDC": 20, "SOLUSDC": 20},
    )
    assert payload["sample"]["ready_for_preregistered_effect_test"] is True
    assert payload["alignment_coverage"]["status"] == "COVERAGE_FAILURE"
    assert payload["alignment_coverage_ready"] is False
    assert payload["ready_for_preregistered_effect_test"] is False


def test_v4_waiting_state_is_explicit_when_no_forward_signals_exist():
    payload = build_alignment_v4_status(
        {"ready_for_forward_feature_analysis": True},
        [],
        SYMBOLS,
        {symbol: 0 for symbol in SYMBOLS},
    )
    assert payload["alignment_coverage"]["status"] == "WAITING_FOR_FORWARD_SIGNALS"
    assert payload["alignment_coverage"]["reason"] == "waiting_for_forward_signals"
    assert payload["ready_for_preregistered_effect_test"] is False


def test_v4_production_validator_accepts_only_frozen_contract():
    payload = _production_payload()
    assert validate_alignment_status(payload) == (True, "ok")


def test_v4_production_validator_fails_closed_if_any_research_gate_opens():
    for field, bad_value in (
        ("outcome_visible", True),
        ("promotion_allowed", True),
        ("threshold_search_allowed", True),
        ("strategy_version_isolated", False),
        ("preregistered_strategy_version", "0.7.5"),
    ):
        payload = _production_payload()
        payload[field] = bad_value
        ok, reason = validate_alignment_status(payload)
        assert ok is False, field
        assert reason != "ok"


def test_v4_production_validator_fails_closed_on_activation_evidence_drift():
    payload = _production_payload()
    payload["spec"]["cohort_start_at"] = "2026-08-21T13:54:00+00:00"
    assert validate_alignment_status(payload) == (False, "cohort_start_at_mutated")

    mutations = (
        ("strategy_merge_sha", "0" * 40),
        ("exact_production_verifier_pr", 400),
        ("verified_by_utc", "2026-08-21T13:52:44+00:00"),
        ("cohort_start_rule", "after_exact_production_verification"),
    )
    for field, bad_value in mutations:
        payload = _production_payload()
        payload["spec"]["production_activation_evidence"][field] = bad_value
        ok, reason = validate_alignment_status(payload)
        assert ok is False, field
        assert reason == f"production_activation_evidence_{field}_mutated"
