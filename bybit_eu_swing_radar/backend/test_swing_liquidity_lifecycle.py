from __future__ import annotations

import pytest

from research import swing_liquidity_lifecycle as lifecycle
from research.research_governance import trial_fingerprint


@pytest.mark.asyncio
async def test_duplicate_capture_retry_never_advances_lifecycle(monkeypatch):
    calls = []

    async def fail_status(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("duplicate capture must not read or advance lifecycle")

    monkeypatch.setattr(lifecycle, "lifecycle_status", fail_status)
    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=False, source_commit_sha="a" * 40
    )

    assert calls == []
    assert result["attempted"] is False
    assert result["inserted"] is False
    assert result["reason"] == "capture_not_inserted"
    assert result["historical_backfill"] is False
    assert result["live_strategy_mutated"] is False
    assert result["execution_authorized"] is False


@pytest.mark.asyncio
async def test_first_new_capture_records_only_prospective_trial_registration(monkeypatch):
    recorded = {}

    async def empty_status(conn, study, *, entity_type, entity_id):
        assert study == lifecycle.STUDY
        assert entity_type == "TRIAL"
        assert entity_id == lifecycle.STUDY
        return {"event_count": 0, "current_event_type": None}

    async def record_event(conn, study, **kwargs):
        recorded.update(kwargs)
        return {
            "inserted": True,
            "event_type": "TRIAL_REGISTERED",
            "event_fingerprint": "b" * 64,
            "recorded_at": "2026-08-21T00:01:00+00:00",
        }

    monkeypatch.setattr(lifecycle, "lifecycle_status", empty_status)
    monkeypatch.setattr(lifecycle, "record_trial_event", record_event)

    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=True, source_commit_sha="c" * 40
    )

    assert recorded["event_id"] == lifecycle.ADOPTION_EVENT_ID
    assert recorded["event_type"] == "TRIAL_REGISTERED"
    assert recorded["source_commit_sha"] == "c" * 40
    payload = recorded["event_payload"]
    assert payload["trial_registered"] is True
    assert payload["prospective_adoption"] is True
    assert payload["historical_backfill"] is False
    assert payload["evidence_refs"] == [trial_fingerprint(lifecycle.STUDY)]
    assert not any(
        key in payload
        for key in ("outcome", "returns", "net_r", "mfe_r", "mae_r", "oos_payload")
    )
    assert result["inserted"] is True
    assert result["event_type"] == "TRIAL_REGISTERED"
    assert result["historical_backfill"] is False
    assert result["live_strategy_mutated"] is False
    assert result["production_eligibility_mutated"] is False
    assert result["execution_authorized"] is False


@pytest.mark.asyncio
async def test_existing_lifecycle_is_not_reconstructed_or_advanced(monkeypatch):
    async def existing_status(conn, study, *, entity_type, entity_id):
        return {"event_count": 1, "current_event_type": "TRIAL_REGISTERED"}

    async def fail_record(*args, **kwargs):
        raise AssertionError("existing lifecycle must not be advanced by adoption helper")

    monkeypatch.setattr(lifecycle, "lifecycle_status", existing_status)
    monkeypatch.setattr(lifecycle, "record_trial_event", fail_record)

    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=True, source_commit_sha="d" * 40
    )

    assert result["attempted"] is True
    assert result["inserted"] is False
    assert result["event_type"] == "TRIAL_REGISTERED"
    assert result["reason"] == "lifecycle_already_adopted"
    assert result["prospective_adoption"] is True
    assert result["historical_backfill"] is False
