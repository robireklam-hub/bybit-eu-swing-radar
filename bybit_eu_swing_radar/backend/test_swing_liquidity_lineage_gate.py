from __future__ import annotations

from datetime import timedelta

import pytest

from research import swing_liquidity_lifecycle as lifecycle
from research.research_governance import PIT_VERSION
from research.swing_liquidity_lineage import LINEAGE_FORWARD_START_UTC, evaluate_lineage_capture, spec

TRIAL_ID = "swing-liquidity-validation-v1"
TRIAL_FP = "a" * 64
DQ_FP = "b" * 64


def _row(**overrides):
    row = {
        "captured_at": LINEAGE_FORWARD_START_UTC + timedelta(minutes=1),
        "inserted_at": LINEAGE_FORWARD_START_UTC + timedelta(minutes=2),
        "feature_available_at": LINEAGE_FORWARD_START_UTC + timedelta(minutes=1, seconds=10),
        "provenance_version": PIT_VERSION,
        "trial_id": TRIAL_ID,
        "trial_fingerprint": TRIAL_FP,
        "source_commit_sha": "c" * 40,
        "candidate_count": 7,
        "orderbook_count": 7,
        "orderbook_error_count": 0,
    }
    row.update(overrides)
    return row


def test_lineage_spec_is_frozen_outcome_blind_and_non_promoting():
    payload = spec()
    assert payload["forward_start_utc"] == "2026-08-21T04:53:33+00:00"
    assert payload["historical_backfill_allowed"] is False
    assert payload["outcome_fields_used"] is False
    assert payload["threshold_search_allowed"] is False
    assert payload["live_strategy_mutated"] is False
    assert payload["production_eligibility_mutated"] is False
    assert payload["execution_authorized"] is False


def test_lineage_pass_is_deterministic_and_binds_dq_fingerprint():
    first = evaluate_lineage_capture(_row(), trial_id=TRIAL_ID, trial_fingerprint=TRIAL_FP, data_quality_event_fingerprint=DQ_FP)
    second = evaluate_lineage_capture(_row(), trial_id=TRIAL_ID, trial_fingerprint=TRIAL_FP, data_quality_event_fingerprint=DQ_FP)
    assert first == second
    assert first["ready"] is True
    assert len(first["evidence_fingerprint"]) == 64
    assert len(first["lineage_fingerprint"]) == 64
    assert first["evidence"]["data_quality_event_fingerprint"] == DQ_FP


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"inserted_at": LINEAGE_FORWARD_START_UTC - timedelta(seconds=1)}, "predates_lineage_forward_start"),
        ({"provenance_version": "wrong"}, "provenance_version_mismatch"),
        ({"trial_id": "other"}, "trial_id_mismatch"),
        ({"trial_fingerprint": "d" * 64}, "trial_fingerprint_mismatch"),
        ({"source_commit_sha": "bad"}, "invalid_source_commit_sha"),
        ({"candidate_count": 0, "orderbook_count": 0}, "candidate_count_not_positive"),
        ({"orderbook_count": 6}, "orderbook_coverage_incomplete"),
        ({"orderbook_error_count": 1}, "orderbook_errors_present"),
    ],
)
def test_lineage_fails_closed_on_invalid_evidence(override, expected):
    result = evaluate_lineage_capture(_row(**override), trial_id=TRIAL_ID, trial_fingerprint=TRIAL_FP, data_quality_event_fingerprint=DQ_FP)
    assert result["ready"] is False
    assert expected in result["failures"]


def test_lineage_rejects_invalid_data_quality_event_fingerprint():
    result = evaluate_lineage_capture(_row(), trial_id=TRIAL_ID, trial_fingerprint=TRIAL_FP, data_quality_event_fingerprint="bad")
    assert result["ready"] is False
    assert "invalid_data_quality_event_fingerprint" in result["failures"]


@pytest.mark.asyncio
async def test_data_quality_state_waits_for_fresh_post_prereg_lineage_capture(monkeypatch):
    async def status(*args, **kwargs):
        return {"event_count": 3, "current_event_type": "DATA_QUALITY_GATE_RECORDED"}

    async def evidence(*args, **kwargs):
        return None, DQ_FP

    monkeypatch.setattr(lifecycle, "lifecycle_status", status)
    monkeypatch.setattr(lifecycle, "_load_post_data_quality_lineage_evidence", evidence)
    monkeypatch.setattr(lifecycle, "trial_manifest", lambda study: {"trial_id": TRIAL_ID})
    monkeypatch.setattr(lifecycle, "trial_fingerprint", lambda study: TRIAL_FP)

    result = await lifecycle.record_lifecycle_on_capture_persistence(object(), inserted_capture=True, source_commit_sha="e" * 40)
    assert result["event_type"] == "DATA_QUALITY_GATE_RECORDED"
    assert result["inserted"] is False
    assert result["reason"] == "waiting_for_fresh_post_data_quality_lineage_capture"
    assert result["historical_backfill"] is False


@pytest.mark.asyncio
async def test_fresh_post_dq_capture_records_exact_lineage_event(monkeypatch):
    recorded = {}

    async def status(*args, **kwargs):
        return {"event_count": 3, "current_event_type": "DATA_QUALITY_GATE_RECORDED"}

    async def evidence(*args, **kwargs):
        return _row(), DQ_FP

    async def record(*args, **kwargs):
        recorded.update(kwargs)
        return {"inserted": True, "event_type": "LINEAGE_RECORDED", "event_fingerprint": "f" * 64, "recorded_at": LINEAGE_FORWARD_START_UTC}

    monkeypatch.setattr(lifecycle, "lifecycle_status", status)
    monkeypatch.setattr(lifecycle, "_load_post_data_quality_lineage_evidence", evidence)
    monkeypatch.setattr(lifecycle, "record_trial_event", record)
    monkeypatch.setattr(lifecycle, "trial_manifest", lambda study: {"trial_id": TRIAL_ID})
    monkeypatch.setattr(lifecycle, "trial_fingerprint", lambda study: TRIAL_FP)

    result = await lifecycle.record_lifecycle_on_capture_persistence(object(), inserted_capture=True, source_commit_sha="e" * 40)
    assert result["inserted"] is True
    assert result["event_type"] == "LINEAGE_RECORDED"
    assert result["reason"] == "prospective_lineage_gate"
    assert result["lineage"]["ready"] is True
    assert recorded["event_type"] == "LINEAGE_RECORDED"
    assert recorded["event_payload"]["lineage_verified"] is True
    assert recorded["event_payload"]["historical_backfill"] is False
    assert recorded["event_payload"]["outcome_fields_used"] is False
    assert recorded["event_payload"]["threshold_search_allowed"] is False
