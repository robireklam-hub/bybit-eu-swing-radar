"""Outcome-blind feature adapter for controlled-pullback calibration v1.

Research-only. Converts persisted 5-second microstructure buckets into the exact
feature rows accepted by controlled_pullback_calibration_v1. No journal labels,
future returns, trade outcomes, live ranking, eligibility, or execution state are
read or mutated here.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from research.microstructure.controlled_pullback_v1 import SYMBOLS

FEATURE_ADAPTER_ID = "microstructure-controlled-pullback-feature-adapter-v1"
BUCKET_SECONDS = 5
MOMENTUM_LOOKBACK_SECONDS = 60
MOMENTUM_LOOKBACK_BUCKETS = MOMENTUM_LOOKBACK_SECONDS // BUCKET_SECONDS


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("bucket_start must be an ISO-8601 string or datetime")
    if result.tzinfo is None:
        raise ValueError("bucket_start must be timezone-aware")
    return result.astimezone(timezone.utc)


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _book_pressure_abs(row: Mapping[str, Any]) -> float | None:
    bid_added = _finite(row.get("bid_added_quote", 0.0) or 0.0, "bid_added_quote")
    bid_removed = _finite(row.get("bid_removed_quote", 0.0) or 0.0, "bid_removed_quote")
    ask_added = _finite(row.get("ask_added_quote", 0.0) or 0.0, "ask_added_quote")
    ask_removed = _finite(row.get("ask_removed_quote", 0.0) or 0.0, "ask_removed_quote")
    churn = bid_added + bid_removed + ask_added + ask_removed
    if churn <= 0:
        return None
    pressure = (bid_added + ask_removed - bid_removed - ask_added) / churn
    return abs(pressure)


def _aggressive_flow_share_abs(row: Mapping[str, Any]) -> float | None:
    signed_quote_flow = _finite(row.get("signed_quote_flow", 0.0) or 0.0, "signed_quote_flow")
    total_quote_volume = _finite(row.get("total_quote_volume", 0.0) or 0.0, "total_quote_volume")
    if total_quote_volume <= 0:
        return None
    return min(1.0, abs(signed_quote_flow) / total_quote_volume)


def derive_calibration_feature_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    allowed_symbols: Iterable[str] = SYMBOLS,
) -> list[dict[str, Any]]:
    """Build label-free calibration rows from persisted 5-second buckets.

    A feature row is emitted only when an exact 60-second predecessor bucket is
    present for the same symbol and all required contemporaneous fields are valid.
    Missing/gapped observations are skipped rather than interpolated, preventing
    synthetic continuity from entering the calibration sample.
    """
    allowed = {str(symbol).upper() for symbol in allowed_symbols}
    normalized: list[dict[str, Any]] = []
    for raw in rows:
        symbol = str(raw.get("symbol", "")).upper()
        if symbol not in allowed:
            raise ValueError(f"unexpected feature symbol: {symbol or '<missing>'}")
        bucket_seconds = int(raw.get("bucket_seconds") or BUCKET_SECONDS)
        if bucket_seconds != BUCKET_SECONDS:
            raise ValueError(f"unexpected bucket_seconds for {symbol}: {bucket_seconds}")
        bucket_start = _utc(raw.get("bucket_start"))
        mid = _finite(raw.get("mid"), "mid")
        if mid <= 0:
            raise ValueError("mid must be positive")
        normalized.append({"symbol": symbol, "bucket_start": bucket_start, "mid": mid, "raw": raw})

    normalized.sort(key=lambda item: (item["symbol"], item["bucket_start"]))
    by_key = {(item["symbol"], item["bucket_start"]): item for item in normalized}
    output: list[dict[str, Any]] = []
    for item in normalized:
        predecessor_at = item["bucket_start"].timestamp() - MOMENTUM_LOOKBACK_SECONDS
        predecessor_dt = datetime.fromtimestamp(predecessor_at, tz=timezone.utc)
        predecessor = by_key.get((item["symbol"], predecessor_dt))
        if predecessor is None:
            continue
        flow_share = _aggressive_flow_share_abs(item["raw"])
        pressure = _book_pressure_abs(item["raw"])
        if flow_share is None or pressure is None:
            continue
        mid_return_abs = abs(item["mid"] / predecessor["mid"] - 1.0)
        output.append(
            {
                "symbol": item["symbol"],
                "bucket_start": item["bucket_start"].isoformat(),
                "mid_return_60s_abs": mid_return_abs,
                "aggressive_flow_share_abs": flow_share,
                "book_pressure_abs": pressure,
            }
        )
    return output


def adapter_contract() -> dict[str, Any]:
    return {
        "feature_adapter_id": FEATURE_ADAPTER_ID,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "live_strategy_mutation": False,
        "bucket_seconds": BUCKET_SECONDS,
        "momentum_lookback_seconds": MOMENTUM_LOOKBACK_SECONDS,
        "gap_interpolation_allowed": False,
        "symbols": list(SYMBOLS),
    }
