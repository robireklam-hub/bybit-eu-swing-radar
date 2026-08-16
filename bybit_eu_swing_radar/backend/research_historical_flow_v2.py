"""Historical derivatives enrichment for Trading Radar research dataset v2.

Research only. This module never changes live strategy/scoring/execution and never
uses derivatives context as a hard gate. It provides point-in-time OI/funding
features that can be joined to already-materialized spot opportunities.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

ANALYSIS_VERSION = "day-trade-historical-flow-v2"
DEFAULT_MAX_OI_AGE_SECONDS = 90 * 60
DEFAULT_MAX_FUNDING_AGE_SECONDS = 12 * 60 * 60


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timestamp_seconds(value: Any) -> int | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            raw = float(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            if not math.isfinite(raw):
                return None
            return int(raw / 1000.0) if raw > 10_000_000_000 else int(raw)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        raw = float(value)
        if not math.isfinite(raw):
            return None
        return int(raw / 1000.0) if raw > 10_000_000_000 else int(raw)
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


@dataclass(frozen=True)
class OIPoint:
    ts: int
    value: float


@dataclass(frozen=True)
class FundingPoint:
    ts: int
    rate: float


def normalize_bybit_oi(rows: list[dict[str, Any]]) -> list[OIPoint]:
    points: dict[int, OIPoint] = {}
    for row in rows:
        ts = _timestamp_seconds(row.get("timestamp"))
        value = _number(row.get("openInterest"))
        if ts is None or value is None or value < 0:
            continue
        points[ts] = OIPoint(ts=ts, value=value)
    return [points[key] for key in sorted(points)]


def normalize_bybit_funding(rows: list[dict[str, Any]]) -> list[FundingPoint]:
    points: dict[int, FundingPoint] = {}
    for row in rows:
        ts = _timestamp_seconds(row.get("fundingRateTimestamp"))
        rate = _number(row.get("fundingRate"))
        if ts is None or rate is None:
            continue
        points[ts] = FundingPoint(ts=ts, rate=rate)
    return [points[key] for key in sorted(points)]


def _latest_index_at_or_before(timestamps: list[int], target_ts: int) -> int | None:
    index = bisect.bisect_right(timestamps, target_ts) - 1
    return index if index >= 0 else None


def _value_at_or_before_oi(points: list[OIPoint], target_ts: int, max_age_seconds: int) -> OIPoint | None:
    if not points:
        return None
    timestamps = [point.ts for point in points]
    index = _latest_index_at_or_before(timestamps, target_ts)
    if index is None:
        return None
    point = points[index]
    return point if target_ts - point.ts <= max_age_seconds else None


def _value_at_or_before_funding(
    points: list[FundingPoint], target_ts: int, max_age_seconds: int
) -> FundingPoint | None:
    if not points:
        return None
    timestamps = [point.ts for point in points]
    index = _latest_index_at_or_before(timestamps, target_ts)
    if index is None:
        return None
    point = points[index]
    return point if target_ts - point.ts <= max_age_seconds else None


def _pct_change(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def enrich_opportunity(
    row: dict[str, Any],
    *,
    derivative_symbol: str | None,
    oi_points: list[OIPoint],
    funding_points: list[FundingPoint],
    max_oi_age_seconds: int = DEFAULT_MAX_OI_AGE_SECONDS,
    max_funding_age_seconds: int = DEFAULT_MAX_FUNDING_AGE_SECONDS,
) -> dict[str, Any]:
    """Return a copy of one opportunity with strictly backward-looking flow features."""
    enriched = dict(row)
    opened_ts = _timestamp_seconds(row.get("opened_at"))
    enriched.update(
        {
            "derivative_symbol": derivative_symbol,
            "historical_flow_available": False,
            "oi_value": None,
            "oi_age_seconds": None,
            "oi_change_1h_pct": None,
            "oi_change_4h_pct": None,
            "funding_rate": None,
            "funding_age_seconds": None,
        }
    )
    if opened_ts is None or not derivative_symbol:
        return enriched

    oi = _value_at_or_before_oi(oi_points, opened_ts, max_oi_age_seconds)
    funding = _value_at_or_before_funding(funding_points, opened_ts, max_funding_age_seconds)

    if oi is not None:
        oi_1h = _value_at_or_before_oi(oi_points, opened_ts - 3600, max_oi_age_seconds)
        oi_4h = _value_at_or_before_oi(oi_points, opened_ts - 4 * 3600, max_oi_age_seconds)
        enriched["oi_value"] = oi.value
        enriched["oi_age_seconds"] = opened_ts - oi.ts
        enriched["oi_change_1h_pct"] = _pct_change(oi.value, oi_1h.value if oi_1h else None)
        enriched["oi_change_4h_pct"] = _pct_change(oi.value, oi_4h.value if oi_4h else None)

    if funding is not None:
        enriched["funding_rate"] = funding.rate
        enriched["funding_age_seconds"] = opened_ts - funding.ts

    enriched["historical_flow_available"] = oi is not None or funding is not None
    return enriched


def coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    oi_count = sum(row.get("oi_value") is not None for row in rows)
    funding_count = sum(row.get("funding_rate") is not None for row in rows)
    both_count = sum(
        row.get("oi_value") is not None and row.get("funding_rate") is not None
        for row in rows
    )
    return {
        "analysis_version": ANALYSIS_VERSION,
        "research_only": True,
        "hard_gate_filtering": False,
        "rows": total,
        "oi_rows": oi_count,
        "funding_rows": funding_count,
        "both_rows": both_count,
        "oi_coverage_pct": round(oi_count / total * 100.0, 3) if total else 0.0,
        "funding_coverage_pct": round(funding_count / total * 100.0, 3) if total else 0.0,
        "both_coverage_pct": round(both_count / total * 100.0, 3) if total else 0.0,
        "warnings": [
            "Historical derivatives data is contextual research enrichment only and never a hard eligibility gate.",
            "Point-in-time joins use only observations timestamped at or before the spot opportunity.",
            "Historical shorts remain technical research only and do not establish Bybit EU spot-margin borrowability.",
            "No live day_worker strategy, scoring, trigger, eligibility or execution logic is modified.",
        ],
    }
