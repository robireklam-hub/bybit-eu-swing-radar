"""Label-blind swing-liquidity event builder v1.

Builds independent closed-4H trigger events from previously frozen candidate
snapshots. No future outcome, R, MFE/MAE or promotion logic is read here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from research.swing_liquidity_event_contract import (
    close_satisfies_frozen_trigger,
    pretrigger_snapshot_age_seconds,
    safe_event_metadata,
)


def _ts(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def select_pretrigger_snapshot(
    snapshots: Iterable[dict[str, Any]],
    *,
    symbol: str,
    side: str,
    trigger_close_at: datetime | str,
) -> dict[str, Any] | None:
    """Pick the latest matching snapshot strictly before the trigger within 90m."""
    trigger_close = _ts(trigger_close_at)
    matches: list[tuple[datetime, dict[str, Any]]] = []
    for row in snapshots:
        if str(row.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(row.get("side") or "").lower() != side.lower():
            continue
        captured = _ts(row["captured_at"])
        if pretrigger_snapshot_age_seconds(captured, trigger_close) is None:
            continue
        matches.append((captured, row))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def build_first_trigger_event(
    snapshots: Iterable[dict[str, Any]],
    candles: Iterable[dict[str, Any]],
    *,
    symbol: str,
    side: str,
) -> dict[str, Any] | None:
    """Return the first chronological eligible closed-4H trigger event.

    Each candle must provide start_at, close_at and close. The candidate stored in
    the chosen pre-trigger snapshot is authoritative and remains label-blind.
    """
    ordered = sorted(candles, key=lambda row: _ts(row["close_at"]))
    for candle in ordered:
        start_at = _ts(candle["start_at"])
        close_at = _ts(candle["close_at"])
        if close_at <= start_at:
            continue
        snapshot = select_pretrigger_snapshot(
            snapshots,
            symbol=symbol,
            side=side,
            trigger_close_at=close_at,
        )
        if snapshot is None:
            continue
        candidate = snapshot.get("candidate")
        if not isinstance(candidate, dict):
            continue
        if not close_satisfies_frozen_trigger(candidate, candle.get("close")):
            continue
        metadata = safe_event_metadata(
            candidate,
            captured_at=snapshot["captured_at"],
            trigger_bar_start_at=start_at,
            trigger_close_at=close_at,
        )
        metadata["event_id"] = f"{metadata['symbol']}:{metadata['side']}:{metadata['trigger_close_at']}"
        metadata["source_capture_at"] = metadata["pretrigger_captured_at"]
        return metadata
    return None
