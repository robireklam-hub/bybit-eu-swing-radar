"""Frozen research-only BTC macro / cycle / ETF Intelligence v1.

This module is descriptive only. It does not emit a trade direction, score, gate,
or execution instruction and does not consume post-trade outcome labels.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

SPEC_VERSION = "btc-macro-cycle-etf-shadow-v1"
LAST_HALVING_HEIGHT = 840_000
NEXT_HALVING_HEIGHT = 1_050_000
HALVING_INTERVAL = 210_000
LAST_HALVING_AT = datetime(2024, 4, 20, 0, 9, 27, tzinfo=timezone.utc)


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "btc_execution_symbol": "BTCUSDC",
        "cycle": {
            "last_halving_height": LAST_HALVING_HEIGHT,
            "next_halving_height": NEXT_HALVING_HEIGHT,
            "halving_interval_blocks": HALVING_INTERVAL,
            "phase_semantics": "block-progress quartiles; descriptive, not predictive",
        },
        "btc_price_features": [
            "close",
            "sma_200d",
            "distance_from_200d_pct",
            "return_30d_pct",
            "return_90d_pct",
            "rolling_300d_high_drawdown_pct",
        ],
        "macro_series": ["DGS10", "DTWEXBGS", "WALCL", "RRPONTSYD"],
        "etf_features": ["latest_daily_flow_usd", "flow_5d_usd", "flow_20d_usd"],
    }


def _pct_change(current: float, prior: float | None) -> float | None:
    if prior in (None, 0):
        return None
    return (current / float(prior) - 1.0) * 100.0


def summarize_cycle(tip_height: int, *, now: datetime | None = None) -> dict[str, Any]:
    if tip_height < LAST_HALVING_HEIGHT:
        raise ValueError("tip height predates the configured 2024 halving")
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    blocks_since = tip_height - LAST_HALVING_HEIGHT
    blocks_remaining = max(NEXT_HALVING_HEIGHT - tip_height, 0)
    progress = min(max(blocks_since / HALVING_INTERVAL, 0.0), 1.0)
    quartile = min(int(progress * 4) + 1, 4)
    estimated_next = timestamp + timedelta(seconds=blocks_remaining * 600)
    return {
        "tip_height": int(tip_height),
        "last_halving_height": LAST_HALVING_HEIGHT,
        "next_halving_height": NEXT_HALVING_HEIGHT,
        "blocks_since_halving": blocks_since,
        "blocks_to_next_halving": blocks_remaining,
        "cycle_progress_pct": progress * 100.0,
        "cycle_quartile": f"Q{quartile}",
        "days_since_halving": (timestamp - LAST_HALVING_AT).total_seconds() / 86400.0,
        "estimated_next_halving_at": estimated_next.isoformat(),
        "estimated_next_halving_assumption_seconds_per_block": 600,
    }


def _close_series_from_bybit(payload: Mapping[str, Any]) -> list[tuple[int, float]]:
    rows = ((payload.get("result") or {}).get("list") or []) if isinstance(payload, Mapping) else []
    parsed: list[tuple[int, float]] = []
    for row in rows:
        if not isinstance(row, Sequence) or len(row) < 5:
            continue
        try:
            parsed.append((int(row[0]), float(row[4])))
        except (TypeError, ValueError):
            continue
    parsed.sort(key=lambda item: item[0])
    return parsed


def summarize_btc_price(payload: Mapping[str, Any]) -> dict[str, Any]:
    rows = _close_series_from_bybit(payload)
    if len(rows) < 201:
        raise ValueError("at least 201 daily BTCUSDC closes are required")
    closes = [value for _, value in rows]
    close = closes[-1]
    sma200 = mean(closes[-200:])
    high300 = max(closes[-300:]) if len(closes) >= 300 else max(closes)
    return {
        "symbol": "BTCUSDC",
        "data_points": len(closes),
        "last_candle_start_ms": rows[-1][0],
        "close": close,
        "sma_200d": sma200,
        "distance_from_200d_pct": _pct_change(close, sma200),
        "return_30d_pct": _pct_change(close, closes[-31]) if len(closes) >= 31 else None,
        "return_90d_pct": _pct_change(close, closes[-91]) if len(closes) >= 91 else None,
        "rolling_300d_high": high300,
        "rolling_300d_high_drawdown_pct": (close / high300 - 1.0) * 100.0,
        "price_vs_200d": "ABOVE" if close > sma200 else "BELOW" if close < sma200 else "AT",
    }


def summarize_series(points: Sequence[tuple[str, float]]) -> dict[str, Any]:
    if not points:
        raise ValueError("macro series is empty")
    latest_date, latest_value = points[-1]
    def value_back(n: int) -> float | None:
        return points[-1 - n][1] if len(points) > n else None
    return {
        "latest_date": latest_date,
        "latest": latest_value,
        "change_5obs_pct": _pct_change(latest_value, value_back(5)),
        "change_20obs_pct": _pct_change(latest_value, value_back(20)),
        "observations": len(points),
    }


def summarize_etf_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = [row for row in rows if row.get("total_usd") is not None]
    if not usable:
        raise ValueError("ETF flow rows are empty")
    latest = usable[-1]
    totals = [float(row["total_usd"]) for row in usable]
    return {
        "latest_date": latest.get("date"),
        "latest_daily_flow_usd": totals[-1],
        "flow_5d_usd": sum(totals[-5:]),
        "flow_20d_usd": sum(totals[-20:]),
        "positive_days_20d": sum(1 for value in totals[-20:] if value > 0),
        "negative_days_20d": sum(1 for value in totals[-20:] if value < 0),
        "observations": len(totals),
        "latest_breakdown_usd": latest.get("funds") or {},
    }


def build_snapshot(
    *,
    cycle: Mapping[str, Any],
    btc_price: Mapping[str, Any],
    macro: Mapping[str, Any],
    etf: Mapping[str, Any] | None,
    source_status: Mapping[str, Any],
    captured_at: datetime | None = None,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    timestamp = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec": spec(),
        "captured_at": timestamp.isoformat(),
        "source_commit_sha": source_commit_sha,
        "cycle": dict(cycle),
        "btc_price": dict(btc_price),
        "macro": dict(macro),
        "etf": dict(etf) if etf is not None else None,
        "coverage": {"source_status": dict(source_status)},
    }
