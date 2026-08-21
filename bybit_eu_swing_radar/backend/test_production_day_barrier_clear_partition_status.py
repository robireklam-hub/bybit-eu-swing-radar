from scripts.production_day_barrier_clear_partition_status import validate_partition_observer


def _payload():
    return {
        "cumulative": {
            "pending": 0,
            "cleared": 1,
            "invalidated_boundary": 0,
            "invalidated_structure": 0,
        },
        "partition": {
            "study": "day-barrier-clear-rearm-v1",
            "partition_spec_version": "day-barrier-clear-partition-v1",
            "research_only": True,
            "label_blind_partition": True,
            "outcome_fields_used": False,
            "development_target": 60,
            "validation_target": 40,
            "terminal_event_count": 1,
            "development_partition_ready": False,
            "development_analysis_eligible": False,
            "development_event_ids": [],
            "development_partition_fingerprint": None,
            "validation_partition_ready": False,
            "validation_event_ids": [],
            "validation_partition_fingerprint": None,
            "outcome_visible": False,
            "threshold_search_allowed": False,
            "promotion_allowed": False,
            "live_strategy_mutation": False,
            "execution_authorized": False,
        },
    }


def test_valid_predevelopment_partition_status_passes():
    result = validate_partition_observer(_payload())
    assert result["ok"] is True
    assert result["terminal_count"] == 1


def test_terminal_count_drift_fails_closed():
    payload = _payload()
    payload["partition"]["terminal_event_count"] = 0
    result = validate_partition_observer(payload)
    assert result["ok"] is False
    assert "partition terminal count mismatch" in result["errors"]


def test_partial_development_identity_leak_fails_closed():
    payload = _payload()
    payload["partition"]["development_event_ids"] = ["event-1"]
    result = validate_partition_observer(payload)
    assert result["ok"] is False
    assert "partial development partition leaked" in result["errors"]


def test_outcome_or_promotion_firewall_drift_fails_closed():
    payload = _payload()
    payload["partition"]["outcome_visible"] = True
    payload["partition"]["promotion_allowed"] = True
    result = validate_partition_observer(payload)
    assert result["ok"] is False
    assert "partition.outcome_visible mismatch" in result["errors"]
    assert "partition.promotion_allowed mismatch" in result["errors"]
