from __future__ import annotations

import pytest

from research import swing_liquidity_lifecycle as lifecycle
from research.research_governance import PIT_VERSION, trial_fingerprint
from research.research_lifecycle_ledger import canonical_fingerprint


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

    async def fail_pit(*args, **kwargs):
        raise AssertionError("first adoption capture must not also backfill PIT")

    monkeypatch.setattr(lifecycle, "lifecycle_status", empty_status)
    monkeypatch.setattr(lifecycle, "record_trial_event", record_event)
    monkeypatch.setattr(lifecycle, "_load_post_adoption_pit_evidence", fail_pit)

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
async def test_second_fresh_pit_capture_records_only_pit_audit(monkeypatch):
    recorded = {}
    evidence = {
        "captured_at": "2026-08-21 01:10:00+00:00",
        "inserted_at": "2026-08-21 01:10:05+00:00",
        "feature_available_at": "2026-08-21 01:09:59+00:00",
        "provenance_version": PIT_VERSION,
        "trial_id": lifecycle.STUDY,
        "trial_fingerprint": trial_fingerprint(lifecycle.STUDY),
        "source_commit_sha": "d" * 40,
    }

    async def adopted_status(conn, study, *, entity_type, entity_id):
        return {"event_count": 1, "current_event_type": "TRIAL_REGISTERED"}

    async def load_evidence(conn, *, trial_id, trial_fp):
        assert trial_id == lifecycle.STUDY
        assert trial_fp == trial_fingerprint(lifecycle.STUDY)
        return dict(evidence)

    async def record_event(conn, study, **kwargs):
        recorded.update(kwargs)
        return {
            "inserted": True,
            "event_type": "PIT_AUDIT_RECORDED",
            "event_fingerprint": "e" * 64,
            "recorded_at": "2026-08-21T01:10:06+00:00",
        }

    monkeypatch.setattr(lifecycle, "lifecycle_status", adopted_status)
    monkeypatch.setattr(lifecycle, "_load_post_adoption_pit_evidence", load_evidence)
    monkeypatch.setattr(lifecycle, "record_trial_event", record_event)

    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=True, source_commit_sha="f" * 40
    )

    assert recorded["event_id"] == lifecycle.PIT_AUDIT_EVENT_ID
    assert recorded["event_type"] == "PIT_AUDIT_RECORDED"
    payload = recorded["event_payload"]
    evidence_fp = canonical_fingerprint(evidence)
    assert payload["point_in_time_verified"] is True
    assert payload["provenance_version"] == PIT_VERSION
    assert payload["evidence_refs"] == [trial_fingerprint(lifecycle.STUDY), evidence_fp]
    assert payload["evidence_capture_fingerprint"] == evidence_fp
    assert payload["historical_backfill"] is False
    assert not any(
        key in payload
        for key in ("outcome", "returns", "net_r", "mfe_r", "mae_r", "oos_payload")
    )
    assert result["inserted"] is True
    assert result["event_type"] == "PIT_AUDIT_RECORDED"
    assert result["reason"] == "prospective_pit_audit"
    assert result["live_strategy_mutated"] is False
    assert result["execution_authorized"] is False


@pytest.mark.asyncio
async def test_no_fresh_post_adoption_pit_capture_does_not_advance(monkeypatch):
    async def adopted_status(conn, study, *, entity_type, entity_id):
        return {"event_count": 1, "current_event_type": "TRIAL_REGISTERED"}

    async def no_evidence(*args, **kwargs):
        return None

    async def fail_record(*args, **kwargs):
        raise AssertionError("missing prospective PIT evidence must not advance lifecycle")

    monkeypatch.setattr(lifecycle, "lifecycle_status", adopted_status)
    monkeypatch.setattr(lifecycle, "_load_post_adoption_pit_evidence", no_evidence)
    monkeypatch.setattr(lifecycle, "record_trial_event", fail_record)

    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=True, source_commit_sha="1" * 40
    )

    assert result["inserted"] is False
    assert result["event_type"] == "TRIAL_REGISTERED"
    assert result["reason"] == "waiting_for_fresh_post_adoption_pit_capture"
    assert result["historical_backfill"] is False


@pytest.mark.asyncio
async def test_existing_lifecycle_beyond_pit_is_not_reconstructed_or_advanced(monkeypatch):
    async def existing_status(conn, study, *, entity_type, entity_id):
        return {"event_count": 2, "current_event_type": "PIT_AUDIT_RECORDED"}

    async def fail_record(*args, **kwargs):
        raise AssertionError("helper must not advance beyond PIT audit")

    async def fail_load(*args, **kwargs):
        raise AssertionError("PIT evidence must not be re-read after PIT audit")

    monkeypatch.setattr(lifecycle, "lifecycle_status", existing_status)
    monkeypatch.setattr(lifecycle, "record_trial_event", fail_record)
    monkeypatch.setattr(lifecycle, "_load_post_adoption_pit_evidence", fail_load)

    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=True, source_commit_sha="2" * 40
    )

    assert result["attempted"] is True
    assert result["inserted"] is False
    assert result["event_type"] == "PIT_AUDIT_RECORDED"
    assert result["reason"] == "lifecycle_already_beyond_pit_adoption"
    assert result["prospective_adoption"] is True
    assert result["historical_backfill"] is False
