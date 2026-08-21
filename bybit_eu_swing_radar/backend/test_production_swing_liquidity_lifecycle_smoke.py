from __future__ import annotations

from scripts.production_swing_liquidity_lifecycle_smoke import run_check, validate_lifecycle_persistence


def _result(**lifecycle_overrides):
    lifecycle = {
        "attempted": True,
        "inserted": False,
        "event_type": "PIT_AUDIT_RECORDED",
        "reason": "insufficient_consecutive_post_pit_captures",
        "prospective_adoption": True,
        "historical_backfill": False,
        "research_only": True,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
        "data_quality": {"ready": False, "capture_count": 1, "required_capture_count": 3},
    }
    lifecycle.update(lifecycle_overrides)
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": "swing-liquidity-validation-v1",
        "captured_at": "2026-08-21T05:00:00+00:00",
        "inserted": True,
        "candidate_count": 3,
        "orderbook_count": 3,
        "orderbook_error_count": 0,
        "lifecycle_adoption": lifecycle,
    }


def test_validate_accepts_waiting_post_pit_data_quality_state():
    assert validate_lifecycle_persistence(_result()) == []


def test_validate_accepts_fresh_pit_transition_with_evidence_fingerprint():
    payload = _result(inserted=True, event_type="PIT_AUDIT_RECORDED", reason="prospective_pit_audit", evidence_capture_fingerprint="a" * 64, data_quality=None)
    assert validate_lifecycle_persistence(payload) == []


def test_validate_accepts_fresh_data_quality_transition_with_frozen_evidence():
    payload = _result(inserted=True, event_type="DATA_QUALITY_GATE_RECORDED", reason="prospective_data_quality_gate", data_quality={"ready": True, "evidence_window_fingerprint": "b" * 64})
    assert validate_lifecycle_persistence(payload) == []


def test_validate_accepts_waiting_post_data_quality_lineage_state():
    payload = _result(inserted=False, event_type="DATA_QUALITY_GATE_RECORDED", reason="waiting_for_fresh_post_data_quality_lineage_capture", data_quality=None)
    assert validate_lifecycle_persistence(payload) == []


def test_validate_accepts_fresh_lineage_transition_with_fingerprints():
    payload = _result(
        inserted=True,
        event_type="LINEAGE_RECORDED",
        reason="prospective_lineage_gate",
        data_quality=None,
        lineage={"ready": True, "evidence_fingerprint": "c" * 64, "lineage_fingerprint": "d" * 64},
    )
    assert validate_lifecycle_persistence(payload) == []


def test_validate_accepts_already_recorded_lineage_state():
    payload = _result(inserted=False, event_type="LINEAGE_RECORDED", reason="lifecycle_already_beyond_lineage_adoption", data_quality=None)
    assert validate_lifecycle_persistence(payload) == []


def test_validate_rejects_trial_only_or_premature_development_state():
    assert any("unexpected_current_lifecycle_event" in error for error in validate_lifecycle_persistence(_result(event_type="TRIAL_REGISTERED")))
    assert any("unexpected_current_lifecycle_event" in error for error in validate_lifecycle_persistence(_result(event_type="DEVELOPMENT_EVIDENCE_RECORDED")))


def test_validate_rejects_backfill_live_mutation_and_execution():
    errors = validate_lifecycle_persistence(_result(historical_backfill=True, live_strategy_mutated=True, production_eligibility_mutated=True, execution_authorized=True))
    assert "lifecycle_historical_backfill_invalid" in errors
    assert "lifecycle_live_strategy_mutated_invalid" in errors
    assert "lifecycle_production_eligibility_mutated_invalid" in errors
    assert "lifecycle_execution_authorized_invalid" in errors


def test_validate_rejects_invalid_fresh_pit_evidence_fingerprint():
    errors = validate_lifecycle_persistence(_result(inserted=True, reason="prospective_pit_audit", evidence_capture_fingerprint="bad", data_quality=None))
    assert "invalid_evidence_capture_fingerprint" in errors


def test_validate_rejects_claimed_data_quality_transition_without_ready_evidence():
    errors = validate_lifecycle_persistence(_result(inserted=True, event_type="DATA_QUALITY_GATE_RECORDED", reason="prospective_data_quality_gate", data_quality={"ready": False}))
    assert "data_quality_evidence_not_ready" in errors


def test_validate_rejects_claimed_lineage_transition_without_valid_fingerprints():
    errors = validate_lifecycle_persistence(_result(inserted=True, event_type="LINEAGE_RECORDED", reason="prospective_lineage_gate", data_quality=None, lineage={"ready": True, "evidence_fingerprint": "bad", "lineage_fingerprint": "bad"}))
    assert "invalid_lineage_evidence_fingerprint" in errors
    assert "invalid_lineage_fingerprint" in errors


def test_run_check_is_deterministic_and_uses_fresh_capture_path():
    calls = []

    def collect(base_url, api_key):
        calls.append(("collect", base_url, api_key))
        return {"captured_at": "2026-08-21T05:00:00+00:00", "candidate_count": 3}

    def persist(base_url, api_key, snapshot):
        calls.append(("persist", base_url, api_key, snapshot["captured_at"]))
        return _result()

    assert run_check("https://example.invalid", "secret", collect=collect, persist=persist) == 0
    assert calls == [
        ("collect", "https://example.invalid", "secret"),
        ("persist", "https://example.invalid", "secret", "2026-08-21T05:00:00+00:00"),
    ]
