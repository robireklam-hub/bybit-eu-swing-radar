from datetime import datetime, timezone

from research.research_data_quality import evaluate_source_record, source_contract
from research.research_lineage_cards import (
    CARD_VERSION,
    LINEAGE_VERSION,
    build_capture_lineage,
    cards_manifest,
    dataset_card,
    feature_card,
)


NOW = datetime(2026, 8, 19, 11, 0, tzinfo=timezone.utc)
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


def _record(source: str, index: int = 0):
    contract = source_contract(source)
    captured = NOW.replace(minute=index)
    payload = {
        "captured_at": captured.isoformat(),
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "immutable_history": {
            "immutable": True,
            "research_family": contract["research_family"],
            "spec_version": contract["spec_version"],
            "payload_fingerprint": f"payload-{source}-{index}",
            "point_in_time_verified": False,
        },
    }
    return {
        "captured_at": captured.isoformat(),
        "source_commit_sha": f"sha-{source}",
        "payload": payload,
    }


def _records_and_quality():
    records = {source: _record(source) for source in SOURCES}
    quality = {
        source: evaluate_source_record(source, record, observed_at=NOW)
        for source, record in records.items()
    }
    return records, quality


def test_dataset_card_is_deterministic_and_contract_backed():
    first = dataset_card("market_regime")
    second = dataset_card("market_regime")
    contract = source_contract("market_regime")
    assert first == second
    assert first["card_version"] == CARD_VERSION
    assert first["card_type"] == "DATASET"
    assert first["dataset_id"] == contract["research_family"]
    assert first["spec_version"] == contract["spec_version"]
    assert first["freshness_budget_seconds"] == contract["max_age_seconds"]
    assert first["time_semantics"]["provider_availability_inference"] == "FORBIDDEN_FROM_CAPTURE_TIME_ALONE"
    assert len(first["card_fingerprint"]) == 64
    assert first["production_eligible"] is False


def test_feature_card_references_dataset_card_fingerprints():
    card = feature_card(
        feature_id="cross-layer-context-v2",
        spec_version="cross-layer-context-shadow-v2",
        input_sources=SOURCES,
        max_symbols=24,
    )
    assert card["card_type"] == "FEATURE_SET"
    assert card["research_only"] is True
    assert card["label_free"] is True
    assert card["production_eligible"] is False
    assert card["execution_proof"] is False
    assert len(card["input_datasets"]) == 8
    for row in card["input_datasets"]:
        assert row["dataset_card_fingerprint"] == dataset_card(row["source"])["card_fingerprint"]
    assert len(card["card_fingerprint"]) == 64


def test_cards_manifest_is_ordered_and_deterministic():
    first = cards_manifest(
        feature_id="cross-layer-context-v2",
        spec_version="cross-layer-context-shadow-v2",
        input_sources=SOURCES,
        max_symbols=24,
    )
    second = cards_manifest(
        feature_id="cross-layer-context-v2",
        spec_version="cross-layer-context-shadow-v2",
        input_sources=SOURCES,
        max_symbols=24,
    )
    assert first == second
    assert set(first["datasets"]) == set(SOURCES)
    assert len(first["manifest_fingerprint"]) == 64


def test_capture_lineage_references_exact_immutable_input_snapshots():
    records, quality = _records_and_quality()
    lineage = build_capture_lineage(
        records,
        quality,
        feature_id="cross-layer-context-v2",
        feature_spec_version="cross-layer-context-shadow-v2",
        input_sources=SOURCES,
        captured_at=NOW,
        source_commit_sha="output-sha",
        max_symbols=24,
    )
    assert lineage["lineage_version"] == LINEAGE_VERSION
    assert lineage["input_count"] == 8
    assert lineage["immutable_input_fingerprint_count"] == 8
    assert lineage["references_complete"] is True
    assert lineage["all_inputs_research_usable"] is True
    assert lineage["point_in_time_verified_for_all_inputs"] is False
    assert lineage["provider_availability_verified_for_all_inputs"] is False
    assert lineage["provider_availability_inferred_from_capture_time"] is False
    assert lineage["production_usable"] is False
    assert lineage["output"]["source_commit_sha"] == "output-sha"
    assert len(lineage["lineage_fingerprint"]) == 64
    for row in lineage["inputs"]:
        assert row["snapshot_payload_fingerprint"].startswith("payload-")
        assert row["immutable"] is True
        assert row["dataset_card_fingerprint"] == dataset_card(row["source"])["card_fingerprint"]
        assert row["provider_availability_verified"] is False


def test_lineage_fingerprint_is_stable_for_equivalent_mapping_order():
    records, quality = _records_and_quality()
    first = build_capture_lineage(
        records,
        quality,
        feature_id="cross-layer-context-v2",
        feature_spec_version="cross-layer-context-shadow-v2",
        input_sources=SOURCES,
        captured_at=NOW,
        source_commit_sha="output-sha",
        max_symbols=24,
    )
    reversed_records = dict(reversed(list(records.items())))
    reversed_quality = dict(reversed(list(quality.items())))
    second = build_capture_lineage(
        reversed_records,
        reversed_quality,
        feature_id="cross-layer-context-v2",
        feature_spec_version="cross-layer-context-shadow-v2",
        input_sources=SOURCES,
        captured_at=NOW,
        source_commit_sha="output-sha",
        max_symbols=24,
    )
    assert first["lineage_fingerprint"] == second["lineage_fingerprint"]
    assert first == second


def test_missing_or_legacy_input_is_explicit_and_not_falsely_complete():
    records, quality = _records_and_quality()
    legacy = dict(records["btc_onchain"])
    legacy["payload"] = dict(legacy["payload"])
    legacy["payload"].pop("immutable_history")
    records["btc_onchain"] = legacy
    records["eth_onchain"] = None
    quality["btc_onchain"] = evaluate_source_record("btc_onchain", legacy, observed_at=NOW)
    quality["eth_onchain"] = evaluate_source_record("eth_onchain", None, observed_at=NOW)

    lineage = build_capture_lineage(
        records,
        quality,
        feature_id="cross-layer-context-v2",
        feature_spec_version="cross-layer-context-shadow-v2",
        input_sources=SOURCES,
        captured_at=NOW,
        source_commit_sha="output-sha",
        max_symbols=24,
    )
    assert lineage["references_complete"] is False
    assert lineage["all_inputs_research_usable"] is False
    assert lineage["immutable_input_fingerprint_count"] == 6
    assert lineage["production_usable"] is False
    by_source = {row["source"]: row for row in lineage["inputs"]}
    assert by_source["btc_onchain"]["immutable_history_present"] is False
    assert by_source["btc_onchain"]["snapshot_payload_fingerprint"] is None
    assert by_source["eth_onchain"]["captured_at"] is None
    assert by_source["eth_onchain"]["snapshot_payload_fingerprint"] is None


def test_provider_availability_flag_requires_explicit_quality_evidence():
    records, quality = _records_and_quality()
    quality = {source: dict(row) for source, row in quality.items()}
    quality["market_regime"] = dict(quality["market_regime"])
    quality["market_regime"]["lineage"] = dict(quality["market_regime"]["lineage"])
    quality["market_regime"]["lineage"]["provider_availability_verified"] = True
    quality["market_regime"]["lineage"]["availability_semantics"] = "PROVIDER_AVAILABILITY_VERIFIED"

    lineage = build_capture_lineage(
        records,
        quality,
        feature_id="cross-layer-context-v2",
        feature_spec_version="cross-layer-context-shadow-v2",
        input_sources=SOURCES,
        captured_at=NOW,
        source_commit_sha="output-sha",
        max_symbols=24,
    )
    by_source = {row["source"]: row for row in lineage["inputs"]}
    assert by_source["market_regime"]["provider_availability_verified"] is True
    assert lineage["provider_availability_verified_for_all_inputs"] is False
    assert lineage["provider_availability_inferred_from_capture_time"] is False
