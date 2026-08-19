from datetime import datetime, timedelta, timezone

from scripts.production_swing_liquidity_event_smoke import validate_event_payload


def _event():
    trigger = datetime(2026, 8, 18, 20, tzinfo=timezone.utc)
    captured = trigger - timedelta(minutes=30)
    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "symbol": "BTCUSDC",
        "side": "long",
        "pretrigger_captured_at": captured.isoformat(),
        "pretrigger_snapshot_age_seconds": 1800.0,
        "trigger_bar_start_at": (trigger - timedelta(hours=4)).isoformat(),
        "trigger_close_at": trigger.isoformat(),
        "matures_at": (trigger + timedelta(days=10)).isoformat(),
        "trigger_price": 100.0,
        "entry_midpoint": 101.0,
        "stop": 95.0,
        "tp2": 116.0,
        "event_id": "BTCUSDC:long:2026-08-18T20:00:00+00:00",
        "source_capture_at": captured.isoformat(),
    }


def _payload(events=None):
    events = [_event()] if events is None else events
    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": "swing-liquidity-validation-v1",
        "builder_version": "swing-liquidity-event-builder-v1",
        "event_identity": "symbol_side_first_qualifying_4h_trigger_bar",
        "checked_at": datetime(2026, 8, 19, tzinfo=timezone.utc).isoformat(),
        "durable_snapshot_rows": 12,
        "symbol_count": 3,
        "kline_symbol_count": 3,
        "kline_errors": {},
        "event_count": len(events),
        "matured_event_count": 0,
        "events": events,
    }


def test_event_smoke_accepts_label_blind_complete_payload():
    assert validate_event_payload(_payload()) == []


def test_event_smoke_allows_zero_events_but_requires_real_input_coverage():
    assert validate_event_payload(_payload(events=[])) == []


def test_event_smoke_rejects_missing_kline_coverage_and_outcome_leakage():
    payload = _payload()
    payload["kline_symbol_count"] = 2
    payload["kline_errors"] = {"SOLUSDC": "TimeoutError"}
    payload["events"][0]["net_r"] = 1.2

    failures = validate_event_payload(payload)

    assert "incomplete_kline_coverage:2/3" in failures
    assert "kline_errors_present:SOLUSDC" in failures
    assert any("forbidden_keys:net_r" in failure for failure in failures)


def test_event_smoke_rejects_wrong_maturity_horizon():
    payload = _payload()
    trigger = datetime.fromisoformat(payload["events"][0]["trigger_close_at"])
    payload["events"][0]["matures_at"] = (trigger + timedelta(days=9)).isoformat()

    assert any("wrong_maturity_horizon" in failure for failure in validate_event_payload(payload))


def test_event_smoke_allows_distinct_trigger_bars_for_same_symbol_side():
    first = _event()
    second = dict(first)
    later_trigger = datetime.fromisoformat(first["trigger_close_at"]) + timedelta(hours=4)
    second["trigger_bar_start_at"] = (later_trigger - timedelta(hours=4)).isoformat()
    second["trigger_close_at"] = later_trigger.isoformat()
    second["matures_at"] = (later_trigger + timedelta(days=10)).isoformat()
    second["pretrigger_captured_at"] = (later_trigger - timedelta(minutes=30)).isoformat()
    second["source_capture_at"] = second["pretrigger_captured_at"]
    second["event_id"] = f"BTCUSDC:long:{later_trigger.isoformat()}"

    assert validate_event_payload(_payload(events=[first, second])) == []


def test_event_smoke_rejects_duplicate_symbol_side_trigger_bar():
    first = _event()
    duplicate = dict(first)
    duplicate["event_id"] = "different-id-but-same-trigger-bar"

    failures = validate_event_payload(_payload(events=[first, duplicate]))

    assert any("duplicate_symbol_side_trigger_bar" in failure for failure in failures)
