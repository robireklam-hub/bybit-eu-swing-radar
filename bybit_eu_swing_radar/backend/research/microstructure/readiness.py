"""Deterministic data-quality gate for untouched forward microstructure research.

This module only evaluates the research recorder output. It does not alter live
strategy, scoring, eligibility or execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import asyncpg

MIN_DURATION_HOURS = 24.0
MIN_CONTINUITY_RATIO = 0.90
MIN_BOOK_READY_RATIO = 0.95
MIN_BOOK_MESSAGE_RATIO = 0.95
MAX_FRESHNESS_SECONDS = 30.0

READINESS_SQL = """
SELECT
    symbol,
    COUNT(*)::bigint AS bucket_count,
    MIN(bucket_start) AS first_bucket_at,
    MAX(bucket_start) AS last_bucket_at,
    COUNT(*) FILTER (WHERE book_ready)::bigint AS book_ready_count,
    COUNT(*) FILTER (WHERE book_message_count > 0)::bigint AS book_message_bucket_count,
    COUNT(*) FILTER (WHERE trade_count > 0)::bigint AS trade_bucket_count,
    COALESCE(SUM(trade_count), 0)::bigint AS trade_count,
    COALESCE(SUM(book_message_count), 0)::bigint AS book_message_count
FROM microstructure_buckets
WHERE symbol = ANY($1::text[])
GROUP BY symbol
ORDER BY symbol
"""


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    raise TypeError(f"unsupported datetime value: {type(value).__name__}")


def summarize_readiness(
    rows: Iterable[Mapping[str, Any]],
    symbols: Iterable[str],
    bucket_seconds: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a fail-closed, preregistered readiness report.

    Twenty-four hours is only a data-quality gate. Passing it does *not* imply
    statistical edge or permission to promote any feature into live trading.
    """
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    by_symbol = {str(row["symbol"]).upper(): row for row in rows}
    reports: list[dict[str, Any]] = []

    for symbol in wanted:
        row = by_symbol.get(symbol)
        if row is None:
            reports.append({
                "symbol": symbol,
                "ready": False,
                "reasons": ["no_buckets"],
                "bucket_count": 0,
                "duration_hours": 0.0,
                "continuity_ratio": 0.0,
                "book_ready_ratio": 0.0,
                "book_message_ratio": 0.0,
                "trade_bucket_ratio": 0.0,
                "freshness_seconds": None,
                "first_bucket_at": None,
                "last_bucket_at": None,
            })
            continue

        first_at = _as_utc(row.get("first_bucket_at"))
        last_at = _as_utc(row.get("last_bucket_at"))
        count = int(row.get("bucket_count") or 0)
        span_seconds = max(0.0, (last_at - first_at).total_seconds()) if first_at and last_at else 0.0
        duration_hours = span_seconds / 3600.0
        expected = max(1, int(span_seconds // bucket_seconds) + 1)
        continuity = min(1.0, count / expected)
        ready_ratio = (int(row.get("book_ready_count") or 0) / count) if count else 0.0
        book_message_ratio = (int(row.get("book_message_bucket_count") or 0) / count) if count else 0.0
        trade_bucket_ratio = (int(row.get("trade_bucket_count") or 0) / count) if count else 0.0
        freshness = max(0.0, (now_utc - last_at).total_seconds()) if last_at else None

        reasons: list[str] = []
        if duration_hours < MIN_DURATION_HOURS:
            reasons.append("insufficient_duration")
        if continuity < MIN_CONTINUITY_RATIO:
            reasons.append("insufficient_continuity")
        if ready_ratio < MIN_BOOK_READY_RATIO:
            reasons.append("insufficient_book_ready_coverage")
        if book_message_ratio < MIN_BOOK_MESSAGE_RATIO:
            reasons.append("insufficient_book_message_coverage")
        if freshness is None or freshness > MAX_FRESHNESS_SECONDS:
            reasons.append("stale_or_missing_latest_bucket")

        reports.append({
            "symbol": symbol,
            "ready": not reasons,
            "reasons": reasons,
            "bucket_count": count,
            "duration_hours": round(duration_hours, 6),
            "continuity_ratio": round(continuity, 6),
            "book_ready_ratio": round(ready_ratio, 6),
            "book_message_ratio": round(book_message_ratio, 6),
            "trade_bucket_ratio": round(trade_bucket_ratio, 6),
            "trade_count": int(row.get("trade_count") or 0),
            "book_message_count": int(row.get("book_message_count") or 0),
            "freshness_seconds": round(freshness, 3) if freshness is not None else None,
            "first_bucket_at": _iso(first_at),
            "last_bucket_at": _iso(last_at),
        })

    ready = bool(reports) and all(item["ready"] for item in reports)
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "gate_version": "microstructure-readiness-v1",
        "ready_for_forward_feature_analysis": ready,
        "promotion_allowed": False,
        "promotion_note": "Readiness is a data-quality gate only; feature promotion still requires prespecified out-of-sample evidence.",
        "thresholds": {
            "min_duration_hours": MIN_DURATION_HOURS,
            "min_continuity_ratio": MIN_CONTINUITY_RATIO,
            "min_book_ready_ratio": MIN_BOOK_READY_RATIO,
            "min_book_message_ratio": MIN_BOOK_MESSAGE_RATIO,
            "max_freshness_seconds": MAX_FRESHNESS_SECONDS,
        },
        "bucket_seconds": bucket_seconds,
        "symbols": reports,
        "checked_at": now_utc.isoformat(),
    }


async def get_readiness(
    database_url: str,
    symbols: Iterable[str],
    bucket_seconds: int,
) -> dict[str, Any]:
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    if not database_url:
        return {
            "research_only": True,
            "live_strategy_mutated": False,
            "gate_version": "microstructure-readiness-v1",
            "ready_for_forward_feature_analysis": False,
            "promotion_allowed": False,
            "error": "DATABASE_URL is not configured",
            "symbols": [],
        }
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(READINESS_SQL, list(wanted))
    finally:
        await connection.close()
    return summarize_readiness(rows, wanted, bucket_seconds)
