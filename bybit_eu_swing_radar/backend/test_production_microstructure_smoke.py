from __future__ import annotations

from scripts.production_microstructure_smoke import healthy, run_smoke


def _healthy_status(**overrides):
    payload = {
        "research_only": True,
        "live_strategy_mutated": False,
        "enabled": True,
        "running": True,
        "singleton_acquired": True,
        "connected": True,
        "symbols": ["BTCUSDC", "ETHUSDC", "SOLUSDC"],
        "messages": 100,
        "rows_written": 10,
        "last_message_at": "2026-08-16T16:00:00+00:00",
        "last_write_at": "2026-08-16T16:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_healthy_requires_real_messages_and_rows():
    assert healthy(_healthy_status()) == (True, "ok")
    assert healthy(_healthy_status(messages=0))[1] == "no_websocket_messages"
    assert healthy(_healthy_status(rows_written=0))[1] == "no_database_rows_written"
    assert healthy(_healthy_status(connected=False))[1] == "websocket_not_connected"


def test_smoke_polls_until_recorder_writes_rows():
    responses = iter([
        {"commit_sha": "abc"},
        _healthy_status(rows_written=0),
        _healthy_status(rows_written=2),
    ])

    def fetch(url, api_key, timeout):
        return next(responses)

    sleeps = []
    result = run_smoke("https://example.test", "secret", "abc", fetch=fetch, sleep=sleeps.append)
    assert result == 0
    assert sleeps == [5]


def test_smoke_fails_closed_on_wrong_deployment_sha():
    def fetch(url, api_key, timeout):
        return {"commit_sha": "wrong"}

    result = run_smoke("https://example.test", "secret", "abc", fetch=fetch, sleep=lambda _: None)
    assert result == 1
