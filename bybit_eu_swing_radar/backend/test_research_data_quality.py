from datetime import datetime, timedelta, timezone

import pytest

from research.research_data_quality import (
    CONTRACT_VERSION,
    aggregate_contract_results,
    contract_manifest,
    evaluate_source_record,
    source_contract,
    source_max_age_seconds,
)


NOW = datetime(2026, 8, 19, 10, 30, tzinfo=timezone.utc)


def _record(source: str, *, age_seconds: int = 60, history: bool = True, **overrides):
    contract = source_contract(source)
    captured = NOW - timedelta(seconds=age_seconds)
    payload = {
        "captured_at": captured.isoformat(),
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
    }
    if history:
        payload["immutable_history"] = {
            "immutable": True,
            "research_family": contract["research_family"],
            "spec_version": contract["spec_version"],
            "payload_fingerprint": "abc123",
            "point_in_time_verified": False,
        }
    payload.update(overrides)
    return {
        "captured_at": captured.isoformat(),
        "source_commit_sha": "deadbeef",
        "payload": payload,
    }


def test_manifest_centralizes_existing_cross_layer_freshness_budgets():
    manifest = contract_manifest()
    assert manifest["contract_version"] == CONTRACT_VERSION
    assert manifest["provider_availability_inference"] == "FORBIDDEN_FROM_CAPTURE_TIME_ALONE"
    assert source_max_age_seconds("market_regime") == 3 * 3600
    assert source_max_age_seconds("derivatives_positioning") == 3 * 3600
    assert source_max_age_seconds("event_tokenomics") == 8 * 3600
    assert source_max_age_seconds("btc_macro_cycle_etf") == 8 * 3600
    assert source_max_age_seconds("relative_strength") == 36 * 3600
    assert source_max_age_seconds("sector_rotation") == 36 * 3600
    assert source_max_age_seconds("btc_onchain") == 8 * 3600
    assert source_max_age_seconds("eth_onchain") == 8 * 3600
    assert set(manifest["sources"]) == {
        "market_regime",
        "derivatives_positioning",
        "relative_strength",
        "sector_rotation",
        "event_tokenomics",
        "btc_macro_cycle_etf",
        "btc_onchain",
        "eth_onchain",
    }


def test_fresh_immutable_capture_is_research_usable_but_not_production_proof():
    result = evaluate_source_record("market_regime", _record("market_regime"), observed_at=NOW)
    assert result["status"] == "FRESH"
    assert result["contract_status"] == "WARN"
    assert result["severity"] == "WARNING"
    assert result["research_usable"] is True
    assert result["production_usable"] is False
    assert result["production_severity"] == "PRODUCTION_BLOCK"
    assert result["completeness"]["required_field_coverage"] == "4/4"
    assert result["lineage"]["immutable"] is True
    assert result["lineage"]["point_in_time_verified"] is False
    assert result["lineage"]["provider_availability_verified"] is False
    assert result["lineage"]["availability_semantics"] == "CAPTURE_TIME_ONLY_NOT_PROVIDER_AVAILABILITY"
    assert any("provider availability time is unverified" in item for item in result["warning_reasons"])


def test_provider_availability_must_be_explicit_not_inferred_from_pit_or_history():
    record = _record("btc_onchain")
    record["payload"]["immutable_history"]["point_in_time_verified"] = True
    result = evaluate_source_record("btc_onchain", record, observed_at=NOW)
    assert result["lineage"]["point_in_time_verified"] is True
    assert result["lineage"]["provider_availability_verified"] is False
    assert result["severity"] == "WARNING"
    assert result["production_usable"] is False


def test_stale_source_is_research_blocked_without_mutating_payload():
    max_age = source_max_age_seconds("derivatives_positioning")
    record = _record("derivatives_positioning", age_seconds=max_age + 1)
    result = evaluate_source_record("derivatives_positioning", record, observed_at=NOW)
    assert result["status"] == "STALE"
    assert result["severity"] == "RESEARCH_BLOCK"
    assert result["contract_status"] == "BLOCK"
    assert result["research_usable"] is False
    assert any("freshness budget" in item for item in result["blocking_reasons"])
    assert record["payload"]["live_strategy_mutated"] is False


def test_future_timestamp_and_missing_source_fail_closed_for_research():
    future = _record("relative_strength", age_seconds=-5)
    future_result = evaluate_source_record("relative_strength", future, observed_at=NOW)
    assert future_result["status"] == "FUTURE_REJECTED"
    assert future_result["research_usable"] is False
    assert future_result["severity"] == "RESEARCH_BLOCK"

    missing_result = evaluate_source_record("relative_strength", None, observed_at=NOW)
    assert missing_result["status"] == "MISSING"
    assert missing_result["research_usable"] is False
    assert missing_result["severity"] == "RESEARCH_BLOCK"


def test_contract_invariants_and_required_fields_fail_closed():
    record = _record("event_tokenomics", promotion_allowed=True)
    del record["payload"]["research_only"]
    result = evaluate_source_record("event_tokenomics", record, observed_at=NOW)
    assert result["research_usable"] is False
    assert result["severity"] == "RESEARCH_BLOCK"
    assert "research_only" in result["completeness"]["missing_required_fields"]
    assert any("promotion_allowed must be false" in item for item in result["blocking_reasons"])


def test_legacy_missing_history_is_warning_not_silent_pit_claim():
    result = evaluate_source_record(
        "sector_rotation", _record("sector_rotation", history=False), observed_at=NOW
    )
    assert result["status"] == "FRESH"
    assert result["research_usable"] is True
    assert result["severity"] == "WARNING"
    assert result["lineage"]["immutable_history_present"] is False
    assert result["lineage"]["point_in_time_verified"] is False
    assert result["lineage"]["provider_availability_verified"] is False


def test_lineage_identity_mismatch_is_research_block():
    record = _record("eth_onchain")
    record["payload"]["immutable_history"]["research_family"] = "btc-onchain"
    result = evaluate_source_record("eth_onchain", record, observed_at=NOW)
    assert result["research_usable"] is False
    assert result["severity"] == "RESEARCH_BLOCK"
    assert result["lineage"]["identity_matches_contract"] is False


def test_aggregate_reports_research_and_production_gates_separately():
    rows = {
        "market_regime": evaluate_source_record(
            "market_regime", _record("market_regime"), observed_at=NOW
        ),
        "relative_strength": evaluate_source_record(
            "relative_strength",
            _record(
                "relative_strength",
                age_seconds=source_max_age_seconds("relative_strength") + 1,
            ),
            observed_at=NOW,
        ),
    }
    aggregate = aggregate_contract_results(rows)
    assert aggregate["severity"] == "RESEARCH_BLOCK"
    assert aggregate["research_gate"] == "BLOCK"
    assert aggregate["production_gate"] == "BLOCK"
    assert aggregate["production_severity"] == "PRODUCTION_BLOCK"
    assert aggregate["blocked_sources"] == ["relative_strength"]
    assert aggregate["live_strategy_mutated"] is False
    assert aggregate["production_eligibility_mutated"] is False
    assert aggregate["provider_availability_inferred_from_capture_time"] is False


def test_unknown_source_fails_closed():
    with pytest.raises(ValueError, match="unregistered research data-quality source"):
        source_contract("not-a-source")
