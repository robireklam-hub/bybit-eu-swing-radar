from datetime import datetime, timedelta, timezone

from research.signal_context_freeze_v3 import (
    CROSS_LAYER_SPEC_VERSION, HISTORY_FAMILY, HISTORY_SOURCE, SPEC_VERSION,
    build_freeze_payload, spec,
)

NOW = datetime(2026, 8, 19, 8, 30, tzinfo=timezone.utc)


def test_v3_contract_requires_immutable_history_and_preserves_prior_cohorts():
    payload = spec()
    assert payload["version"] == SPEC_VERSION == "signal-context-freeze-v3"
    assert payload["source_layers"]["cross_layer_context"] == CROSS_LAYER_SPEC_VERSION == "cross-layer-context-shadow-v2"
    assert payload["cross_layer_storage_source"] == HISTORY_SOURCE == "immutable_raw_history_v1"
    assert payload["cross_layer_history_family"] == HISTORY_FAMILY == "cross-layer-context-v2"
    assert payload["immutable_history_required"] is True
    assert payload["historical_backfill_allowed"] is False
    assert payload["v1_preserved"] is True and payload["v2_preserved"] is True
    assert payload["temporal_contract"]["source_capture_time_safe"] is True
    assert payload["temporal_contract"]["provider_availability_verified"] is False
    assert payload["promotion_allowed"] is False


def test_v3_freeze_copies_history_fingerprint_without_score_mutation():
    record = {
        "captured_at": (NOW - timedelta(minutes=5)).isoformat(),
        "source_commit_sha": "ctx",
        "payload_fingerprint": "abc123",
        "payload": {
            "data_quality": "COMPLETE",
            "layers": {"sector_rotation": {"status": "FRESH"}},
            "symbols": [{"symbol": "BTCUSDC", "sector_rotation": {"state": "LEADING"}}],
            "global_context": {},
        },
    }
    payload = build_freeze_payload(
        {
            "id": 7, "signal_key": "sig-v3", "strategy_version": "0.7.3",
            "signal_class": "STRICT", "symbol": "BTCUSDC", "side": "long",
            "opened_at": NOW, "setup_type": "liquidity_sweep",
        },
        cross_layer_record=record, microstructure_feature=None, recorder_symbols=(),
        frozen_at=NOW + timedelta(minutes=1), source_commit_sha="main",
    )
    cross = payload["cross_layer_context"]
    assert payload["spec_version"] == SPEC_VERSION
    assert payload["cross_layer_storage_source"] == HISTORY_SOURCE
    assert cross["status"] == "FRESH"
    assert cross["history_payload_fingerprint"] == "abc123"
    assert cross["history_immutable"] is True
    assert cross["source_capture_time_safe"] is True
    assert cross["provider_availability_verified"] is False
    assert payload["composite_score_emitted"] is False
    assert payload["promotion_allowed"] is False
