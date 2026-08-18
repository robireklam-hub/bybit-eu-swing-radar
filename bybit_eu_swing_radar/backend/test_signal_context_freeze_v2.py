from datetime import datetime, timedelta, timezone

from research.signal_context_freeze_v2 import (
    CROSS_LAYER_SPEC_VERSION,
    SPEC_VERSION,
    build_freeze_payload,
    cross_layer_context,
    spec,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def test_v2_spec_is_prospective_only_and_preserves_v1():
    payload = spec()
    assert payload["version"] == SPEC_VERSION == "signal-context-freeze-v2"
    assert payload["source_layers"]["cross_layer_context"] == CROSS_LAYER_SPEC_VERSION == "cross-layer-context-shadow-v2"
    assert payload["prospective_start_rule"].startswith("only signals opened_at >= first persisted")
    assert payload["historical_backfill_allowed"] is False
    assert payload["v1_preserved"] is True
    assert payload["outcome_fields_read"] is False
    assert payload["promotion_allowed"] is False
    assert payload["execution_proof"] is False


def test_cross_layer_v2_future_context_is_rejected():
    record = {
        "captured_at": (NOW + timedelta(seconds=1)).isoformat(),
        "source_commit_sha": "future",
        "payload": {"data_quality": "COMPLETE", "symbols": [{"symbol": "BTCUSDC"}]},
    }
    result = cross_layer_context(record, opened_at=NOW, symbol="BTCUSDC")
    assert result["status"] == "FUTURE_REJECTED"
    assert result["symbol_context"] is None
    assert result["global_context"] is None


def test_freeze_v2_copies_symbol_context_without_trade_score():
    cross_record = {
        "captured_at": (NOW - timedelta(minutes=5)).isoformat(),
        "source_commit_sha": "ctx",
        "payload": {
            "data_quality": "COMPLETE",
            "layers": {"sector_rotation": {"status": "FRESH"}, "eth_onchain": {"status": "FRESH"}},
            "symbols": [{"symbol": "ETHUSDC", "sector_rotation": {"functional_tags": [{"id": "layer-1-l1"}]}, "onchain": {"data_quality": "COMPLETE"}}],
            "global_context": {"sector_rotation": {"taxonomy_provider": "CoinPaprika"}},
        },
    }
    payload = build_freeze_payload(
        {
            "id": 1,
            "signal_key": "sig-1",
            "strategy_version": "0.7.3",
            "signal_class": "STRICT",
            "symbol": "ETHUSDC",
            "side": "long",
            "opened_at": NOW,
            "setup_type": "liquidity_sweep",
        },
        cross_layer_record=cross_record,
        microstructure_feature=None,
        recorder_symbols=(),
        frozen_at=NOW + timedelta(minutes=1),
        source_commit_sha="main",
    )
    assert payload["spec_version"] == SPEC_VERSION
    assert payload["cross_layer_context"]["status"] == "FRESH"
    assert payload["cross_layer_context"]["symbol_context"]["onchain"]["data_quality"] == "COMPLETE"
    assert payload["cross_layer_context"]["symbol_context"]["sector_rotation"]["functional_tags"][0]["id"] == "layer-1-l1"
    assert payload["microstructure"]["status"] == "NOT_TRACKED"
    assert payload["outcome_fields_read"] is False
    assert payload["composite_score_emitted"] is False
    assert payload["promotion_allowed"] is False
