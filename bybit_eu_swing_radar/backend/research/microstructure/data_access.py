"""Bounded read-only access to persisted research microstructure buckets.

This module exposes label-free market microstructure observations only. It has no
live strategy/scoring/eligibility/execution mutation path and never reads trade
outcomes or journal labels.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

import asyncpg

MAX_LOOKBACK_MINUTES = 360
MAX_ROWS = 1000
DEFAULT_LOOKBACK_MINUTES = 15
DEFAULT_LIMIT = 240

BUCKET_COLUMNS = (
    "symbol",
    "bucket_start",
    "bucket_seconds",
    "source",
    "trade_count",
    "block_trade_count",
    "rpi_trade_count",
    "taker_buy_base",
    "taker_sell_base",
    "taker_buy_quote",
    "taker_sell_quote",
    "signed_quote_flow",
    "total_quote_volume",
    "trade_vwap",
    "best_bid",
    "best_ask",
    "mid",
    "spread_bps",
    "microprice",
    "bid_depth_5_quote",
    "ask_depth_5_quote",
    "bid_depth_10_quote",
    "ask_depth_10_quote",
    "bid_depth_50_quote",
    "ask_depth_50_quote",
    "imbalance_5",
    "imbalance_10",
    "imbalance_50",
    "bid_added_quote",
    "bid_removed_quote",
    "ask_added_quote",
    "ask_removed_quote",
    "book_message_count",
    "last_trade_at",
    "last_book_at",
    "book_update_id",
    "cross_seq",
    "book_ready",
)

BUCKET_SQL = f"""
SELECT {', '.join(BUCKET_COLUMNS)}
FROM microstructure_buckets
WHERE symbol = $1
  AND bucket_start >= $2
  AND bucket_start < $3
ORDER BY bucket_start DESC
LIMIT $4
"""


def normalize_symbol(symbol: str, allowed_symbols: Iterable[str]) -> str:
    normalized = str(symbol or "").strip().upper()
    allowed = {str(item).strip().upper() for item in allowed_symbols}
    if not normalized.endswith("USDC"):
        raise ValueError("microstructure data access is USDC-only")
    if normalized not in allowed:
        raise ValueError("symbol is not enabled in the microstructure recorder")
    return normalized


def validate_bounds(lookback_minutes: int, limit: int) -> tuple[int, int]:
    lookback = int(lookback_minutes)
    row_limit = int(limit)
    if not 1 <= lookback <= MAX_LOOKBACK_MINUTES:
        raise ValueError(f"lookback_minutes must be between 1 and {MAX_LOOKBACK_MINUTES}")
    if not 1 <= row_limit <= MAX_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_ROWS}")
    return lookback, row_limit


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def normalize_bucket_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {column: _json_value(row.get(column)) for column in BUCKET_COLUMNS}


def _finite_values(rows: list[Mapping[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(field)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _book_pressure_ratio(row: Mapping[str, Any]) -> float | None:
    fields = (
        "bid_added_quote",
        "bid_removed_quote",
        "ask_added_quote",
        "ask_removed_quote",
    )
    try:
        bid_added, bid_removed, ask_added, ask_removed = (
            float(row.get(field) or 0.0) for field in fields
        )
    except (TypeError, ValueError):
        return None
    churn = bid_added + bid_removed + ask_added + ask_removed
    if churn <= 0:
        return None
    return (bid_added + ask_removed - bid_removed - ask_added) / churn


def _microprice_displacement_bps(row: Mapping[str, Any]) -> float | None:
    try:
        mid = float(row.get("mid") or 0.0)
        microprice = float(row.get("microprice") or 0.0)
    except (TypeError, ValueError):
        return None
    if mid <= 0 or microprice <= 0:
        return None
    return (microprice - mid) / mid * 10_000.0


def summarize_bucket_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    spreads = _finite_values(items, "spread_bps")
    imbalance10 = _finite_values(items, "imbalance_10")
    imbalance50 = _finite_values(items, "imbalance_50")
    pressure = [value for value in (_book_pressure_ratio(row) for row in items) if value is not None]
    microprice = [
        value
        for value in (_microprice_displacement_bps(row) for row in items)
        if value is not None
    ]
    row_count = len(items)
    return {
        "row_count": row_count,
        "book_ready_ratio": (
            sum(1 for row in items if bool(row.get("book_ready"))) / row_count
            if row_count else 0.0
        ),
        "trade_bucket_ratio": (
            sum(1 for row in items if int(row.get("trade_count") or 0) > 0) / row_count
            if row_count else 0.0
        ),
        "trade_count": sum(int(row.get("trade_count") or 0) for row in items),
        "book_message_count": sum(int(row.get("book_message_count") or 0) for row in items),
        "total_quote_volume": sum(float(row.get("total_quote_volume") or 0.0) for row in items),
        "signed_quote_flow": sum(float(row.get("signed_quote_flow") or 0.0) for row in items),
        "mean_spread_bps": _mean(spreads),
        "p95_spread_bps": _p95(spreads),
        "mean_imbalance_10": _mean(imbalance10),
        "mean_imbalance_50": _mean(imbalance50),
        "mean_microprice_displacement_bps": _mean(microprice),
        "mean_book_pressure_ratio": _mean(pressure),
    }


def build_bucket_payload(
    symbol: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    lookback_minutes: int,
    limit: int,
    checked_at: datetime,
) -> dict[str, Any]:
    normalized_rows = [normalize_bucket_row(row) for row in rows]
    normalized_rows.sort(key=lambda row: str(row.get("bucket_start") or ""))
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "outcome_fields_read": False,
        "promotion_allowed": False,
        "source_table": "microstructure_buckets",
        "symbol": symbol,
        "lookback_minutes": lookback_minutes,
        "limit": limit,
        "checked_at": checked_at.isoformat(),
        "row_count": len(normalized_rows),
        "first_bucket_at": normalized_rows[0]["bucket_start"] if normalized_rows else None,
        "last_bucket_at": normalized_rows[-1]["bucket_start"] if normalized_rows else None,
        "summary": summarize_bucket_rows(normalized_rows),
        "rows": normalized_rows,
    }


async def load_bucket_rows(
    database_url: str,
    symbol: str,
    since: datetime,
    until: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(BUCKET_SQL, symbol, since, until, limit)
    finally:
        await connection.close()
    return [dict(row) for row in reversed(rows)]


async def load_recent_bucket_payload(
    database_url: str,
    symbol: str,
    allowed_symbols: Iterable[str],
    *,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = normalize_symbol(symbol, allowed_symbols)
    lookback, row_limit = validate_bounds(lookback_minutes, limit)
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    checked_at = checked_at.astimezone(timezone.utc)
    since = checked_at - timedelta(minutes=lookback)
    rows = await load_bucket_rows(database_url, normalized, since, checked_at, row_limit)
    return build_bucket_payload(
        normalized,
        rows,
        lookback_minutes=lookback,
        limit=row_limit,
        checked_at=checked_at,
    )
