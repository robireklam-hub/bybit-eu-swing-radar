"""Research-only event and tokenomics intelligence v1.

This module normalizes forward-looking and recently-observed catalysts. It never
changes live strategy scores, eligibility, execution, entries, stops, targets,
or trade decisions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SPEC_VERSION = "event-tokenomics-shadow-v1"
EVENT_TYPES = {
    "MACRO_CPI",
    "MACRO_JOBS",
    "MACRO_PPI",
    "MACRO_JOLTS",
    "MACRO_ECI",
    "MACRO_FOMC_DECISION",
    "MACRO_FOMC_MINUTES",
    "EXCHANGE_LISTING",
    "EXCHANGE_DELISTING",
    "EXCHANGE_MAINTENANCE",
    "PROTOCOL_UPGRADE",
    "PROTOCOL_RELEASE",
    "TOKEN_UNLOCK",
    "TOKEN_BURN",
    "TOKEN_BUYBACK",
    "TOKEN_DISTRIBUTION",
    "REGULATORY_EVENT",
    "SECURITY_EVENT",
    "OTHER",
}
SEVERITIES = ("LOW", "MEDIUM", "MEDIUM_HIGH", "HIGH", "CRITICAL")
WINDOWS = ("PAST_24H", "NEXT_24H", "NEXT_3D", "NEXT_7D", "NEXT_30D", "OUTSIDE_WINDOW")


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "event_types": sorted(EVENT_TYPES),
        "windows": list(WINDOWS),
        "principles": [
            "events are descriptive context, never a trade signal by themselves",
            "source coverage is explicit; missing provider credentials are not interpreted as zero events",
            "estimated dates remain marked estimated",
            "token unlock size may affect severity but never eligibility or execution",
            "no outcome, journal, net-R, or post-trade labels are read",
        ],
    }


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def event_window(event_at: Any, captured_at: datetime) -> str:
    dt = parse_datetime(event_at)
    if dt is None:
        return "OUTSIDE_WINDOW"
    delta = (dt - captured_at.astimezone(timezone.utc)).total_seconds()
    if -86400 <= delta < 0:
        return "PAST_24H"
    if 0 <= delta < 86400:
        return "NEXT_24H"
    if 86400 <= delta < 3 * 86400:
        return "NEXT_3D"
    if 3 * 86400 <= delta < 7 * 86400:
        return "NEXT_7D"
    if 7 * 86400 <= delta <= 30 * 86400:
        return "NEXT_30D"
    return "OUTSIDE_WINDOW"


def severity_from_impact(impact: float | None) -> str:
    if impact is None:
        return "MEDIUM"
    if impact >= 9.0:
        return "CRITICAL"
    if impact >= 8.0:
        return "HIGH"
    if impact >= 6.5:
        return "MEDIUM_HIGH"
    if impact >= 5.0:
        return "MEDIUM"
    return "LOW"


def severity_from_unlock_pct_market_cap(value: float | None) -> str:
    if value is None:
        return "MEDIUM"
    if value >= 5.0:
        return "CRITICAL"
    if value >= 2.0:
        return "HIGH"
    if value >= 0.5:
        return "MEDIUM_HIGH"
    return "MEDIUM"


def normalize_event(event: Mapping[str, Any], *, captured_at: datetime) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "OTHER").upper()
    if event_type not in EVENT_TYPES:
        event_type = "OTHER"
    severity = str(event.get("severity") or "MEDIUM").upper()
    if severity not in SEVERITIES:
        severity = "MEDIUM"
    symbols = sorted({str(item).upper() for item in (event.get("symbols") or []) if str(item).upper().endswith("USDC")})
    event_at = parse_datetime(event.get("event_at"))
    return {
        "event_id": str(event.get("event_id") or "").strip(),
        "event_type": event_type,
        "title": str(event.get("title") or "").strip(),
        "event_at": event_at.isoformat() if event_at else None,
        "display_date": event.get("display_date"),
        "date_precision": str(event.get("date_precision") or "EXACT").upper(),
        "is_estimated": bool(event.get("is_estimated")),
        "severity": severity,
        "symbols": symbols,
        "scope": "SYMBOL" if symbols else "GLOBAL",
        "window": event_window(event_at, captured_at),
        "source": dict(event.get("source") or {}),
        "tokenomics": dict(event.get("tokenomics") or {}),
        "metadata": dict(event.get("metadata") or {}),
    }


def build_snapshot(
    events: Iterable[Mapping[str, Any]],
    source_status: Mapping[str, Mapping[str, Any]],
    tracked_symbols: Iterable[str],
    *,
    captured_at: datetime | None = None,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    now = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    dedup: dict[str, dict[str, Any]] = {}
    for raw in events:
        item = normalize_event(raw, captured_at=now)
        event_id = item["event_id"]
        if not event_id:
            continue
        # Keep the last observation for a duplicated stable provider ID.
        dedup[event_id] = item

    normalized = sorted(
        dedup.values(),
        key=lambda item: (item.get("event_at") or "9999", item["event_id"]),
    )
    active = [item for item in normalized if item["window"] != "OUTSIDE_WINDOW"]
    tracked = sorted({str(symbol).upper() for symbol in tracked_symbols if str(symbol).upper().endswith("USDC")})

    type_counts = {event_type: 0 for event_type in sorted(EVENT_TYPES)}
    severity_counts = {severity: 0 for severity in SEVERITIES}
    window_counts = {window: 0 for window in WINDOWS}
    symbol_counts = {symbol: 0 for symbol in tracked}
    for item in active:
        type_counts[item["event_type"]] += 1
        severity_counts[item["severity"]] += 1
        window_counts[item["window"]] += 1
        for symbol in item["symbols"]:
            if symbol in symbol_counts:
                symbol_counts[symbol] += 1

    statuses = {name: dict(value) for name, value in source_status.items()}
    live_sources = sorted(name for name, value in statuses.items() if str(value.get("status")) in {"LIVE", "PARTIAL"})
    missing_key_sources = sorted(name for name, value in statuses.items() if str(value.get("status")) == "MISSING_KEY")
    error_sources = sorted(name for name, value in statuses.items() if str(value.get("status")) == "ERROR")

    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec": spec(),
        "captured_at": now.isoformat(),
        "source_commit_sha": source_commit_sha,
        "tracked_symbols": tracked,
        "coverage": {
            "source_status": statuses,
            "live_sources": live_sources,
            "missing_key_sources": missing_key_sources,
            "error_sources": error_sources,
            "tracked_symbol_count": len(tracked),
        },
        "event_count": len(active),
        "event_type_counts": {key: value for key, value in type_counts.items() if value},
        "severity_counts": {key: value for key, value in severity_counts.items() if value},
        "window_counts": {key: value for key, value in window_counts.items() if value},
        "symbol_event_counts": {key: value for key, value in symbol_counts.items() if value},
        "events": active,
    }
