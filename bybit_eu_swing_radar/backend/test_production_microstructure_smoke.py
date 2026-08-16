from __future__ import annotations

from scripts.production_microstructure_smoke import healthy, readiness_healthy, run_smoke


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


def _readiness(**overrides):
    symbols = []
    for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC"):
        symbols.append({
            "symbol": symbol,
            "ready": False,
            "reasons": ["insufficient_duration"],
            "bucket_count": 100,
            "duration_hours": 0.2,
            "continuity_ratio": 0.99,
            "book_ready_ratio": 1.0,
            "book_message_ratio": 1.0,
            "trade_bucket_ratio": 0.3,
            "freshness_seconds": 5.0,
            "first_bucket_at": "2026-08-16T15:48:00+00:00",
            "last_bucket_at": "2026-08-16T16:00:00+00:00",
        })
    payload = {
        "research_only": True,
        "live_strategy_mutated": False,
        "gate_version": "microstructure-readiness-v1",
        "ready_for_forward_feature_analysis": False,
        "promotion_allowed": False,
        "thresholds": {"min_duration_hours": 24.0},
        "bucket_seconds": 5,
        "symbols": symbols,
        "checked_at": "2026-08-16T16:00:05+00:00",
    }
    payload.update(overrides)
    return payload


def test_healthy_requires_real_messages_and_rows():
    assert healthy(_healthy_status()) == (True, "ok")
    assert healthy(_healthy_status(messages=0))[1] == "no_websocket_messages"
    assert healthy(_healthy_status(rows_written=0))[1] == "no_database_rows_written"
    assert healthy(_healthy_status(connected=False))[1] == "websocket_not_connected"


def test_readiness_contract_does_not_require_24h_gate_to_have_passed():
    symbols = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
    assert readiness_healthy(_readiness(), symbols) == (True, "ok")
    assert readiness_healthy(_readiness(promotion_allowed=True), symbols)[1] == "readiness_promotion_allowed_not_false"
    assert readiness_healthy(_readiness(error="db failed"), symbols)[1] == "readiness_query_error"


def test_smoke_polls_until_recorder_writes_rows_then_checks_readiness():
    responses = iter([
        {"commit_sha": "abc"},
        _healthy_status(rows_written=0),
        _healthy_status(rows_written=2),
        _readiness(),
    ])

    def fetch(url, api_key, timeout):
        return next(responses)

    sleeps = []
    result = run_smoke("https://example.test", "secret", "abc", fetch=fetch, sleep=sleeps.append)
    assert result == 0
    assert sleeps == [5]


def test_smoke_fails_closed_on_readiness_query_error():
    responses = iter([
        {"commit_sha": "abc"},
        _healthy_status(),
        _readiness(error="database unavailable"),
    ])

    def fetch(url, api_key, timeout):
        return next(responses)

    result = run_smoke("https://example.test", "secret", "abc", fetch=fetch, sleep=lambda _: None)
    assert result == 1


def test_smoke_fails_closed_on_wrong_deployment_sha():
    def fetch(url, api_key, timeout):
        return {"commit_sha": "wrong"}

    result = run_smoke("https://example.test", "secret", "abc", fetch=fetch, sleep=lambda _: None)
    assert result == 1
