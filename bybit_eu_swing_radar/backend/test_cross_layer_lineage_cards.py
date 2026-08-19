from datetime import datetime, timezone

from research.cross_layer_context_v2 import build_context_snapshot, spec
from research.research_data_quality import source_contract
from research.research_lineage_cards import CARD_VERSION, LINEAGE_VERSION, dataset_card


NOW = datetime(2026, 8, 19, 11, 15, tzinfo=timezone.utc)
SOURCES = (
    "market_regime",
    "derivatives_positioning",
    "event_tokenomics",
    "btc_macro_cycle_etf",
    "relative_strength",
    "sector_rotation",
    "btc_onchain",
    "eth_onchain",
)


def _record(source: str):
    contract = source_contract(source)
    payload = {
        "captured_at": NOW.isoformat(),
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "immutable_history": {
            "immutable": True,
            "research_family": contract["research_family"],
            "spec_version": contract["spec_version"],
            "payload_fingerprint": f"fp-{source}",
            "point_in_time_verified": False,
        },
    }
    return {
        "captured_at": NOW.isoformat(),
        "source_commit_sha": f"sha-{source}",
        "payload": payload,
    }


def test_spec_exposes_dataset_and_feature_cards_without_live_semantic_change():
    payload = spec()
    cards = payload["lineage_cards"]
    assert payload["lineage_card_version"] == CARD_VERSION
    assert cards["card_version"] == CARD_VERSION
    assert set(cards["datasets"]) == set(SOURCES)
    assert cards["feature"]["feature_id"] == "cross-layer-context-v2"
    assert cards["feature"]["spec_version"] == "cross-layer-context-shadow-v2"
    assert cards["feature"]["production_eligible"] is False
    assert payload["research_only"] is True
    assert payload["promotion_allowed"] is False
    assert payload["composite_score_emitted"] is False
    assert payload["execution_proof"] is False


def test_snapshot_embeds_exact_input_lineage_before_immutable_output_history():
    records = {source: _record(source) for source in SOURCES}
    snapshot = build_context_snapshot(
        records,
        captured_at=NOW,
        source_commit_sha="cross-layer-output-sha",
    )
    lineage = snapshot["lineage"]
    assert lineage["lineage_version"] == LINEAGE_VERSION
    assert lineage["feature_id"] == "cross-layer-context-v2"
    assert lineage["feature_spec_version"] == "cross-layer-context-shadow-v2"
    assert lineage["output"]["captured_at"] == NOW.isoformat()
    assert lineage["output"]["source_commit_sha"] == "cross-layer-output-sha"
    assert lineage["input_count"] == 8
    assert lineage["immutable_input_fingerprint_count"] == 8
    assert lineage["references_complete"] is True
    assert lineage["all_inputs_research_usable"] is True
    assert lineage["provider_availability_verified_for_all_inputs"] is False
    assert lineage["provider_availability_inferred_from_capture_time"] is False
    assert lineage["production_usable"] is False
    assert len(lineage["lineage_fingerprint"]) == 64
    by_source = {row["source"]: row for row in lineage["inputs"]}
    for source in SOURCES:
        assert by_source[source]["snapshot_payload_fingerprint"] == f"fp-{source}"
        assert by_source[source]["source_commit_sha"] == f"sha-{source}"
        assert by_source[source]["dataset_card_fingerprint"] == dataset_card(source)["card_fingerprint"]

    assert snapshot["data_quality"] == "COMPLETE"
    assert snapshot["data_quality_contract"]["research_gate"] == "PASS"
    assert snapshot["live_strategy_mutated"] is False
    assert snapshot["promotion_allowed"] is False
    assert snapshot["composite_score_emitted"] is False
    assert snapshot["execution_proof"] is False
