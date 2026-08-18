"""Preregistered label-free market-regime shadow classifier v1.

Research only. This module does not alter live strategy/scoring/eligibility/execution.
The thresholds below are frozen for forward observation before any outcome study.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SPEC_VERSION = "market-regime-shadow-v1"
REGIMES = (
    "TREND",
    "RANGE",
    "COMPRESSION",
    "EXPANSION",
    "HIGH_VOL_STRESS",
    "REVERSAL",
)
DIRECTIONS = ("BULL", "BEAR", "NEUTRAL")
THRESHOLDS = {
    "compression_bb_width_percentile_max": 20.0,
    "compression_atr_ratio_max": 0.85,
    "high_vol_atr_ratio_min": 1.50,
    "high_vol_true_range_ratio_min": 1.75,
    "expansion_atr_ratio_min": 1.20,
    "expansion_true_range_ratio_min": 1.35,
    "expansion_turnover_ratio_min": 1.25,
    "trend_efficiency_ratio_min": 0.35,
    "trend_ema_gap_atr_min": 0.40,
    "reversal_prior_atr_units_min": 2.00,
    "reversal_recent_atr_units_min": 0.75,
    "global_breadth_direction_pct": 60.0,
    "global_regime_breadth_pct": 40.0,
    "global_high_vol_breadth_pct": 30.0,
}


@dataclass(frozen=True)
class Bar:
    start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "promotion_allowed": False,
        "regimes": list(REGIMES),
        "directions": list(DIRECTIONS),
        "thresholds": dict(THRESHOLDS),
        "classification_priority": [
            "HIGH_VOL_STRESS",
            "COMPRESSION",
            "REVERSAL",
            "EXPANSION",
            "TREND",
            "RANGE",
        ],
        "notes": [
            "Uses completed Bybit EU spot OHLCV bars only.",
            "No trade outcomes, journal labels, net-R, scoring or eligibility fields are read.",
            "Thresholds are frozen for forward observation; no tuning is permitted on the forward sample.",
        ],
    }


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_bybit_klines(
    rows: Iterable[Iterable[Any]],
    *,
    interval_ms: int,
    now_ms: int | None = None,
) -> list[Bar]:
    """Normalize reverse-chronological Bybit rows and exclude incomplete bars."""
    cutoff = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    parsed: list[Bar] = []
    for raw in reversed(list(rows)):
        values = list(raw)
        if len(values) < 7:
            continue
        try:
            start_ms = int(values[0])
        except (TypeError, ValueError):
            continue
        if start_ms + interval_ms > cutoff:
            continue
        numeric = [_finite(value) for value in values[1:7]]
        if any(value is None for value in numeric):
            continue
        open_, high, low, close, volume, turnover = (float(value) for value in numeric)
        if min(open_, high, low, close) <= 0 or high < low:
            continue
        parsed.append(Bar(start_ms, open_, high, low, close, volume, turnover))
    return parsed


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.fmean(values) if values else 0.0


def _median(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.median(values) if values else 0.0


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    current = values[0]
    result = [current]
    for value in values[1:]:
        current = current + alpha * (value - current)
        result.append(current)
    return result


def _true_ranges(bars: list[Bar]) -> list[float]:
    if len(bars) < 2:
        return []
    previous_close = bars[0].close
    result: list[float] = []
    for bar in bars[1:]:
        result.append(max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)))
        previous_close = bar.close
    return result


def _rolling_mean(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    return [_mean(values[index - period:index]) for index in range(period, len(values) + 1)]


def _bb_widths(closes: list[float], period: int = 20) -> list[float]:
    if len(closes) < period:
        return []
    result: list[float] = []
    for index in range(period, len(closes) + 1):
        window = closes[index - period:index]
        avg = _mean(window)
        if avg <= 0:
            result.append(0.0)
            continue
        result.append(4.0 * statistics.pstdev(window) / avg)
    return result


def _percentile_rank(history: list[float], value: float) -> float:
    if not history:
        return 50.0
    ordered = [item for item in history if math.isfinite(item)]
    if not ordered:
        return 50.0
    return 100.0 * sum(1 for item in ordered if item <= value) / len(ordered)


def _return_pct(start: float, end: float) -> float:
    return (end / start - 1.0) * 100.0 if start > 0 else 0.0


def _efficiency_ratio(closes: list[float], periods: int = 20) -> float:
    if len(closes) < periods + 1:
        return 0.0
    window = closes[-(periods + 1):]
    net = abs(window[-1] - window[0])
    path = sum(abs(window[index] - window[index - 1]) for index in range(1, len(window)))
    return net / path if path > 0 else 0.0


def classify_symbol(symbol: str, bars_4h: list[Bar], bars_1d: list[Bar]) -> dict[str, Any]:
    if len(bars_4h) < 80 or len(bars_1d) < 55:
        raise ValueError(f"insufficient completed history for {symbol}")

    closes_4h = [bar.close for bar in bars_4h]
    closes_1d = [bar.close for bar in bars_1d]
    ema20_4h_series = _ema_series(closes_4h, 20)
    ema50_4h_series = _ema_series(closes_4h, 50)
    ema20_1d = _ema_series(closes_1d, 20)[-1]
    ema50_1d = _ema_series(closes_1d, 50)[-1]
    ema20_4h = ema20_4h_series[-1]
    ema50_4h = ema50_4h_series[-1]
    close = closes_4h[-1]

    tr = _true_ranges(bars_4h)
    atr14_series = _rolling_mean(tr, 14)
    atr14 = atr14_series[-1]
    atr_reference = _median(atr14_series[-50:-1]) or atr14
    atr_ratio = atr14 / atr_reference if atr_reference > 0 else 1.0
    atr_pct = atr14 / close * 100.0 if close > 0 else 0.0

    bb_width_series = _bb_widths(closes_4h, 20)
    bb_width = bb_width_series[-1]
    bb_width_percentile = _percentile_rank(bb_width_series[-100:-1], bb_width)

    last_tr = tr[-1]
    tr_reference = _median(tr[-21:-1]) or last_tr
    true_range_ratio = last_tr / tr_reference if tr_reference > 0 else 1.0

    recent_turnover = _mean([bar.turnover for bar in bars_4h[-3:]])
    turnover_reference = _median([bar.turnover for bar in bars_4h[-23:-3]]) or recent_turnover
    turnover_ratio = recent_turnover / turnover_reference if turnover_reference > 0 else 1.0

    efficiency = _efficiency_ratio(closes_4h, 20)
    ema_gap_atr = abs(ema20_4h - ema50_4h) / atr14 if atr14 > 0 else 0.0

    if ema20_4h > ema50_4h and ema20_1d > ema50_1d and close > ema20_4h:
        direction = "BULL"
    elif ema20_4h < ema50_4h and ema20_1d < ema50_1d and close < ema20_4h:
        direction = "BEAR"
    else:
        direction = "NEUTRAL"

    prior_return = _return_pct(closes_4h[-21], closes_4h[-4])
    recent_return = _return_pct(closes_4h[-4], closes_4h[-1])
    prior_atr_units = abs(prior_return) / atr_pct if atr_pct > 0 else 0.0
    recent_atr_units = abs(recent_return) / atr_pct if atr_pct > 0 else 0.0
    start_recent_above = closes_4h[-4] >= ema20_4h_series[-4]
    end_above = closes_4h[-1] >= ema20_4h_series[-1]
    ema20_cross_against_prior = (
        (prior_return > 0 and recent_return < 0 and start_recent_above and not end_above)
        or (prior_return < 0 and recent_return > 0 and not start_recent_above and end_above)
    )
    reversal = (
        prior_return * recent_return < 0
        and prior_atr_units >= THRESHOLDS["reversal_prior_atr_units_min"]
        and recent_atr_units >= THRESHOLDS["reversal_recent_atr_units_min"]
        and ema20_cross_against_prior
    )

    high_vol = (
        atr_ratio >= THRESHOLDS["high_vol_atr_ratio_min"]
        and true_range_ratio >= THRESHOLDS["high_vol_true_range_ratio_min"]
    )
    compression = (
        bb_width_percentile <= THRESHOLDS["compression_bb_width_percentile_max"]
        and atr_ratio <= THRESHOLDS["compression_atr_ratio_max"]
    )
    expansion = (
        atr_ratio >= THRESHOLDS["expansion_atr_ratio_min"]
        and (
            true_range_ratio >= THRESHOLDS["expansion_true_range_ratio_min"]
            or turnover_ratio >= THRESHOLDS["expansion_turnover_ratio_min"]
        )
    )
    trend = (
        direction != "NEUTRAL"
        and efficiency >= THRESHOLDS["trend_efficiency_ratio_min"]
        and ema_gap_atr >= THRESHOLDS["trend_ema_gap_atr_min"]
    )

    if high_vol:
        regime = "HIGH_VOL_STRESS"
    elif compression:
        regime = "COMPRESSION"
    elif reversal:
        regime = "REVERSAL"
    elif expansion:
        regime = "EXPANSION"
    elif trend:
        regime = "TREND"
    else:
        regime = "RANGE"

    return {
        "symbol": symbol.upper(),
        "regime": regime,
        "direction": direction,
        "last_completed_4h_at": datetime.fromtimestamp(bars_4h[-1].start_ms / 1000, tz=timezone.utc).isoformat(),
        "last_completed_1d_at": datetime.fromtimestamp(bars_1d[-1].start_ms / 1000, tz=timezone.utc).isoformat(),
        "metrics": {
            "close": close,
            "atr_4h": atr14,
            "atr_pct": atr_pct,
            "atr_ratio": atr_ratio,
            "bb_width": bb_width,
            "bb_width_percentile": bb_width_percentile,
            "true_range_ratio": true_range_ratio,
            "turnover_ratio": turnover_ratio,
            "trend_efficiency_ratio": efficiency,
            "ema_gap_atr": ema_gap_atr,
            "ema20_4h": ema20_4h,
            "ema50_4h": ema50_4h,
            "ema20_1d": ema20_1d,
            "ema50_1d": ema50_1d,
            "prior_17x4h_return_pct": prior_return,
            "recent_3x4h_return_pct": recent_return,
            "prior_atr_units": prior_atr_units,
            "recent_atr_units": recent_atr_units,
            "ema20_cross_against_prior": ema20_cross_against_prior,
        },
        "flags": {
            "high_vol_stress": high_vol,
            "compression": compression,
            "reversal": reversal,
            "expansion": expansion,
            "trend": trend,
        },
    }


def build_market_snapshot(
    analyses: Iterable[Mapping[str, Any]],
    *,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    rows = [dict(item) for item in analyses]
    if not rows:
        raise ValueError("market regime snapshot requires at least one symbol")
    by_symbol = {str(item.get("symbol") or "").upper(): item for item in rows}
    btc = by_symbol.get("BTCUSDC")
    if btc is None:
        raise ValueError("BTCUSDC is required for the global regime anchor")

    regime_counts = {regime: 0 for regime in REGIMES}
    direction_counts = {direction: 0 for direction in DIRECTIONS}
    for item in rows:
        regime = str(item.get("regime") or "")
        direction = str(item.get("direction") or "")
        if regime in regime_counts:
            regime_counts[regime] += 1
        if direction in direction_counts:
            direction_counts[direction] += 1

    n = len(rows)
    regime_pct = {key: value / n * 100.0 for key, value in regime_counts.items()}
    direction_pct = {key: value / n * 100.0 for key, value in direction_counts.items()}

    if direction_pct["BULL"] >= THRESHOLDS["global_breadth_direction_pct"]:
        dominant_direction = "BULL"
    elif direction_pct["BEAR"] >= THRESHOLDS["global_breadth_direction_pct"]:
        dominant_direction = "BEAR"
    else:
        dominant_direction = "NEUTRAL"

    btc_regime = str(btc["regime"])
    if btc_regime == "HIGH_VOL_STRESS" or regime_pct["HIGH_VOL_STRESS"] >= THRESHOLDS["global_high_vol_breadth_pct"]:
        global_regime = "HIGH_VOL_STRESS"
    elif btc_regime == "COMPRESSION" and regime_pct["COMPRESSION"] >= THRESHOLDS["global_regime_breadth_pct"]:
        global_regime = "COMPRESSION"
    elif btc_regime == "REVERSAL":
        global_regime = "REVERSAL"
    elif btc_regime == "EXPANSION" or regime_pct["EXPANSION"] >= THRESHOLDS["global_regime_breadth_pct"]:
        global_regime = "EXPANSION"
    elif btc_regime == "TREND" and dominant_direction == str(btc.get("direction")):
        global_regime = "TREND"
    else:
        global_regime = "RANGE"

    timestamp = captured_at or datetime.now(timezone.utc)
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_free": True,
        "promotion_allowed": False,
        "spec": spec(),
        "captured_at": timestamp.astimezone(timezone.utc).isoformat(),
        "global_regime": global_regime,
        "dominant_direction": dominant_direction,
        "btc_anchor": {"regime": btc["regime"], "direction": btc["direction"]},
        "universe_size": n,
        "regime_counts": regime_counts,
        "regime_pct": regime_pct,
        "direction_counts": direction_counts,
        "direction_pct": direction_pct,
        "symbols": sorted(rows, key=lambda item: str(item.get("symbol") or "")),
    }
