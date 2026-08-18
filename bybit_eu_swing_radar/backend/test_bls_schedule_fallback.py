from research.bls_schedule_fallback import embedded_bls_2026_events


def test_embedded_bls_schedule_contains_key_forward_releases() -> None:
    events = embedded_bls_2026_events()
    by_id = {event["event_id"]: event for event in events}
    assert "bls:fallback:MACRO_JOLTS:2026-09-01" in by_id
    assert "bls:fallback:MACRO_JOBS:2026-09-04" in by_id
    assert "bls:fallback:MACRO_PPI:2026-09-10" in by_id
    assert "bls:fallback:MACRO_CPI:2026-09-11" in by_id
    assert all(event["metadata"]["network_live"] is False for event in events)
    assert all(event["source"]["official"] is True for event in events)
