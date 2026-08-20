from datetime import datetime, timedelta, timezone

import pytest

from scripts.production_swing_liquidity_maturity_status import summarize_maturity_payload


def _payload(*, checked_at=None, maturity_offsets_hours=(12, 36, -1)):
    checked = checked_at or datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    events = []
    for index, offset in enumerate(maturity_offsets_hours):
        matures_at = checked + timedelta(hours=offset)
        trigger_close = matures_at - timedelta(days=10)
        events.append(
            {
                "event_id": f"COIN{index}USDC:long:{trigger_close.isoformat()}",
                "symbol": f"COIN{index}USDC",
                "side": "long",
                "trigger_close_at": trigger_close.isoformat(),
                "matures_at": matures_at.isoformat(),
            }
        )
    matured = sum(1 for offset in maturity_offsets_hours if offset <= 0)
    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "checked_at": checked.isoformat(),
        "event_count": len(events),
        "matured_event_count": matured,
        "events": events,
    }


def test_maturity_summary_is_label_blind_and_reports_next_windows():
    summary = summarize_maturity_payload(_payload())

    assert summary["event_count"] == 3
    assert summary["matured_event_count"] == 1
    assert summary["pending_maturity_event_count"] == 2
    assert summary["next_maturity_at"] == "2026-08-20T00:00:00+00:00"
    assert summary["maturities_next_24h"] == 1
    assert summary["maturities_next_72h"] == 2
    assert summary["development_maturity_count_ready"] is False
    assert summary["maturity_contract_verified"] is True
    assert summary["event_identity_uniqueness_verified"] is True
    assert summary["outcome_visible"] is False
    assert summary["promotion_allowed"] is False


def test_maturity_summary_reports_no_pending_events():
    summary = summarize_maturity_payload(_payload(maturity_offsets_hours=(-48, -1)))

    assert summary["matured_event_count"] == 2
    assert summary["pending_maturity_event_count"] == 0
    assert summary["next_maturity_at"] is None
    assert summary["maturities_next_24h"] == 0
    assert summary["maturities_next_72h"] == 0


def test_maturity_summary_rejects_declared_count_mismatches():
    payload = _payload()
    payload["matured_event_count"] = 0
    with pytest.raises(ValueError, match="matured_event_count_mismatch"):
        summarize_maturity_payload(payload)

    payload = _payload()
    payload["event_count"] = 99
    with pytest.raises(ValueError, match="event_count_mismatch"):
        summarize_maturity_payload(payload)


def test_maturity_summary_rejects_payload_maturity_not_frozen_horizon():
    payload = _payload()
    event = payload["events"][0]
    declared = datetime.fromisoformat(event["matures_at"])
    event["matures_at"] = (declared - timedelta(hours=1)).isoformat()

    with pytest.raises(ValueError, match="wrong_maturity_horizon"):
        summarize_maturity_payload(payload)


def test_maturity_summary_rejects_duplicate_event_id():
    payload = _payload()
    payload["events"][1]["event_id"] = payload["events"][0]["event_id"]

    with pytest.raises(ValueError, match="duplicate_event_id"):
        summarize_maturity_payload(payload)


def test_maturity_summary_rejects_duplicate_symbol_side_trigger_bar_even_with_new_id():
    payload = _payload()
    first = payload["events"][0]
    duplicate = dict(first)
    duplicate["event_id"] = "different-id-for-same-trigger"
    payload["events"].append(duplicate)
    payload["event_count"] = len(payload["events"])
    payload["matured_event_count"] += int(
        datetime.fromisoformat(duplicate["matures_at"])
        <= datetime.fromisoformat(payload["checked_at"])
    )

    with pytest.raises(ValueError, match="duplicate_symbol_side_trigger_bar"):
        summarize_maturity_payload(payload)


def test_maturity_summary_fails_closed_if_research_guards_change():
    for field, value in (
        ("research_only", False),
        ("label_blind", False),
        ("outcome_visible", True),
        ("promotion_allowed", True),
    ):
        payload = _payload()
        payload[field] = value
        with pytest.raises(ValueError):
            summarize_maturity_payload(payload)
