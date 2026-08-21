from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from research.day_barrier_clear_partition_status_v1 import load_partition_status


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows


def _rows(count: int):
    start = datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        rows.append({
            "event_id": f"event-{index:03d}",
            "symbol": f"TEST{index % 5}USDC",
            "side": "long" if index % 2 == 0 else "short",
            "terminal_status": "cleared" if index % 2 == 0 else "invalidated_boundary",
            "resolved_at": start + timedelta(minutes=5 * index),
        })
    return rows


def test_loader_uses_only_terminal_identity_query_and_preserves_closed_firewall():
    connection = FakeConnection(_rows(60))
    result = asyncio.run(load_partition_status(connection))
    assert result["terminal_event_count"] == 60
    assert result["development_partition_ready"] is True
    assert len(result["development_event_ids"]) == 60
    assert result["validation_partition_ready"] is False
    assert result["outcome_visible"] is False
    assert result["threshold_search_allowed"] is False
    assert result["promotion_allowed"] is False
    assert len(connection.calls) == 1
    query, args = connection.calls[0]
    lowered = query.lower()
    assert "day_barrier_clear_rearm_v1_parent" in lowered
    assert "resolution_status in" in lowered
    assert "outcome" not in lowered
    assert "pnl" not in lowered
    assert args == ("day-barrier-clear-rearm-v1",)


def test_loader_does_not_freeze_partial_development_sample():
    result = asyncio.run(load_partition_status(FakeConnection(_rows(59))))
    assert result["terminal_event_count"] == 59
    assert result["development_partition_ready"] is False
    assert result["development_event_ids"] == []
    assert result["development_partition_fingerprint"] is None
