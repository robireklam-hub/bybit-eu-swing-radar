import os

os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/test")
os.environ.setdefault("RADAR_API_KEY", "test-radar-key")
os.environ.setdefault("COINALYZE_API_KEY", "test-coinalyze-key")

from fastapi import FastAPI

from app.research_microstructure_alignment_v2_api import build_alignment_v2_status
from app.research_prospective_funnel_api import attach_prospective_funnel_research


SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]


def _features(counts):
    rows = []
    signal_id = 1
    for symbol, count in counts.items():
        for _ in range(count):
            rows.append({"signal_id": signal_id, "symbol": symbol})
            signal_id += 1
    return rows


def test_v2_route_is_attached_by_production_research_composition():
    app = FastAPI()

    def require_api_key():
        return None

    attach_prospective_funnel_research(app, require_api_key)
    paths = {route.path for route in app.routes}
    assert "/v1/research/microstructure/alignment-status-v2" in paths
    assert "/v1/day-trade/research/prospective-funnel/status" in paths


def test_v2_status_is_v074_label_blind_and_closed_to_promotion():
    counts = {"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}
    payload = build_alignment_v2_status(
        {"ready_for_forward_feature_analysis": True},
        _features(counts),
        SYMBOLS,
        counts,
    )
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["label_blind"] is True
    assert payload["post_signal_data_used"] is False
    assert payload["outcome_visible"] is False
    assert payload["promotion_allowed"] is False
    assert payload["spec"]["spec_version"] == "microstructure-forward-alignment-v2"
    assert payload["spec"]["preregistered_strategy_version"] == "0.7.4"
    assert payload["preregistered_strategy_version"] == "0.7.4"
    assert payload["strategy_version_isolated"] is True
    assert payload["alignment_coverage"]["status"] == "ALIGNED"
    assert payload["sample"]["total_signals"] == 60
    assert payload["ready_for_preregistered_effect_test"] is True


def test_v2_coverage_failure_closes_effect_gate():
    payload = build_alignment_v2_status(
        {"ready_for_forward_feature_analysis": True},
        _features({"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}),
        SYMBOLS,
        {"BTCUSDC": 21, "ETHUSDC": 20, "SOLUSDC": 20},
    )
    assert payload["sample"]["ready_for_preregistered_effect_test"] is True
    assert payload["alignment_coverage"]["status"] == "COVERAGE_FAILURE"
    assert payload["alignment_coverage_ready"] is False
    assert payload["ready_for_preregistered_effect_test"] is False


def test_v2_waiting_state_is_explicit_when_no_forward_signals_exist():
    payload = build_alignment_v2_status(
        {"ready_for_forward_feature_analysis": True},
        [],
        SYMBOLS,
        {symbol: 0 for symbol in SYMBOLS},
    )
    assert payload["alignment_coverage"]["status"] == "WAITING_FOR_FORWARD_SIGNALS"
    assert payload["alignment_coverage"]["reason"] == "waiting_for_forward_signals"
    assert payload["ready_for_preregistered_effect_test"] is False
