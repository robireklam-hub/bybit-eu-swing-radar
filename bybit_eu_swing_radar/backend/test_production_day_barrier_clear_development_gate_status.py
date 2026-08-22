from scripts.production_day_barrier_clear_development_gate_status import (
    validate_development_gate_observer,
)


def _payload():
    development_ids = [f"event-{index:03d}" for index in range(60)]
    return {
        "cumulative": {
            "pending": 3,
            "cleared": 48,
            "invalidated_boundary": 38,
            "invalidated_structure": 2,
        },
        "partition": {
            "study": "day-barrier-clear-rearm-v1",
            "partition_spec_version": "day-barrier-clear-partition-v1",
            "research_only": True,
            "label_blind_partition": True,
            "outcome_fields_used": False,
            "development_target": 60,
            "validation_target": 40,
            "terminal_event_count": 88,
            "development_partition_ready": True,
            "development_analysis_eligible": True,
            "development_event_ids": development_ids,
            "development_partition_fingerprint": "f" * 64,
            "development_boundary": {
                "resolved_at": "2026-08-22T06:00:00+00:00",
                "event_id": development_ids[-1],
            },
            "development_balance": {
                "cleared": 37,
                "noncleared": 23,
                "long": 30,
                "short": 30,
                "minimum_cleared": 15,
                "minimum_noncleared": 15,
                "minimum_long": 10,
                "minimum_short": 10,
            },
            "outcome_visible": False,
            "threshold_search_allowed": False,
            "promotion_allowed": False,
            "live_strategy_mutation": False,
            "execution_authorized": False,
        },
    }


def test_balanced_fixed_first_60_authorizes_development_outcome_opening_only():
    result = validate_development_gate_observer(_payload())
    assert result["ok"] is True
    assert result["verification_ok"] is True
    assert result["opening_blockers"] == []
    assert result["outcome_fields_read"] is False
    assert result["development_event_count"] == 60
    assert result["development_outcome_opening_authorized"] is True
    assert result["validation_outcome_opening_authorized"] is False
    assert result["threshold_search_allowed"] is False
    assert result["promotion_allowed"] is False
    assert result["live_strategy_mutation"] is False
    assert result["execution_authorized"] is False


def test_failed_preregistered_balance_is_verified_but_opening_remains_closed():
    payload = _payload()
    payload["partition"]["development_analysis_eligible"] = False
    payload["partition"]["development_balance"]["short"] = 4

    result = validate_development_gate_observer(payload)

    assert result["ok"] is True
    assert result["verification_ok"] is True
    assert result["errors"] == []
    assert result["development_outcome_opening_authorized"] is False
    assert result["opening_blockers"] == ["development balance failed: short"]
    assert result["development_partition_fingerprint"] == "f" * 64
    assert result["development_boundary"]["event_id"] == "event-059"


def test_analysis_eligibility_drift_fails_verification():
    payload = _payload()
    payload["partition"]["development_balance"]["short"] = 4
    # Reported eligible=True is inconsistent with the preregistered balance.

    result = validate_development_gate_observer(payload)

    assert result["ok"] is False
    assert result["development_outcome_opening_authorized"] is False
    assert "development analysis eligibility mismatch" in result["errors"]
    assert "development balance failed: short" in result["opening_blockers"]


def test_missing_fixed_identity_or_boundary_fails_verification():
    payload = _payload()
    payload["partition"]["development_event_ids"] = payload["partition"]["development_event_ids"][:-1]
    payload["partition"]["development_partition_fingerprint"] = None
    payload["partition"]["development_boundary"] = None

    result = validate_development_gate_observer(payload)

    assert result["ok"] is False
    assert result["development_outcome_opening_authorized"] is False
    assert "development event identity count mismatch" in result["errors"]
    assert "development fingerprint missing" in result["errors"]
    assert "development composite boundary missing" in result["errors"]


def test_firewall_drift_fails_verification():
    payload = _payload()
    payload["partition"]["outcome_visible"] = True
    payload["partition"]["promotion_allowed"] = True
    payload["partition"]["execution_authorized"] = True

    result = validate_development_gate_observer(payload)

    assert result["ok"] is False
    assert result["development_outcome_opening_authorized"] is False
    assert "partition.outcome_visible mismatch" in result["errors"]
    assert "partition.promotion_allowed mismatch" in result["errors"]
    assert "partition.execution_authorized mismatch" in result["errors"]


def test_terminal_count_drift_fails_verification():
    payload = _payload()
    payload["partition"]["terminal_event_count"] = 87

    result = validate_development_gate_observer(payload)

    assert result["ok"] is False
    assert "partition terminal count mismatch" in result["errors"]
