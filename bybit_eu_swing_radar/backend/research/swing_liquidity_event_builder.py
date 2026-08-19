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


def _available_at(row: dict[str, Any]) -> datetime:
    """Use feature availability when present; legacy rows fall back to capture time."""
    return _ts(row.get("available_at") or row.get("feature_available_at") or row["captured_at"])


def select_pretrigger_snapshot(
    snapshots: Iterable[dict[str, Any]],
    *,
    symbol: str,
    side: str,
    trigger_close_at: datetime | str,
) -> dict[str, Any] | None:
    """Pick the latest matching snapshot available strictly before trigger within 90m."""
    trigger_close = _ts(trigger_close_at)
    matches: list[tuple[datetime, dict[str, Any]]] = []
    for row in snapshots:
        if str(row.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(row.get("side") or "").lower() != side.lower():
            continue
        available = _available_at(row)
        if pretrigger_snapshot_age_seconds(available, trigger_close) is None:
            continue
        matches.append((available, row))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def build_trigger_events(
    snapshots: Iterable[dict[str, Any]],
    candles: Iterable[dict[str, Any]],
    *,
    symbol: str,
    side: str,
) -> list[dict[str, Any]]:
    """Return unique eligible first-trigger-bar events in chronological order.

    Repeated hourly snapshots are covariates. For PIT-v1 rows, pre-trigger means
    feature availability rather than collection start. Historical rows retain
    capture-time fallback and are surfaced as unverified provenance.
    """
    snapshot_rows = list(snapshots)
    ordered = sorted(candles, key=lambda row: _ts(row["close_at"]))
    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candle in ordered:
        start_at = _ts(candle["start_at"])
        close_at = _ts(candle["close_at"])
        if close_at <= start_at:
            continue
        snapshot = select_pretrigger_snapshot(
            snapshot_rows,
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
        available_at = _available_at(snapshot)
        metadata = safe_event_metadata(
            candidate,
            captured_at=available_at,
            trigger_bar_start_at=start_at,
            trigger_close_at=close_at,
        )
        metadata["pretrigger_available_at"] = metadata["pretrigger_captured_at"]
        metadata["pretrigger_captured_at"] = _ts(snapshot["captured_at"]).isoformat()
        metadata["point_in_time_verified"] = bool(snapshot.get("point_in_time_verified", False))
        metadata["provenance_version"] = snapshot.get("provenance_version") or "legacy-captured-at-v0"
        metadata["trial_id"] = snapshot.get("trial_id")
        metadata["trial_fingerprint"] = snapshot.get("trial_fingerprint")
        event_id = f"{metadata['symbol']}:{metadata['side']}:{metadata['trigger_close_at']}"
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        metadata["event_id"] = event_id
        metadata["source_capture_at"] = metadata["pretrigger_captured_at"]
        events.append(metadata)
    return events


def build_first_trigger_event(
    snapshots: Iterable[dict[str, Any]],
    candles: Iterable[dict[str, Any]],
    *,
    symbol: str,
    side: str,
) -> dict[str, Any] | None:
    """Compatibility helper returning the first chronological trigger event."""
    events = build_trigger_events(snapshots, candles, symbol=symbol, side=side)
    return events[0] if events else None
