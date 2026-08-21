"""Label-blind context snapshot for day-barrier-clear-rearm-v1.

This module enriches a prospective barrier-clear event with information that was
available by the close of the clearing 5m bar. Candle-derived fields are rebuilt
from closed point-in-time prefixes. Bid/ask spread is not reconstructable from
OHLCV, so the Bybit instrument snapshot from the observer run is stored in a
separate provenance bucket and is never represented as clear-time spread.

Research only: no score, ranking, eligibility or execution mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import day_worker as live
from research.market_regime_shadow import THRESHOLDS as REGIME_THRESHOLDS
from sweep_research import classify_15m_structure

CONTEXT_VERSION = "day-barrier-clear-context-v1"
FIVE_MIN_MS = 5 * 60 * 1000
FIFTEEN_MIN_MS = 15 * 60 * 1000
ONE_HOUR_MS = 60 * 60 * 1000
RELATIVE_LOOKBACK = 20


def _closed_prefix(bars: Iterable[Any], interval_ms: int, cutoff_ms: int) -> list[Any]:
    return [bar for bar in bars if int(bar.start_ms) + interval_ms <= cutoff_ms]


def _relative_ratio(bars: list[Any], field: str, lookback: int = RELATIVE_LOOKBACK) -> float | None:
    if len(bars) < lookback + 1:
        return None
    baseline_rows = bars[-lookback - 1:-1]
    baseline = sum(float(getattr(bar, field, 0.0) or 0.0) for bar in baseline_rows) / lookback
    current = float(getattr(bars[-1], field, 0.0) or 0.0)
    return current / baseline if baseline > 0 else None


def _trend_structure(current_price: float, bars: list[Any]) -> str | None:
    if len(bars) < 50:
        return None
    closes = [float(bar.close) for bar in bars]
    return live.structure_label(current_price, live.ema(closes, 20), live.ema(closes, 50))


def _session_bucket(clear_close: datetime) -> dict[str, Any]:
    clear_close = clear_close.astimezone(timezone.utc)
    hour = clear_close.hour
    if hour < 8:
        bucket = "ASIA_00_08_UTC"
    elif hour < 13:
        bucket = "EUROPE_08_13_UTC"
    elif hour < 21:
        bucket = "US_13_21_UTC"
    else:
        bucket = "LATE_US_21_24_UTC"
    return {
        "basis": "FIXED_UTC_RESEARCH_BUCKET_V1",
        "bucket": bucket,
        "utc_hour": hour,
        "exchange_hours_inference": False,
    }


def _volatility_state(atr_ratio_15m: float | None) -> str:
    if atr_ratio_15m is None:
        return "UNKNOWN"
    if atr_ratio_15m >= REGIME_THRESHOLDS["high_vol_atr_ratio_min"]:
        return "HIGH_VOL_STRESS_ATR_ONLY"
    if atr_ratio_15m <= REGIME_THRESHOLDS["compression_atr_ratio_max"]:
        return "COMPRESSION_ATR_ONLY"
    if atr_ratio_15m >= REGIME_THRESHOLDS["expansion_atr_ratio_min"]:
        return "EXPANSION_ATR_ONLY"
    return "NORMAL_ATR"


def _structure_alignment(structure_15m: str | None, structure_1h: str | None) -> str:
    s15 = (structure_15m or "").lower()
    s1h = (structure_1h or "").lower()
    if "bullish" in s15 and "bullish" in s1h:
        return "ALIGNED_BULLISH"
    if "bearish" in s15 and "bearish" in s1h:
        return "ALIGNED_BEARISH"
    if not s15 or not s1h:
        return "UNKNOWN"
    return "MIXED"


def _bar_payload(bar: Any | None) -> dict[str, Any] | None:
    if bar is None:
        return None
    return {
        "start_ms": int(bar.start_ms),
        "close": float(bar.close),
        "volume": float(bar.volume),
        "turnover": float(bar.turnover),
    }


def build_clear_context_snapshot(analysis: Any, clear_index: int) -> dict[str, Any]:
    """Build a label-free context snapshot using no bars after the clear close."""
    bars_5m = list(analysis.bars_5m[: clear_index + 1])
    if not bars_5m:
        raise ValueError("clear_index has no 5m prefix")
    clear_bar = bars_5m[-1]
    clear_close_ms = int(clear_bar.start_ms) + FIVE_MIN_MS
    clear_close = datetime.fromtimestamp(clear_close_ms / 1000.0, tz=timezone.utc)

    bars_15m = _closed_prefix(getattr(analysis, "bars_15m", []) or [], FIFTEEN_MIN_MS, clear_close_ms)
    bars_1h = _closed_prefix(getattr(analysis, "bars_1h", []) or [], ONE_HOUR_MS, clear_close_ms)
    current_price = float(clear_bar.close)

    volume_ratio_5m = _relative_ratio(bars_5m, "volume")
    volume_ratio_15m = _relative_ratio(bars_15m, "volume")
    turnover_ratio_5m = _relative_ratio(bars_5m, "turnover")
    turnover_ratio_15m = _relative_ratio(bars_15m, "turnover")
    structure_15m = _trend_structure(current_price, bars_15m)
    structure_1h = _trend_structure(current_price, bars_1h)
    sweep_structure_15m = (
        classify_15m_structure(bars_15m, clear_close_ms, 3) if bars_15m else "UNKNOWN"
    )
    atr_ratio_15m = live.atr_ratio(bars_15m) if len(bars_15m) >= 36 else None

    instrument = analysis.instrument
    observer_market = {
        "snapshot_timing": "OBSERVER_RUN_NEAR_CLEAR_NOT_RECONSTRUCTED",
        "point_in_time_at_clear": False,
        "spread_at_clear_reconstructed": False,
        "reason": "HISTORICAL_BID_ASK_NOT_AVAILABLE_FROM_OHLCV",
        "last_price": getattr(instrument, "last_price", None),
        "bid": getattr(instrument, "bid", None),
        "ask": getattr(instrument, "ask", None),
        "spread_bps": getattr(instrument, "spread_bps", None),
        "turnover_24h": getattr(instrument, "turnover_24h", None),
        "volume_24h": getattr(instrument, "volume_24h", None),
        "tradeable": bool(getattr(instrument, "tradeable", False)),
        "liquidity_reasons": list(getattr(instrument, "liquidity_reasons", []) or []),
    }

    return {
        "context_version": CONTEXT_VERSION,
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "as_of_clear_close": clear_close.isoformat(),
        "point_in_time_candle_context": {
            "uses_bars_after_clear": False,
            "relative_lookback_bars": RELATIVE_LOOKBACK,
            "volume_ratio_5m": volume_ratio_5m,
            "volume_ratio_15m": volume_ratio_15m,
            "turnover_ratio_5m": turnover_ratio_5m,
            "turnover_ratio_15m": turnover_ratio_15m,
            "structure_15m": structure_15m,
            "structure_1h": structure_1h,
            "sweep_structure_15m": sweep_structure_15m,
            "atr_ratio_15m": atr_ratio_15m,
            "clear_bar_5m": _bar_payload(clear_bar),
            "last_closed_15m": _bar_payload(bars_15m[-1] if bars_15m else None),
            "session": _session_bucket(clear_close),
            "regime_context": {
                "basis": "POINT_IN_TIME_DAY_FEATURES_PLUS_FROZEN_MARKET_REGIME_ATR_THRESHOLDS",
                "volatility_state": _volatility_state(atr_ratio_15m),
                "structure_alignment": _structure_alignment(structure_15m, structure_1h),
                "full_market_regime_not_reconstructed": True,
                "reason": "FULL_MARKET_REGIME_V1_REQUIRES_4H_1D_AND_MARKET_BREADTH",
            },
        },
        "observer_run_market_snapshot": observer_market,
    }


__all__ = ["CONTEXT_VERSION", "build_clear_context_snapshot"]
