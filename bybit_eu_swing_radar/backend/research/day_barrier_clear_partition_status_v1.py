"""Production-facing label-blind partition status for day-barrier-clear-rearm-v1."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from research.day_barrier_clear_partition_v1 import STUDY_ID, freeze_partition


async def load_partition_status(connection: Any) -> dict[str, Any]:
    """Build the frozen 60/40 partition from persisted terminal parent identities.

    Only label-blind terminal metadata is read. No outcome-bearing table or field is
    accessed, and the returned contract keeps outcome/search/promotion closed.
    """
    rows = await connection.fetch(
        """
        SELECT event_key AS event_id, symbol, side,
               LOWER(resolution_status) AS terminal_status, resolved_at
        FROM day_barrier_clear_rearm_v1_parent
        WHERE study=$1
          AND resolution_status IN ('CLEARED','INVALIDATED_BOUNDARY','INVALIDATED_STRUCTURE')
          AND resolved_at IS NOT NULL
        ORDER BY resolved_at,event_key
        """,
        STUDY_ID,
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        resolved_at = row["resolved_at"]
        if isinstance(resolved_at, datetime):
            resolved_at = resolved_at.isoformat()
        events.append({
            "event_id": str(row["event_id"]),
            "symbol": str(row["symbol"]),
            "side": str(row["side"]),
            "terminal_status": str(row["terminal_status"]),
            "resolved_at": str(resolved_at),
        })
    return freeze_partition(events)


__all__ = ["load_partition_status"]
