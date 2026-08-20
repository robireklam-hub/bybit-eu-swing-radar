from __future__ import annotations

from datetime import datetime, timezone

from scripts.production_microstructure_smoke import (
    healthy,
    prospective_runtime_healthy,
    readiness_healthy,
    run_smoke,
)


def _healthy_status(**overrides):
    now = datetime.now(timezone.utc).isoformat()
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
        "last_message_at": now,
        "last_write_at": now,
        "process_role": "standalone",
        "external_service_healthy": True,
        "source_commit_sha": "abc",
        "heartbeat_age_seconds": 4.0,
        "controlled_pullback_v2": {
            "status": "ok",
            "last_cycle_at": now,
            "bucket_rows": 100,
            "candidate_records": 0,
            "inserted_records": 0,
            "duplicate_records": 0,
            "runtime": {
                "research_only": True,
                "label_blind": True,
                "outcome_fields_read": False,
                "outcome_visible": False,
                "promotion_allowed": False,
                "live_strategy_mutation": False,
                "connection_policy": "DEDICATED_RESEARCH_DB_CONNECTION",
            },
        },
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


def test_prospective_runtime_requires_exact_external_sha_and_label_blind_cycle():
    now = datetime.now(timezone.utc)
    assert prospective_runtime_healthy(_healthy_status(), "abc", now=now) == (True, "ok")
    assert prospective_runtime_healthy(_healthy_status(source_commit_sha="old"), "abc", now=now)[1] == "external_recorder_sha_mismatch"
    assert prospective_runtime_healthy(
        _healthy_status(controlled_pullback_v2={"status": "degraded"}), "abc", now=now
    )[1] == "prospective_runtime_not_ok"

    contaminated = _healthy_status()
    contaminated["controlled_pullback_v2"]["runtime"]["outcome_visible"] = True
    assert prospective_runtime_healthy(contaminated, "abc", now=now)[1] == "prospective_guard_changed"


def test_monitor_mode_allows_healthy_older_recorder_sha_but_requires_identity():
    now = datetime.now(timezone.utc)
    older = _healthy_status(source_commit_sha="recorder-relevant-old-sha")
    assert prospective_runtime_healthy(
        older,
        "current-api-main-sha",
        require_exact_recorder_sha=False,
        now=now,
    ) == (True, "ok")

    missing = _healthy_status(source_commit_sha="")
    assert prospective_runtime_healthy(
        missing,
        "current-api-main-sha",
        require_exact_recorder_sha=False,
        now=now,
    )[1] == "external_recorder_sha_missing"


def test_prospective_runtime_allows_zero_events_but_requires_real_bucket_cycle():
    now = datetime.now(timezone.utc)
    status = _healthy_status()
    assert status["controlled_pullback_v2"]["candidate_records"] == 0
    assert prospective_runtime_healthy(status, "abc", now=now) == (True, "ok")

    no_buckets = _healthy_status()
    no_buckets["controlled_pullback_v2"]["bucket_rows"] = 0
    assert prospective_runtime_healthy(no_buckets, "abc", now=now)[1] == "prospective_no_bucket_rows"


def test_readiness_contract_does_not_require_24h_gate_to_have_passed():
    symbols = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
    assert readiness_healthy(_readiness(), symbols) == (True, "ok")
    assert readiness_healthy(_readiness(promotion_allowed=True), symbols)[1] == "readiness_promotion_allowed_not_false"
    assert readiness_healthy(_readiness(error="db failed"), symbols)[1] == "readiness_query_error"


def test_smoke_polls_until_exact_external_recorder_and_prospective_cycle_then_checks_readiness():
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


def test_smoke_fails_closed_on_old_external_recorder_even_when_api_sha_matches():
    responses = iter([{"commit_sha": "abc"}] + [_healthy_status(source_commit_sha="old")] * 24)

    def fetch(url, api_key, timeout):
        return next(responses)

    result = run_smoke("https://example.test", "secret", "abc", fetch=fetch, sleep=lambda _: None)
    assert result == 1


def test_monitor_smoke_accepts_older_healthy_recorder_without_weakening_api_sha():
    responses = iter([
        {"commit_sha": "api-current"},
        _healthy_status(source_commit_sha="recorder-last-relevant"),
        _readiness(),
    ])

    def fetch(url, api_key, timeout):
        return next(responses)

    result = run_smoke(
        "https://example.test",
        "secret",
        "api-current",
        require_exact_recorder_sha=False,
        fetch=fetch,
        sleep=lambda _: None,
    )
    assert result == 0


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
