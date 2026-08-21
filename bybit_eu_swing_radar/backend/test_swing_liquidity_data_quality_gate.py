from __future__ import annotations

import pytest

from research import swing_liquidity_lifecycle as lifecycle
from research.research_governance import PIT_VERSION, trial_fingerprint
from research.swing_liquidity_data_quality import (
    DATA_QUALITY_SPEC_VERSION,
    MIN_CONSECUTIVE_CAPTURES,
    evaluate_capture_rows,
)


def _row(index: int, **overrides):
    row = {
        "captured_at": f"2026-08-21T04:0{index}:00+00:00",
        "inserted_at": f"2026-08-21T04:0{index}:05+00:00",
        "feature_available_at": f"2026-08-21T04:0{index}:00+00:00",
        "provenance_version": PIT_VERSION,
        "source_commit_sha": "a" * 40,
        "candidate_count": 5,
        "orderbook_count": 5,
        "orderbook_error_count": 0,
    }
    row.update(overrides)
    return row


def test_data_quality_requires_three_consecutive_post_pit_captures():
    result = evaluate_capture_rows([_row(1), _row(2)])
    assert result["ready"] is False
    assert result["reason"] == "insufficient_consecutive_post_pit_captures"
    assert result["required_capture_count"] == MIN_CONSECUTIVE_CAPTURES == 3


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"orderbook_error_count": 1}, "orderbook_errors_present"),
        ({"orderbook_count": 4}, "orderbook_coverage_incomplete"),
        ({"candidate_count": 0, "orderbook_count": 0}, "candidate_count_not_positive"),
        ({"provenance_version": "wrong"}, "provenance_version_mismatch"),
        ({"feature_available_at": None}, "feature_available_at_missing"),
    ],
)
def test_data_quality_fails_closed_on_bad_capture(override, expected):
    rows = [_row(1), _row(2), _row(3, **override)]
    result = evaluate_capture_rows(rows)
    assert result["ready"] is False
    assert any(expected in failure for failure in result["failures"])


def test_data_quality_pass_is_label_blind_and_deterministic():
    rows = [_row(1), _row(2), _row(3)]
    first = evaluate_capture_rows(rows)
    second = evaluate_capture_rows(rows)
    assert first == second
    assert first["ready"] is True
    assert len(first["evidence_fingerprints"]) == 3
    assert len(first["evidence_window_fingerprint"]) == 64
    assert first["spec"]["outcome_fields_used"] is False
    assert first["spec"]["threshold_search_allowed"] is False
    assert first["spec"]["live_strategy_mutated"] is False


@pytest.mark.asyncio
async def test_pit_state_waits_when_data_quality_sample_is_not_ready(monkeypatch):
    async def status(*args, **kwargs):
        return {"event_count": 2, "current_event_type": "PIT_AUDIT_RECORDED"}

    async def rows(*args, **kwargs):
        return [_row(1), _row(2)]

    async def fail_record(*args, **kwargs):
        raise AssertionError("data-quality event must not be recorded before the frozen gate passes")

    monkeypatch.setattr(lifecycle, "lifecycle_status", status)
    monkeypatch.setattr(lifecycle, "_load_post_pit_data_quality_rows", rows)
    monkeypatch.setattr(lifecycle, "record_trial_event", fail_record)

    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=True, source_commit_sha="b" * 40
    )
    assert result["inserted"] is False
    assert result["event_type"] == "PIT_AUDIT_RECORDED"
    assert result["reason"] == "insufficient_consecutive_post_pit_captures"
    assert result["data_quality"]["ready"] is False
    assert result["live_strategy_mutated"] is False


@pytest.mark.asyncio
async def test_three_good_post_pit_captures_record_exact_data_quality_event(monkeypatch):
    recorded = {}

    async def status(*args, **kwargs):
        return {"event_count": 2, "current_event_type": "PIT_AUDIT_RECORDED"}

    async def rows(*args, **kwargs):
        return [_row(1), _row(2), _row(3)]

    async def record_event(conn, study, **kwargs):
        recorded.update(kwargs)
        return {
            "inserted": True,
            "event_type": "DATA_QUALITY_GATE_RECORDED",
            "event_fingerprint": "c" * 64,
            "recorded_at": "2026-08-21T04:04:00+00:00",
        }

    monkeypatch.setattr(lifecycle, "lifecycle_status", status)
    monkeypatch.setattr(lifecycle, "_load_post_pit_data_quality_rows", rows)
    monkeypatch.setattr(lifecycle, "record_trial_event", record_event)

    result = await lifecycle.record_lifecycle_on_capture_persistence(
        object(), inserted_capture=True, source_commit_sha="d" * 40
    )

    assert recorded["event_id"] == lifecycle.DATA_QUALITY_EVENT_ID
    assert recorded["event_type"] == "DATA_QUALITY_GATE_RECORDED"
    payload = recorded["event_payload"]
    assert payload["data_quality_gate_passed"] is True
    assert payload["data_quality_spec_version"] == DATA_QUALITY_SPEC_VERSION
    assert payload["consecutive_capture_count"] == 3
    assert payload["required_consecutive_capture_count"] == 3
    assert payload["full_orderbook_coverage"] is True
    assert payload["orderbook_errors_allowed_per_capture"] == 0
    assert payload["outcome_fields_used"] is False
    assert payload["threshold_search_allowed"] is False
    assert payload["historical_backfill"] is False
    assert payload["evidence_refs"][0] == trial_fingerprint(lifecycle.STUDY)
    assert len(payload["evidence_refs"]) == 4
    assert not any(key in payload for key in ("outcome", "returns", "net_r", "mfe_r", "mae_r", "oos_payload"))
    assert result["inserted"] is True
    assert result["event_type"] == "DATA_QUALITY_GATE_RECORDED"
    assert result["reason"] == "prospective_data_quality_gate"
    assert result["data_quality"]["ready"] is True
    assert result["live_strategy_mutated"] is False
    assert result["production_eligibility_mutated"] is False
    assert result["execution_authorized"] is False
