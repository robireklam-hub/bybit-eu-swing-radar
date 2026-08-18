from __future__ import annotations

import pytest

import app.microstructure_research as research_api


@pytest.mark.asyncio
async def test_effect_status_does_not_open_outcomes_below_sample_gate(monkeypatch) -> None:
    readiness = {
        "ready_for_forward_feature_analysis": True,
        "checked_at": "2026-08-18T02:00:00+00:00",
        "symbols": [
            {"symbol": "BTCUSDC", "first_bucket_at": "2026-08-16T16:00:00+00:00"},
            {"symbol": "ETHUSDC", "first_bucket_at": "2026-08-16T16:00:00+00:00"},
            {"symbol": "SOLUSDC", "first_bucket_at": "2026-08-16T16:00:00+00:00"},
        ],
    }

    async def fake_readiness(*args, **kwargs):
        return readiness

    async def fake_features(*args, **kwargs):
        return []

    async def fake_counts(*args, **kwargs):
        return {"BTCUSDC": 0, "ETHUSDC": 0, "SOLUSDC": 0}

    async def forbidden_outcomes(*args, **kwargs):
        raise AssertionError("outcome labels must remain inaccessible below sample gate")

    monkeypatch.setattr(research_api, "get_readiness", fake_readiness)
    monkeypatch.setattr(research_api, "load_feature_rows", fake_features)
    monkeypatch.setattr(research_api, "_load_journal_signal_counts", fake_counts)
    monkeypatch.setattr(research_api, "load_closed_outcomes", forbidden_outcomes)

    payload = await research_api.build_effect_status(
        "postgres://unused",
        ("BTCUSDC", "ETHUSDC", "SOLUSDC"),
        5,
    )

    assert payload["status"] == "WAITING_FOR_SAMPLE"
    assert payload["ready_for_preregistered_effect_test"] is False
    assert payload["promotion_allowed"] is False
    assert payload["effect_spec"]["effect_spec_version"] == "microstructure-effect-test-v1"


@pytest.mark.asyncio
async def test_effect_status_does_not_load_features_before_data_quality(monkeypatch) -> None:
    async def fake_readiness(*args, **kwargs):
        return {"ready_for_forward_feature_analysis": False}

    async def forbidden_features(*args, **kwargs):
        raise AssertionError("feature alignment must wait for data quality gate")

    monkeypatch.setattr(research_api, "get_readiness", fake_readiness)
    monkeypatch.setattr(research_api, "load_feature_rows", forbidden_features)

    payload = await research_api.build_effect_status(
        "postgres://unused",
        ("BTCUSDC", "ETHUSDC", "SOLUSDC"),
        5,
    )

    assert payload["status"] == "WAITING_FOR_DATA_QUALITY"
    assert payload["promotion_allowed"] is False
