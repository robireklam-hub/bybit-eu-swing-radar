from __future__ import annotations

import pytest

from app import research_swing_liquidity_api as api


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, capture_inserted: bool):
        self.capture_inserted = capture_inserted
        self.closed = False

    def transaction(self):
        return _Transaction()

    async def execute(self, sql, *args):
        if "INSERT INTO swing_liquidity_forward_captures" in sql:
            return "INSERT 0 1" if self.capture_inserted else "INSERT 0 0"
        return "INSERT 0 1"

    async def close(self):
        self.closed = True


def _snapshot():
    return {
        "study": api.STUDY,
        "research_only": True,
        "label_blind": True,
        "live_gate_unchanged": True,
        "captured_at": "2026-08-21T00:10:00+00:00",
        "scan_data_as_of": "2026-08-21T00:09:00+00:00",
        "candidate_count": 1,
        "candidates": [
            {
                "symbol": "ALTUSDC",
                "side": "long",
                "source_section": "liquidity_blocked",
                "shortable": False,
                "turnover_tier": "50K_100K",
                "spread_tier": "10_20",
            }
        ],
        "orderbooks": {},
        "orderbook_errors": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_inserted", [True, False])
async def test_persistence_passes_exact_capture_insert_state_to_lifecycle(monkeypatch, capture_inserted):
    conn = _FakeConn(capture_inserted)
    lifecycle_calls = []

    async def connect(_database_url):
        return conn

    async def ensure_registered(*args, **kwargs):
        return {"immutable": True}

    async def lifecycle_hook(_conn, *, inserted_capture, source_commit_sha=None):
        lifecycle_calls.append((inserted_capture, source_commit_sha))
        return {
            "attempted": inserted_capture,
            "inserted": inserted_capture,
            "historical_backfill": False,
            "research_only": True,
            "live_strategy_mutated": False,
        }

    monkeypatch.setattr(api.asyncpg, "connect", connect)
    monkeypatch.setattr(api, "ensure_trial_registered", ensure_registered)
    monkeypatch.setattr(api, "record_lifecycle_on_capture_persistence", lifecycle_hook)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "e" * 40)

    result = await api.persist_forward_snapshot(_snapshot())

    assert result["inserted"] is capture_inserted
    assert lifecycle_calls == [(capture_inserted, "e" * 40)]
    assert result["lifecycle_adoption"]["attempted"] is capture_inserted
    assert result["lifecycle_adoption"]["historical_backfill"] is False
    assert conn.closed is True
