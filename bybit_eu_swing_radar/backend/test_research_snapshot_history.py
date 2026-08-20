from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from research.research_snapshot_history import (
    append_snapshot_history,
    normalize_bucket,
    payload_fingerprint,
    validate_history_identity,
)


class FakeConnection:
    def __init__(self):
        self.rows = {}
        self.execute_calls = []

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        if "INSERT INTO research_snapshot_history" not in sql:
            return "CREATE TABLE"
        family, version, captured, bucket, source_sha, fingerprint, payload = args
        key = (family, version, captured)
        if key in self.rows:
            return "INSERT 0 0"
        self.rows[key] = {
            "payload_fingerprint": fingerprint,
            "payload": payload,
            "capture_bucket": bucket,
            "source_commit_sha": source_sha,
        }
        return "INSERT 0 1"

    async def fetchrow(self, sql, *args):
        family, version, captured = args
        row = self.rows.get((family, version, captured))
        if row is None:
            return None
        return {
            "payload_fingerprint": row["payload_fingerprint"],
            "payload": row["payload"],
        }

    async def fetchval(self, sql, *args):
        if "capture_bucket=$3" in sql:
            family, version, bucket = args
            return sum(
                1
                for (row_family, row_version, _), row in self.rows.items()
                if row_family == family
                and row_version == version
                and row["capture_bucket"] == bucket
            )
        family, version = args
        return sum(
            1
            for row_family, row_version, _ in self.rows
            if row_family == family and row_version == version
        )


def test_payload_fingerprint_is_order_independent():
    assert payload_fingerprint({"b": 2, "a": 1}) == payload_fingerprint({"a": 1, "b": 2})


def test_normalize_daily_bucket_is_midnight_utc():
    assert normalize_bucket(date(2026, 8, 19)).isoformat() == "2026-08-19T00:00:00+00:00"


def test_history_identity_rejects_invalid_family():
    with pytest.raises(ValueError, match="invalid research family"):
        validate_history_identity("BAD FAMILY!", "spec-v1")


@pytest.mark.asyncio
async def test_append_history_preserves_two_same_hour_captures():
    conn = FakeConnection()
    bucket = datetime(2026, 8, 19, 7, 0, tzinfo=timezone.utc)
    first = await append_snapshot_history(
        conn,
        research_family="market-regime",
        spec_version="market-regime-shadow-v1",
        captured_at=datetime(2026, 8, 19, 7, 10, tzinfo=timezone.utc),
        capture_bucket=bucket,
        source_commit_sha="sha1",
        snapshot={"captured_at": "2026-08-19T07:10:00+00:00", "regime": "RANGE"},
    )
    second = await append_snapshot_history(
        conn,
        research_family="market-regime",
        spec_version="market-regime-shadow-v1",
        captured_at=datetime(2026, 8, 19, 7, 45, tzinfo=timezone.utc),
        capture_bucket=bucket,
        source_commit_sha="sha2",
        snapshot={"captured_at": "2026-08-19T07:45:00+00:00", "regime": "TREND"},
    )
    assert first["inserted"] is True
    assert first["bucket_history_count"] == 1
    assert second["inserted"] is True
    assert second["history_count"] == 2
    assert second["bucket_history_count"] == 2
    assert len(conn.rows) == 2


@pytest.mark.asyncio
async def test_exact_retry_is_idempotent():
    conn = FakeConnection()
    captured = datetime(2026, 8, 19, 7, 10, tzinfo=timezone.utc)
    kwargs = dict(
        research_family="btc-onchain",
        spec_version="btc-onchain-shadow-v1",
        captured_at=captured,
        capture_bucket=captured.replace(minute=0, second=0, microsecond=0),
        source_commit_sha="sha1",
        snapshot={"captured_at": captured.isoformat(), "value": 1},
    )
    first = await append_snapshot_history(conn, **kwargs)
    second = await append_snapshot_history(conn, **kwargs)
    assert first["inserted"] is True
    assert second["inserted"] is False
    assert second["history_count"] == 1


@pytest.mark.asyncio
async def test_same_identity_with_different_payload_fails_closed():
    conn = FakeConnection()
    captured = datetime(2026, 8, 19, 7, 10, tzinfo=timezone.utc)
    common = dict(
        research_family="derivatives-positioning",
        spec_version="derivatives-positioning-shadow-v1",
        captured_at=captured,
        capture_bucket=captured.replace(minute=0, second=0, microsecond=0),
        source_commit_sha="sha1",
    )
    await append_snapshot_history(conn, snapshot={"captured_at": captured.isoformat(), "value": 1}, **common)
    with pytest.raises(RuntimeError, match="payload_fingerprint"):
        await append_snapshot_history(
            conn,
            snapshot={"captured_at": captured.isoformat(), "value": 2},
            **common,
        )


@pytest.mark.asyncio
async def test_matching_stored_fingerprint_with_tampered_payload_fails_closed():
    conn = FakeConnection()
    captured = datetime(2026, 8, 19, 7, 10, tzinfo=timezone.utc)
    kwargs = dict(
        research_family="sector-rotation",
        spec_version="sector-rotation-shadow-v1",
        captured_at=captured,
        capture_bucket=captured.date(),
        source_commit_sha="sha1",
        snapshot={"captured_at": captured.isoformat(), "value": 1},
    )
    await append_snapshot_history(conn, **kwargs)
    key = ("sector-rotation", "sector-rotation-shadow-v1", captured)
    conn.rows[key]["payload"] = '{"captured_at":"2026-08-19T07:10:00+00:00","value":999}'
    with pytest.raises(RuntimeError, match="stored_payload_fingerprint"):
        await append_snapshot_history(conn, **kwargs)


@pytest.mark.asyncio
async def test_invalid_stored_payload_fails_closed():
    conn = FakeConnection()
    captured = datetime(2026, 8, 19, 7, 10, tzinfo=timezone.utc)
    kwargs = dict(
        research_family="sector-rotation",
        spec_version="sector-rotation-shadow-v1",
        captured_at=captured,
        capture_bucket=captured.date(),
        source_commit_sha="sha1",
        snapshot={"captured_at": captured.isoformat(), "value": 1},
    )
    await append_snapshot_history(conn, **kwargs)
    key = ("sector-rotation", "sector-rotation-shadow-v1", captured)
    conn.rows[key]["payload"] = "not-json"
    with pytest.raises(Exception):
        await append_snapshot_history(conn, **kwargs)
