from datetime import datetime, timedelta, timezone

from research.microstructure.alignment import SPEC_VERSION as ALIGNMENT_SPEC_VERSION
from research.signal_context_freeze import (
    build_freeze_payload,
    sample_gate,
    spec,
)


OPENED = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
SIGNAL = {
    "id": 7,
    "signal_key": "sig-7",
    "strategy_version": "0.7.3",
    "signal_class": "SHADOW",
    "symbol": "BTCUSDC",
    "side": "long",
    "opened_at": OPENED,
    "setup_type": "LIQUIDITY_SWEEP_RECLAIM",
}


def _cross(captured_at: datetime) -> dict:
    return {
        "captured_at": captured_at.isoformat(),
        "source_commit_sha": "source-sha",
        "payload": {
            "data_quality": "COMPLETE",
            "layers": {"market_regime": {"status": "FRESH"}},
            "symbols": [
                {
                    "symbol": "BTCUSDC",
                    "market_regime": {"regime": "RANGE"},
                    "derivatives_positioning": {"positioning_state": "MIXED"},
                }
            ],
            "global_context": {"market_regime": {"global_regime": "RANGE"}},
        },
    }


def _micro(cutoff: datetime = OPENED) -> dict:
    return {
        "signal_id": 7,
        "signal_key": "sig-7",
        "strategy_version": "0.7.3",
        "signal_class": "SHADOW",
        "symbol": "BTCUSDC",
        "side": "long",
        "opened_at": OPENED.isoformat(),
        "setup_type": "LIQUIDITY_SWEEP_RECLAIM",
        "feature_cutoff_at": cutoff.isoformat(),
        "spec_version": ALIGNMENT_SPEC_VERSION,
        "label_blind": True,
        "coverage_ratio_60s": 1.0,
        "side_flow_ratio_60s": 0.25,
    }


def test_spec_is_label_blind_and_non_promotional() -> None:
    payload = spec()
    assert payload["research_only"] is True
    assert payload["label_blind"] is True
    assert payload["outcome_fields_read"] is False
    assert payload["live_strategy_mutated"] is False
    assert payload["promotion_allowed"] is False
    assert payload["execution_proof"] is False


def test_build_freeze_uses_only_pre_signal_context() -> None:
    payload = build_freeze_payload(
        SIGNAL,
        cross_layer_record=_cross(OPENED - timedelta(minutes=30)),
        microstructure_feature=_micro(),
        recorder_symbols=("BTCUSDC", "ETHUSDC", "SOLUSDC"),
        frozen_at=OPENED + timedelta(hours=1),
        source_commit_sha="main-sha",
    )
    assert payload["cross_layer_context"]["status"] == "FRESH"
    assert payload["cross_layer_context"]["symbol_context"]["symbol"] == "BTCUSDC"
    assert payload["microstructure"]["status"] == "ALIGNED"
    assert payload["microstructure"]["features"]["coverage_ratio_60s"] == 1.0
    assert payload["outcome_fields_read"] is False
    assert payload["composite_score_emitted"] is False
    assert "net_r" not in payload


def test_future_cross_layer_payload_is_rejected_and_not_copied() -> None:
    payload = build_freeze_payload(
        SIGNAL,
        cross_layer_record=_cross(OPENED + timedelta(seconds=1)),
        microstructure_feature=None,
        recorder_symbols=("BTCUSDC",),
        frozen_at=OPENED + timedelta(hours=1),
    )
    cross = payload["cross_layer_context"]
    assert cross["status"] == "FUTURE_REJECTED"
    assert cross["symbol_context"] is None
    assert cross["global_context"] is None


def test_stale_cross_layer_is_explicit_not_neutralized() -> None:
    payload = build_freeze_payload(
        SIGNAL,
        cross_layer_record=_cross(OPENED - timedelta(hours=3)),
        microstructure_feature=None,
        recorder_symbols=("BTCUSDC",),
        frozen_at=OPENED + timedelta(hours=1),
    )
    cross = payload["cross_layer_context"]
    assert cross["status"] == "STALE"
    assert cross["symbol_context"]["market_regime"]["regime"] == "RANGE"


def test_future_microstructure_cutoff_is_rejected() -> None:
    payload = build_freeze_payload(
        SIGNAL,
        cross_layer_record=_cross(OPENED - timedelta(minutes=10)),
        microstructure_feature=_micro(OPENED + timedelta(seconds=1)),
        recorder_symbols=("BTCUSDC",),
        frozen_at=OPENED + timedelta(hours=1),
    )
    assert payload["microstructure"]["status"] == "TEMPORAL_REJECTED"
    assert payload["microstructure"]["features"] is None


def test_untracked_symbol_microstructure_is_optional() -> None:
    signal = {**SIGNAL, "symbol": "XRPUSDC"}
    payload = build_freeze_payload(
        signal,
        cross_layer_record=_cross(OPENED - timedelta(minutes=10)),
        microstructure_feature=None,
        recorder_symbols=("BTCUSDC", "ETHUSDC", "SOLUSDC"),
        frozen_at=OPENED + timedelta(hours=1),
    )
    assert payload["microstructure"]["status"] == "NOT_TRACKED"


def test_future_effect_gate_is_preregistered() -> None:
    ready = sample_gate(
        {
            "total": 60,
            "long_count": 30,
            "short_count": 30,
            "distinct_utc_days": 10,
            "cross_layer_covered": 58,
        }
    )
    assert ready["ready_for_future_effect_test"] is True
    blocked = sample_gate(
        {
            "total": 59,
            "long_count": 30,
            "short_count": 29,
            "distinct_utc_days": 10,
            "cross_layer_covered": 59,
        }
    )
    assert blocked["ready_for_future_effect_test"] is False
    assert "insufficient_total_signals" in blocked["reasons"]
