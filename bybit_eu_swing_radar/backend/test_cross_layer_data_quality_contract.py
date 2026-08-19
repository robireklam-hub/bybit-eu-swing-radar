from datetime import datetime, timedelta, timezone

from research.cross_layer_context_v2 import build_context_snapshot
from research.research_data_quality import CONTRACT_VERSION, source_contract, source_max_age_seconds


NOW = datetime(2026, 8, 19, 10, 30, tzinfo=timezone.utc)
SOURCES = (
    "market_regime",
    "derivatives_positioning",
    "relative_strength",
    "sector_rotation",
    "event_tokenomics",
    "btc_macro_cycle_etf",
    "btc_onchain",
    "eth_onchain",
)


def _record(source: str, *, age_seconds: int = 60):
    contract = source_contract(source)
    captured = NOW - timedelta(seconds=age_seconds)
    return {
        "captured_at": captured.isoformat(),
        "source_commit_sha": "abc123",
        "payload": {
            "captured_at": captured.isoformat(),
            "research_only": True,
            "label_free": True,
            "context_only": True,
            "live_strategy_mutated": False,
            "promotion_allowed": False,
            "immutable_history": {
                "immutable": True,
                "research_family": contract["research_family"],
                "spec_version": contract["spec_version"],
                "payload_fingerprint": f"fingerprint-{source}",
                "point_in_time_verified": False,
            },
        },
    }


def test_cross_layer_v2_exposes_unified_contract_without_changing_complete_semantics():
    records = {source: _record(source) for source in SOURCES}
    snapshot = build_context_snapshot(records, captured_at=NOW, source_commit_sha="mainsha")

    assert snapshot["data_quality"] == "COMPLETE"
    assert snapshot["layer_fresh_count"] == len(SOURCES)
    contract = snapshot["data_quality_contract"]
    assert contract["contract_version"] == CONTRACT_VERSION
    assert contract["research_gate"] == "PASS"
    assert contract["severity"] == "WARNING"
    assert contract["production_gate"] == "BLOCK"
    assert contract["production_severity"] == "PRODUCTION_BLOCK"
    assert contract["research_usable_source_count"] == len(SOURCES)
    assert contract["provider_availability_verified_sources"] == []
    assert contract["provider_availability_inferred_from_capture_time"] is False

    for source in SOURCES:
        layer = snapshot["layers"][source]
        assert layer["status"] == "FRESH"
        assert layer["research_usable"] is True
        assert layer["severity"] == "WARNING"
        assert layer["production_usable"] is False
        assert layer["lineage"]["immutable"] is True
        assert layer["lineage"]["provider_availability_verified"] is False

    assert snapshot["live_strategy_mutated"] is False
    assert snapshot["promotion_allowed"] is False
    assert snapshot["composite_score_emitted"] is False
    assert snapshot["execution_proof"] is False


def test_stale_layer_keeps_legacy_partial_semantics_and_adds_research_block_metadata():
    records = {source: _record(source) for source in SOURCES}
    records["market_regime"] = _record(
        "market_regime", age_seconds=source_max_age_seconds("market_regime") + 1
    )
    snapshot = build_context_snapshot(records, captured_at=NOW)

    assert snapshot["data_quality"] == "PARTIAL"
    assert snapshot["layers"]["market_regime"]["status"] == "STALE"
    assert snapshot["layers"]["market_regime"]["severity"] == "RESEARCH_BLOCK"
    assert snapshot["layers"]["market_regime"]["research_usable"] is False
    assert snapshot["data_quality_contract"]["research_gate"] == "BLOCK"
    assert snapshot["data_quality_contract"]["blocked_sources"] == ["market_regime"]
    assert snapshot["live_strategy_mutated"] is False
    assert snapshot["promotion_allowed"] is False
