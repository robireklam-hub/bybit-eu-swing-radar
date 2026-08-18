from datetime import datetime, timedelta, timezone

from research.cross_layer_context_v2 import (
    LAYER_MAX_AGE_SECONDS,
    SPEC_VERSION,
    build_context_snapshot,
    spec,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _record(payload, *, age_seconds=60, sha="abc"):
    return {
        "captured_at": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "source_commit_sha": sha,
        "payload": payload,
    }


def _records():
    return {
        "market_regime": _record({"symbols": [{"symbol": "BTCUSDC", "regime": "TREND", "direction": "UP", "metrics": {"atr_ratio": 1.1}}, {"symbol": "ETHUSDC", "regime": "RANGE", "direction": "NEUTRAL", "metrics": {}}], "global_regime": "TREND"}),
        "derivatives_positioning": _record({"symbols": [{"symbol": "BTCUSDC", "positioning_state": "LONG_BUILD", "coverage": {}}, {"symbol": "ETHUSDC", "positioning_state": "MIXED", "coverage": {}}]}),
        "relative_strength": _record({"symbols": [{"symbol": "BTCUSDC", "rank": 2, "rs_score": 70, "state": "OUTPERFORMER", "rotation_context": "STABLE"}, {"symbol": "ETHUSDC", "rank": 1, "rs_score": 80, "state": "LEADER", "rotation_context": "ACCELERATING"}], "leaders": ["ETHUSDC"]}),
        "sector_rotation": _record({"symbols": [{"symbol": "BTCUSDC", "functional_tags": [{"id": "currency", "name": "Currency"}], "taxonomy_resolution": {"status": "RESOLVED_UNIQUE", "provider_coin_id": "btc-bitcoin", "ambiguous": False}}, {"symbol": "ETHUSDC", "functional_tags": [{"id": "layer-1-l1", "name": "Layer 1 (L1)"}], "taxonomy_resolution": {"status": "RESOLVED_UNIQUE", "provider_coin_id": "eth-ethereum", "ambiguous": False}}], "taxonomy_provider": "CoinPaprika", "resolution_coverage_pct": 100, "taxonomy_coverage_pct": 80, "rotation_ranked_group_count": 5, "top_relative_strength_groups": [{"tag_id": "layer-1-l1"}]}),
        "event_tokenomics": _record({"events": [], "tracked_symbols": ["BTCUSDC", "ETHUSDC"], "event_count": 0}),
        "btc_macro_cycle_etf": _record({"btc_price": {"close": 100}, "cycle": {"phase": "x"}, "etf": {}, "macro": {}}),
        "btc_onchain": _record({"data_quality": "COMPLETE", "coin_metrics": {"metrics": {"TxCnt": {"available": True, "latest": 500000, "latest_date": "2026-08-17", "mean_7d": 490000, "mean_30d": 480000, "latest_vs_30d_mean_pct": 4.1, "change_30d_pct": 2.0}}}, "source_status": {"coin_metrics": {"status": "LIVE"}}}),
        "eth_onchain": _record({"data_quality": "COMPLETE", "coin_metrics": {"metrics": {"TxCnt": {"available": True, "latest": 2700000, "latest_date": "2026-08-17", "mean_7d": 2500000, "mean_30d": 2300000, "latest_vs_30d_mean_pct": 17.4, "change_30d_pct": 4.7}}}, "source_status": {"coin_metrics": {"status": "LIVE"}}}),
    }


def test_spec_v2_preserves_v1_and_adds_three_layers():
    payload = spec()
    assert payload["version"] == SPEC_VERSION == "cross-layer-context-shadow-v2"
    assert payload["research_only"] is True
    assert payload["promotion_allowed"] is False
    assert payload["composite_score_emitted"] is False
    assert payload["execution_proof"] is False
    assert payload["versioning"]["v1_preserved"] is True
    assert payload["new_vs_v1"] == ["sector_rotation", "btc_onchain", "eth_onchain"]
    assert set(payload["layers"]) == set(LAYER_MAX_AGE_SECONDS)


def test_v2_joins_sector_and_asset_specific_onchain_without_score():
    snapshot = build_context_snapshot(_records(), captured_at=NOW, source_commit_sha="mainsha")
    assert snapshot["data_quality"] == "COMPLETE"
    assert snapshot["layer_fresh_count"] == 8
    assert snapshot["layer_count"] == 8
    assert snapshot["composite_score_emitted"] is False
    assert snapshot["execution_proof"] is False
    rows = {row["symbol"]: row for row in snapshot["symbols"]}
    assert rows["BTCUSDC"]["sector_rotation"]["functional_tags"][0]["id"] == "currency"
    assert rows["ETHUSDC"]["sector_rotation"]["functional_tags"][0]["id"] == "layer-1-l1"
    assert rows["BTCUSDC"]["onchain"]["metrics"]["TxCnt"]["latest"] == 500000
    assert rows["ETHUSDC"]["onchain"]["metrics"]["TxCnt"]["latest"] == 2700000
    assert rows["BTCUSDC"]["coverage"]["onchain"] is True
    assert snapshot["global_context"]["sector_rotation"]["taxonomy_provider"] == "CoinPaprika"


def test_future_layer_is_rejected_and_payload_not_joined():
    records = _records()
    records["sector_rotation"] = {
        "captured_at": (NOW + timedelta(seconds=30)).isoformat(),
        "source_commit_sha": "future",
        "payload": {"symbols": [{"symbol": "BTCUSDC", "functional_tags": [{"id": "bad"}]}]},
    }
    snapshot = build_context_snapshot(records, captured_at=NOW)
    assert snapshot["layers"]["sector_rotation"]["status"] == "FUTURE_REJECTED"
    rows = {row["symbol"]: row for row in snapshot["symbols"]}
    assert rows["BTCUSDC"]["sector_rotation"] is None
    assert snapshot["data_quality"] == "PARTIAL"


def test_stale_onchain_remains_explicit_but_payload_is_descriptive():
    records = _records()
    records["eth_onchain"] = _record({"data_quality": "COMPLETE", "coin_metrics": {"metrics": {"TxCnt": {"available": True, "latest": 1}}}}, age_seconds=LAYER_MAX_AGE_SECONDS["eth_onchain"] + 1)
    snapshot = build_context_snapshot(records, captured_at=NOW)
    assert snapshot["layers"]["eth_onchain"]["status"] == "STALE"
    assert snapshot["data_quality"] == "PARTIAL"
    rows = {row["symbol"]: row for row in snapshot["symbols"]}
    assert rows["ETHUSDC"]["onchain"]["metrics"]["TxCnt"]["latest"] == 1
