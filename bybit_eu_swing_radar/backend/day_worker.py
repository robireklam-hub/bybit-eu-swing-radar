"""Bybit EU Trading Radar — day-trade worker v0.7.3.

Separate engine from the swing worker:
- universe: active Bybit EU USDC spot pairs
- context: 4H and 1H
- setup: 15m
- trigger: closed 5m candle
- expected holding time: 30 minutes to 8 hours
- long execution: USDC spot
- short execution: USDC spot margin only when public borrowability is confirmed

Railway cron start command:
    python day_worker.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import asyncpg
import httpx

from journal import persist_day_journal
from research.prospective_funnel_v073 import persist_v073_prospective_funnel
from sweep_research import SweepResearchConfig, latest_bar_sweep_setup

from worker import (
    BUDAPEST,
    STABLE_BASES,
    Bar,
    Instrument,
    BybitAPI,
    CoinalyzeAPI,
    apply_shortability,
    atr,
    clamp,
    ema,
    enrich_coinalyze,
    mean,
    return_pct,
    round_to_tick,
    safe_float,
    structure_label,
)


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float environment variable {name}={raw!r}") from exc


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer environment variable {name}={raw!r}") from exc


DATABASE_URL = os.getenv("DATABASE_URL", "")
SOURCE_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
DAY_STRATEGY_VERSION = "0.7.3"
DAY_MIN_TURNOVER_USDC = env_float("DAY_MIN_TURNOVER_USDC", 250_000.0)
DAY_MAX_SPREAD_BPS = env_float("DAY_MAX_SPREAD_BPS", 25.0)
DAY_DISCOVERY_MAX_SPREAD_BPS = env_float("DAY_DISCOVERY_MAX_SPREAD_BPS", 150.0)
DAY_DEEP_LIMIT = min(max(env_int("DAY_DEEP_LIMIT", 30), 15), 50)
DAY_FAST_CONCURRENCY = min(max(env_int("DAY_FAST_CONCURRENCY", 4), 1), 8)
DAY_CONTEXT_CONCURRENCY = min(max(env_int("DAY_CONTEXT_CONCURRENCY", 4), 1), 8)
DAY_OUTPUT_LIMIT = min(max(env_int("DAY_OUTPUT_LIMIT", 20), 10), 50)
DAY_RETRY_PASSES = min(max(env_int("DAY_RETRY_PASSES", 2), 0), 4)
DAY_MIN_RR = env_float("DAY_MIN_RR", 1.8)
DAY_ASSUMED_ROUND_TRIP_COST_BPS = env_float("DAY_ASSUMED_ROUND_TRIP_COST_BPS", 20.0)
DAY_MIN_SETUP_SCORE = env_float("DAY_MIN_SETUP_SCORE", 70.0)
DAY_MIN_EXPANSION_SCORE = env_float("DAY_MIN_EXPANSION_SCORE", 55.0)
DAY_MIN_DIRECTION_SCORE = env_float("DAY_MIN_DIRECTION_SCORE", 35.0)
DAY_MIN_QUALITY_SCORE = env_float("DAY_MIN_QUALITY_SCORE", 65.0)
DAY_TRIGGER_VOLUME_RATIO = env_float("DAY_TRIGGER_VOLUME_RATIO", 1.3)
DAY_BARRIER_LOOKBACK_15M = min(max(env_int("DAY_BARRIER_LOOKBACK_15M", 96), 32), 240)
DAY_BARRIER_PIVOT_LEFT = min(max(env_int("DAY_BARRIER_PIVOT_LEFT", 2), 1), 5)
DAY_BARRIER_PIVOT_RIGHT = min(max(env_int("DAY_BARRIER_PIVOT_RIGHT", 2), 1), 5)
DAY_BARRIER_MIN_PROMINENCE_ATR = env_float("DAY_BARRIER_MIN_PROMINENCE_ATR", 0.10)


DEFAULT_DAY_SYMBOLS = {
    "BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC", "DOGEUSDC",
    "BNBUSDC", "ADAUSDC", "LINKUSDC", "SUIUSDC", "AVAXUSDC",
    "HYPEUSDC", "ENAUSDC", "BCHUSDC", "LTCUSDC", "XLMUSDC",
}
DAY_MANDATORY_SYMBOLS = {
    item.strip().upper()
    for item in os.getenv(
        "DAY_MANDATORY_SYMBOLS", ",".join(sorted(DEFAULT_DAY_SYMBOLS))
    ).split(",")
    if item.strip()
}


@dataclass
class FastResult:
    instrument: Instrument
    bars_5m: list[Bar]
    bars_15m: list[Bar]
    fast_score: float
    fast_side: str
    return_15m_pct: float
    return_1h_pct: float
    volume_ratio_5m: float
    volume_ratio_15m: float
    breakout_5m: bool


@dataclass
class DayAnalysis:
    instrument: Instrument
    bars_5m: list[Bar]
    bars_15m: list[Bar]
    bars_1h: list[Bar]
    bars_4h: list[Bar]
    atr_5m: float
    atr_15m: float
    rolling_vwap_24h: float
    ema20_15m: float
    ema50_15m: float
    ema20_1h: float
    ema50_1h: float
    ema20_4h: float
    ema50_4h: float
    return_15m_pct: float
    return_1h_pct: float
    return_4h_pct: float
    relative_strength_1h: float
    relative_strength_4h: float
    volume_ratio_5m: float
    volume_ratio_15m: float
    atr_ratio_15m: float
    structure_15m: str
    structure_1h: str
    structure_4h: str
    expansion_score: float
    direction_score: float
    quality_score: float
    derivatives: dict[str, Any]
    missing_data: list[str]
    shortable: bool = False
    max_borrowing_amount: float = 0.0


def signed_clamp(value: float) -> float:
    return max(-100.0, min(100.0, value))


def average(values: Iterable[float]) -> float:
    rows = list(values)
    return statistics.fmean(rows) if rows else 0.0


def bar_return_pct(bars: list[Bar], periods: int) -> float:
    return return_pct([bar.close for bar in bars], periods)


def volume_ratio(bars: list[Bar], lookback: int = 20) -> float:
    if len(bars) < lookback + 1:
        return 0.0
    baseline = average(bar.volume for bar in bars[-lookback - 1:-1])
    return bars[-1].volume / baseline if baseline > 0 else 0.0


def rolling_vwap(bars: list[Bar], lookback: int = 96) -> float:
    rows = bars[-lookback:]
    volume = sum(bar.volume for bar in rows)
    turnover = sum(bar.turnover for bar in rows)
    return turnover / volume if volume > 0 else (rows[-1].close if rows else 0.0)


def atr_ratio(bars: list[Bar], period: int = 14, windows: int = 20) -> float:
    if len(bars) < period + windows + 2:
        return 1.0
    values: list[float] = []
    for end in range(len(bars) - windows + 1, len(bars) + 1):
        subset = bars[:end]
        value = atr(subset, period)
        if value > 0:
            values.append(value)
    if not values:
        return 1.0
    current = values[-1]
    baseline = statistics.median(values[:-1]) if len(values) > 1 else current
    return current / baseline if baseline > 0 else 1.0


def normalize_usdc_universe(
    instruments: list[dict[str, Any]],
    tickers: list[dict[str, Any]],
) -> tuple[list[Instrument], list[dict[str, str]], dict[str, Any]]:
    ticker_map = {
        str(item.get("symbol", "")).upper(): item for item in tickers
    }
    normalized: dict[str, dict[str, Any]] = {}
    duplicate_symbols: set[str] = set()
    raw_usdc_records = 0

    def rank_record(item: dict[str, Any]) -> tuple[int, int, int, int]:
        return (
            1 if item.get("status") == "Trading" else 0,
            1 if str(item.get("stTag", "0")) != "1" else 0,
            1 if str(item.get("marginTrading", "none")).lower() != "none" else 0,
            1 if safe_float(item.get("priceFilter", {}).get("tickSize")) > 0 else 0,
        )

    for raw in instruments:
        symbol = str(raw.get("symbol", "")).upper()
        quote = str(raw.get("quoteCoin", "")).upper()
        if quote != "USDC" or not symbol:
            continue
        raw_usdc_records += 1
        previous = normalized.get(symbol)
        if previous is None:
            normalized[symbol] = raw
        else:
            duplicate_symbols.add(symbol)
            if rank_record(raw) > rank_record(previous):
                normalized[symbol] = raw

    exclusions: list[dict[str, str]] = []
    candidates: list[Instrument] = []
    active_count = 0

    for symbol, raw in normalized.items():
        base = str(raw.get("baseCoin", "")).upper()
        if raw.get("status") != "Trading":
            exclusions.append({"symbol": symbol, "reason": "Not Trading"})
            continue
        active_count += 1
        if base in STABLE_BASES:
            exclusions.append({"symbol": symbol, "reason": "Stable/fiat base excluded"})
            continue
        if str(raw.get("stTag", "0")) == "1":
            exclusions.append({"symbol": symbol, "reason": "Special-treatment instrument"})
            continue

        ticker = ticker_map.get(symbol)
        if not ticker:
            exclusions.append({"symbol": symbol, "reason": "Missing ticker"})
            continue

        last = safe_float(ticker.get("lastPrice"))
        bid = safe_float(ticker.get("bid1Price"))
        ask = safe_float(ticker.get("ask1Price"))
        turnover = safe_float(ticker.get("turnover24h"))
        volume = safe_float(ticker.get("volume24h"))
        if min(last, bid, ask) <= 0:
            exclusions.append({"symbol": symbol, "reason": "Invalid bid/ask/last"})
            continue

        spread_bps = ((ask - bid) / ((ask + bid) / 2.0)) * 10_000.0
        if spread_bps > DAY_DISCOVERY_MAX_SPREAD_BPS:
            exclusions.append({
                "symbol": symbol,
                "reason": f"Spread {spread_bps:.1f} bps above day discovery limit",
            })
            continue

        liquidity_reasons: list[str] = []
        if turnover < DAY_MIN_TURNOVER_USDC:
            liquidity_reasons.append(
                f"24h turnover below {DAY_MIN_TURNOVER_USDC:.0f} USDC day-trade minimum"
            )
        if spread_bps > DAY_MAX_SPREAD_BPS:
            liquidity_reasons.append(
                f"Spread {spread_bps:.1f} bps above day-trade executable limit"
            )

        candidates.append(
            Instrument(
                symbol=symbol,
                base=base,
                quote="USDC",
                margin_trading=str(raw.get("marginTrading", "none")),
                tick_size=safe_float(raw.get("priceFilter", {}).get("tickSize")),
                turnover_24h=turnover,
                volume_24h=volume,
                last_price=last,
                bid=bid,
                ask=ask,
                spread_bps=spread_bps,
                price_change_24h_pct=safe_float(ticker.get("price24hPcnt")) * 100.0,
                tradeable=not liquidity_reasons,
                liquidity_reasons=liquidity_reasons,
                discovery_source=(
                    "mandatory" if symbol in DAY_MANDATORY_SYMBOLS else "market"
                ),
            )
        )

    candidates.sort(key=lambda item: item.turnover_24h, reverse=True)
    stats = {
        "raw_usdc_instrument_records": raw_usdc_records,
        "unique_usdc_instruments": len(normalized),
        "duplicate_instrument_records": raw_usdc_records - len(normalized),
        "duplicate_symbols": sorted(duplicate_symbols),
        "active_usdc_pairs": active_count,
        "eligible_day_discovery_pairs": len(candidates),
        "day_tradeable_pairs": sum(1 for item in candidates if item.tradeable),
        "day_liquidity_blocked_pairs": sum(1 for item in candidates if not item.tradeable),
        "minimum_turnover_usdc": DAY_MIN_TURNOVER_USDC,
        "max_executable_spread_bps": DAY_MAX_SPREAD_BPS,
        "max_discovery_spread_bps": DAY_DISCOVERY_MAX_SPREAD_BPS,
    }
    return candidates, exclusions, stats


async def fetch_fast(
    api: BybitAPI,
    instrument: Instrument,
    semaphore: asyncio.Semaphore,
) -> tuple[Instrument, list[Bar], list[Bar]] | None:
    try:
        async with semaphore:
            bars_5m = await api.klines(instrument.symbol, "5", limit=100)
            bars_15m = await api.klines(instrument.symbol, "15", limit=120)
        if len(bars_5m) < 40 or len(bars_15m) < 40:
            return None
        return instrument, bars_5m, bars_15m
    except Exception as exc:
        print(f"WARN {instrument.symbol}: day fast fetch failed: {exc}", file=sys.stderr)
        return None


async def fetch_context(
    api: BybitAPI,
    fast: FastResult,
    semaphore: asyncio.Semaphore,
) -> tuple[FastResult, list[Bar], list[Bar]] | None:
    try:
        async with semaphore:
            bars_1h = await api.klines(fast.instrument.symbol, "60", limit=140)
            bars_4h = await api.klines(fast.instrument.symbol, "240", limit=100)
        if len(bars_1h) < 55 or len(bars_4h) < 55:
            return None
        return fast, bars_1h, bars_4h
    except Exception as exc:
        print(f"WARN {fast.instrument.symbol}: day context fetch failed: {exc}", file=sys.stderr)
        return None


def calculate_fast_result(
    instrument: Instrument,
    bars_5m: list[Bar],
    bars_15m: list[Bar],
) -> FastResult:
    r15 = bar_return_pct(bars_5m, 3)
    r1h = bar_return_pct(bars_15m, 4)
    vol5 = volume_ratio(bars_5m)
    vol15 = volume_ratio(bars_15m)
    previous_high = max(bar.high for bar in bars_5m[-13:-1])
    previous_low = min(bar.low for bar in bars_5m[-13:-1])
    close = bars_5m[-1].close
    breakout = close > previous_high or close < previous_low
    side = "long" if (r15 * 2.0 + r1h) >= 0 else "short"

    score = clamp(
        abs(r15) * 10.0
        + abs(r1h) * 6.0
        + min(vol5, 5.0) * 7.0
        + min(vol15, 4.0) * 4.0
        + (15.0 if breakout else 0.0)
        + min(math.log10(max(instrument.turnover_24h, 1.0)) * 2.0, 12.0)
    )
    return FastResult(
        instrument=instrument,
        bars_5m=bars_5m,
        bars_15m=bars_15m,
        fast_score=round(score, 2),
        fast_side=side,
        return_15m_pct=round(r15, 4),
        return_1h_pct=round(r1h, 4),
        volume_ratio_5m=round(vol5, 3),
        volume_ratio_15m=round(vol15, 3),
        breakout_5m=breakout,
    )


def select_deep_universe(results: list[FastResult]) -> list[FastResult]:
    ranked = sorted(
        results,
        key=lambda item: (
            item.instrument.symbol in DAY_MANDATORY_SYMBOLS,
            item.fast_score,
            item.instrument.turnover_24h,
        ),
        reverse=True,
    )
    selected: list[FastResult] = []
    seen: set[str] = set()
    for item in ranked:
        if item.instrument.symbol in seen:
            continue
        selected.append(item)
        seen.add(item.instrument.symbol)
        if len(selected) >= DAY_DEEP_LIMIT:
            break

    btc = next(
        (item for item in results if item.instrument.symbol == "BTCUSDC"), None
    )
    if btc and btc.instrument.symbol not in seen:
        if len(selected) >= DAY_DEEP_LIMIT:
            selected[-1] = btc
        else:
            selected.append(btc)
    return selected


def analyze_day_market(
    fast: FastResult,
    bars_1h: list[Bar],
    bars_4h: list[Bar],
    btc_return_1h: float,
    btc_return_4h: float,
) -> DayAnalysis:
    bars_5m = fast.bars_5m
    bars_15m = fast.bars_15m
    closes_15m = [bar.close for bar in bars_15m]
    closes_1h = [bar.close for bar in bars_1h]
    closes_4h = [bar.close for bar in bars_4h]
    current = bars_5m[-1].close

    atr5 = atr(bars_5m, 14)
    atr15 = atr(bars_15m, 14)
    vwap24 = rolling_vwap(bars_15m, 96)
    e20_15 = ema(closes_15m, 20)
    e50_15 = ema(closes_15m, 50)
    e20_1h = ema(closes_1h, 20)
    e50_1h = ema(closes_1h, 50)
    e20_4h = ema(closes_4h, 20)
    e50_4h = ema(closes_4h, 50)

    r15 = bar_return_pct(bars_5m, 3)
    r1h = bar_return_pct(bars_15m, 4)
    r4h = bar_return_pct(bars_1h, 4)
    rs1h = r1h - btc_return_1h
    rs4h = r4h - btc_return_4h
    vol5 = volume_ratio(bars_5m)
    vol15 = volume_ratio(bars_15m)
    atr15_ratio = atr_ratio(bars_15m)

    s15 = structure_label(current, e20_15, e50_15)
    s1h = structure_label(current, e20_1h, e50_1h)
    s4h = structure_label(current, e20_4h, e50_4h)

    trend_15 = 15.0 if "bullish" in s15 else -15.0 if "bearish" in s15 else 0.0
    trend_1h = 20.0 if "bullish" in s1h else -20.0 if "bearish" in s1h else 0.0
    trend_4h = 10.0 if "bullish" in s4h else -10.0 if "bearish" in s4h else 0.0
    vwap_effect = 8.0 if current > vwap24 else -8.0

    prior_high = max(bar.high for bar in bars_5m[-13:-1])
    prior_low = min(bar.low for bar in bars_5m[-13:-1])
    breakout_effect = 12.0 if current > prior_high else -12.0 if current < prior_low else 0.0

    direction = signed_clamp(
        r15 * 8.0
        + r1h * 5.0
        + r4h * 2.5
        + rs1h * 4.0
        + rs4h * 2.0
        + trend_15
        + trend_1h
        + trend_4h
        + vwap_effect
        + breakout_effect
    )

    expansion = clamp(
        15.0
        + min(max(vol5 - 0.8, 0.0) * 16.0, 25.0)
        + min(max(vol15 - 0.8, 0.0) * 10.0, 15.0)
        + min(abs(r15) * 10.0, 15.0)
        + min(abs(r1h) * 6.0, 15.0)
        + min(max(atr15_ratio - 0.8, 0.0) * 20.0, 15.0)
        + (10.0 if fast.breakout_5m else 0.0)
    )

    turnover_score = clamp(
        20.0 * math.log10(max(fast.instrument.turnover_24h, 1.0))
        / math.log10(10_000_000.0),
        0.0,
        20.0,
    )
    spread_score = clamp(20.0 - fast.instrument.spread_bps * 0.6, 0.0, 20.0)
    quality = clamp(
        35.0
        + turnover_score
        + spread_score
        + (15.0 if fast.instrument.tradeable else 0.0)
        + (5.0 if len(bars_1h) >= 100 and len(bars_4h) >= 80 else 0.0)
    )

    missing: list[str] = []
    if atr5 <= 0 or atr15 <= 0:
        missing.append("Short-term ATR is zero or unavailable")

    return DayAnalysis(
        instrument=fast.instrument,
        bars_5m=bars_5m,
        bars_15m=bars_15m,
        bars_1h=bars_1h,
        bars_4h=bars_4h,
        atr_5m=atr5,
        atr_15m=atr15,
        rolling_vwap_24h=vwap24,
        ema20_15m=e20_15,
        ema50_15m=e50_15,
        ema20_1h=e20_1h,
        ema50_1h=e50_1h,
        ema20_4h=e20_4h,
        ema50_4h=e50_4h,
        return_15m_pct=r15,
        return_1h_pct=r1h,
        return_4h_pct=r4h,
        relative_strength_1h=rs1h,
        relative_strength_4h=rs4h,
        volume_ratio_5m=vol5,
        volume_ratio_15m=vol15,
        atr_ratio_15m=atr15_ratio,
        structure_15m=s15,
        structure_1h=s1h,
        structure_4h=s4h,
        expansion_score=expansion,
        direction_score=direction,
        quality_score=quality,
        derivatives={},
        missing_data=missing,
    )


def setup_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "WATCH"
    return "NO_TRADE"


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def nearest_structural_barrier(
    analysis: DayAnalysis,
    side: str,
    entry: float,
    trigger_window_start_ms: int,
) -> dict[str, Any] | None:
    """Return the nearest confirmed 15m pivot in the trade direction.

    A barrier is structural only when it is a confirmed 2-sided pivot (configurable)
    with minimum ATR prominence. The pivot and all right-side confirmation bars must
    pre-date the 5m trigger lookback window, so the barrier cannot be the same recent
    structure from which the trigger itself was derived.
    """
    bars = analysis.bars_15m[-DAY_BARRIER_LOOKBACK_15M:]
    left = DAY_BARRIER_PIVOT_LEFT
    right = DAY_BARRIER_PIVOT_RIGHT
    if len(bars) < left + right + 1:
        return None

    candidates: list[dict[str, Any]] = []
    interval_ms = 15 * 60 * 1000
    min_prominence = max(analysis.atr_15m * DAY_BARRIER_MIN_PROMINENCE_ATR, 0.0)

    for index in range(left, len(bars) - right):
        pivot = bars[index]
        confirmation_end_ms = bars[index + right].start_ms + interval_ms
        # Exclude the whole trigger-formation window and anything confirmed inside it.
        if confirmation_end_ms > trigger_window_start_ms:
            continue

        left_rows = bars[index - left:index]
        right_rows = bars[index + 1:index + right + 1]
        if side == "long":
            left_ref = max(row.high for row in left_rows)
            right_ref = max(row.high for row in right_rows)
            is_pivot = pivot.high > left_ref and pivot.high >= right_ref
            prominence = min(pivot.high - left_ref, pivot.high - right_ref)
            price = pivot.high
            swing_type = "SWING_HIGH"
            if not is_pivot or prominence < min_prominence or price <= entry:
                continue
        else:
            left_ref = min(row.low for row in left_rows)
            right_ref = min(row.low for row in right_rows)
            is_pivot = pivot.low < left_ref and pivot.low <= right_ref
            prominence = min(left_ref - pivot.low, right_ref - pivot.low)
            price = pivot.low
            swing_type = "SWING_LOW"
            if not is_pivot or prominence < min_prominence or price >= entry:
                continue

        candidates.append({
            "price": price,
            "timeframe": "15m",
            "swing_type": swing_type,
            "pivot_start_ms": pivot.start_ms,
            "pivot_time": _iso_from_ms(pivot.start_ms),
            "confirmed_at": _iso_from_ms(confirmation_end_ms),
            "prominence": prominence,
            "prominence_atr": (
                prominence / analysis.atr_15m if analysis.atr_15m > 0 else None
            ),
            "search_window_start": _iso_from_ms(bars[0].start_ms),
            "search_window_end": _iso_from_ms(trigger_window_start_ms),
            "trigger_window_start": _iso_from_ms(trigger_window_start_ms),
            "trigger_window_excluded": True,
            "same_structure_as_trigger": False,
        })

    if not candidates:
        return None
    if side == "long":
        return min(candidates, key=lambda item: item["price"])
    return max(candidates, key=lambda item: item["price"])




def compact_day_audit_side(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the small, audit-only representation used by GPT Actions."""
    metrics = candidate.get("metrics") or {}
    targets = list(candidate.get("targets") or [])
    while len(targets) < 3:
        targets.append(0.0)
    trigger = candidate.get("trigger") or {}
    entry = float(trigger.get("price") or 0.0)
    return {
        "symbol": candidate["symbol"],
        "side": candidate["side"],
        "category": candidate["category"],
        "state": candidate["state"],
        "decision": candidate["decision"],
        "watch_bucket": candidate.get("watch_bucket"),
        "tradeable": bool(candidate.get("tradeable")),
        "shortable": bool(candidate.get("shortable")),
        "execution_status": candidate.get("execution_status", ""),
        "timeframe_conflict": bool(candidate.get("timeframe_conflict")),
        "side_direction_score": float(candidate.get("side_direction_score", 0.0)),
        "setup_score": float(candidate.get("setup_score", 0.0)),
        "trigger": {
            "timeframe": trigger.get("timeframe", "5m"),
            "condition": trigger.get("condition", ""),
            "price": entry,
            "requires_close": bool(trigger.get("requires_close", True)),
            "volume_confirmation": trigger.get("volume_confirmation", ""),
            "triggered": bool(trigger.get("triggered")),
        },
        "entry": entry,
        "entry_zone": candidate["entry_zone"],
        "stop": float(candidate["stop"]),
        "tp1": float(targets[0]),
        "tp2": float(targets[1]),
        "tp3": float(targets[2]),
        "expected_rr": float(candidate.get("expected_rr", 0.0)),
        "expected_rr_without_barrier": float(metrics.get("expected_rr_without_barrier", 0.0)),
        "expected_rr_with_barrier": float(metrics.get("expected_rr_with_barrier", 0.0)),
        "target_path_valid": bool(metrics.get("target_path_valid", False)),
        "nearest_structural_barrier": metrics.get("nearest_structural_barrier"),
        "barrier_rr_gross": metrics.get("barrier_rr_gross"),
        "barrier_rr_net": metrics.get("barrier_rr_net"),
        "barrier_before_tp2": bool(metrics.get("barrier_before_tp2", False)),
        "barrier_source": metrics.get("barrier_source"),
        "volume_ratio_5m": float(metrics.get("volume_ratio_5m", 0.0)),
    }


def watch_bucket(
    tradeable: bool,
    shortable: bool,
    side: str,
    conflict_4h: bool,
    expected_rr: float,
    score: float,
) -> str:
    if not tradeable or (side == "short" and not shortable):
        return "LIQUIDITY_OR_BORROW_BLOCKED"
    if expected_rr < 1.0:
        return "POOR_RR"
    if score >= 65 and expected_rr >= 1.2:
        return "NEAR_STRICT"
    return "LOW_CONVICTION"


def watch_rank(item: dict[str, Any]) -> tuple:
    bucket_rank = {
        "NEAR_STRICT": 4,
        "LOW_CONVICTION": 3,
        "TIMEFRAME_CONFLICT": 2,
        "POOR_RR": 1,
        "LIQUIDITY_OR_BORROW_BLOCKED": 0,
    }
    executable_side = (
        item["tradeable"]
        and (item["side"] == "long" or item["shortable"])
    )
    return (
        bucket_rank.get(item.get("watch_bucket", ""), -1),
        1 if executable_side else 0,
        item["expected_rr"],
        item["quality_score"],
        item["setup_score"],
        item["metrics"].get("turnover_24h_usdc", 0.0),
    )

def build_day_candidate(
    analysis: DayAnalysis,
    side: str,
    now: datetime,
) -> dict[str, Any] | None:
    if side not in {"long", "short"}:
        return None
    current = analysis.bars_5m[-1].close
    if current <= 0 or analysis.atr_5m <= 0 or analysis.atr_15m <= 0:
        return None

    side_direction = (
        analysis.direction_score if side == "long" else -analysis.direction_score
    )
    score = clamp(
        0.35 * analysis.expansion_score
        + 0.35 * max(side_direction, 0.0)
        + 0.30 * analysis.quality_score
    )

    previous_5m = analysis.bars_5m[-13:-1]
    trigger_price = (
        max(bar.high for bar in previous_5m)
        if side == "long"
        else min(bar.low for bar in previous_5m)
    )
    last = analysis.bars_5m[-1]
    previous_close = analysis.bars_5m[-2].close
    triggered = (
        last.close > trigger_price and previous_close <= trigger_price
        if side == "long"
        else last.close < trigger_price and previous_close >= trigger_price
    )
    distance_atr = abs(trigger_price - current) / max(analysis.atr_5m, 1e-12)

    aligned_15m = (
        "bullish" in analysis.structure_15m
        if side == "long"
        else "bearish" in analysis.structure_15m
    )
    aligned_1h = (
        "bullish" in analysis.structure_1h
        if side == "long"
        else "bearish" in analysis.structure_1h
    )
    conflict_4h = (
        "bearish" in analysis.structure_4h
        if side == "long"
        else "bullish" in analysis.structure_4h
    )

    sweep_config = SweepResearchConfig(
        volume_confirmation_ratio=DAY_TRIGGER_VOLUME_RATIO
    )
    sweep_trigger = latest_bar_sweep_setup(
        analysis.bars_5m,
        side,
        bars_15m=analysis.bars_15m,
        config=sweep_config,
    )
    # v0.7.3: a live trigger requires the complete closed-bar sequence:
    # sweep -> reclaim -> 5m structure shift -> non-opposing 15m structure -> volume.
    triggered = sweep_trigger is not None

    previous_above_vwap = previous_close > analysis.rolling_vwap_24h
    current_above_vwap = current > analysis.rolling_vwap_24h
    vwap_reclaim = (
        (not previous_above_vwap and current_above_vwap)
        if side == "long"
        else (previous_above_vwap and not current_above_vwap)
    )
    near_ema = abs(current - analysis.ema20_15m) <= 0.5 * analysis.atr_15m

    if triggered:
        setup_type = "LIQUIDITY_SWEEP_RECLAIM"
    elif vwap_reclaim and aligned_1h:
        setup_type = "VWAP_RECLAIM" if side == "long" else "VWAP_REJECTION"
    elif near_ema and aligned_15m and aligned_1h:
        setup_type = "TREND_PULLBACK"
    elif distance_atr <= 0.35:
        setup_type = "BREAKOUT_WATCH"
    else:
        setup_type = "MOMENTUM_WATCH"

    recent = analysis.bars_5m[-9:]
    if side == "long":
        stop = min(
            min(bar.low for bar in recent),
            trigger_price - 1.2 * analysis.atr_5m,
        )
        entry_low = trigger_price
        entry_high = trigger_price + 0.15 * analysis.atr_5m
        invalidation = (
            f"Closed 5m candle below {round_to_tick(stop, analysis.instrument.tick_size)} "
            "or loss of the 15m higher-low structure"
        )
    else:
        stop = max(
            max(bar.high for bar in recent),
            trigger_price + 1.2 * analysis.atr_5m,
        )
        entry_low = trigger_price - 0.15 * analysis.atr_5m
        entry_high = trigger_price
        invalidation = (
            f"Closed 5m candle above {round_to_tick(stop, analysis.instrument.tick_size)} "
            "or reclaim of the 15m lower-high structure"
        )

    if sweep_trigger is not None:
        trigger_price = float(sweep_trigger["candidate_entry"])
        stop = float(sweep_trigger["candidate_invalidation"])
        entry_low = trigger_price
        entry_high = trigger_price
        distance_atr = abs(trigger_price - current) / max(analysis.atr_5m, 1e-12)
        invalidation = (
            f"Sweep extreme {round_to_tick(stop, analysis.instrument.tick_size)} is invalidated"
        )

    entry = trigger_price
    risk = abs(entry - stop)
    if risk <= max(analysis.instrument.tick_size * 3.0, entry * 0.0002):
        return None

    direction_multiplier = 1.0 if side == "long" else -1.0
    assumed_cost = entry * DAY_ASSUMED_ROUND_TRIP_COST_BPS / 10_000.0

    # Net-R convention: denominator is the pre-cost stop distance (risk).
    # Round-trip cost is subtracted from PnL. Therefore a target intended to
    # deliver N net R must have gross reward = N*risk + cost. This makes the
    # 1.8R strict gate mathematically attainable and matches journal/backtest
    # net-R accounting.
    def target_for_net_r(net_r: float) -> float:
        required_reward = net_r * risk + assumed_cost
        return entry + direction_multiplier * required_reward

    gross_tp1 = target_for_net_r(1.0)
    gross_tp2 = target_for_net_r(DAY_MIN_RR)
    gross_tp3 = target_for_net_r(2.5)

    trigger_window_start_ms = previous_5m[0].start_ms
    if sweep_trigger is not None and sweep_trigger.get("sweep_index") is not None:
        sweep_index = int(sweep_trigger["sweep_index"])
        if 0 <= sweep_index < len(analysis.bars_5m):
            trigger_window_start_ms = analysis.bars_5m[sweep_index].start_ms
    barrier_info = nearest_structural_barrier(
        analysis, side, entry, trigger_window_start_ms
    )
    barrier = None if barrier_info is None else float(barrier_info["price"])

    expected_rr_without_barrier = max(
        0.0,
        (abs(gross_tp2 - entry) - assumed_cost) / max(risk, 1e-12),
    )
    barrier_before_tp2 = False
    if barrier is not None:
        barrier_before_tp2 = (
            entry < barrier < gross_tp2
            if side == "long"
            else gross_tp2 < barrier < entry
        )

    reward_reference = barrier if barrier_before_tp2 else gross_tp2
    gross_reward = abs(reward_reference - entry)
    expected_rr = max(
        0.0,
        (gross_reward - assumed_cost) / max(risk, 1e-12),
    )
    barrier_rr = abs(barrier - entry) / risk if barrier is not None else None
    barrier_net_rr = (
        max(0.0, (abs(barrier - entry) - assumed_cost) / max(risk, 1e-12))
        if barrier is not None
        else None
    )
    target_path_valid = (
        not barrier_before_tp2
        or (barrier_net_rr is not None and barrier_net_rr + 1e-9 >= DAY_MIN_RR)
    )

    strict_execution = analysis.instrument.tradeable and (
        side == "long" or analysis.shortable
    )
    strict_scores = (
        score >= DAY_MIN_SETUP_SCORE
        and analysis.expansion_score >= DAY_MIN_EXPANSION_SCORE
        and side_direction >= DAY_MIN_DIRECTION_SCORE
        and analysis.quality_score >= DAY_MIN_QUALITY_SCORE
        and expected_rr + 1e-9 >= DAY_MIN_RR
    )
    strict = strict_execution and strict_scores

    if strict and triggered:
        state = "TRIGGERED"
        decision = "TRADE"
    elif strict and distance_atr <= 0.35:
        state = "ARMED"
        decision = "WAIT"
    elif strict:
        state = "WATCH"
        decision = "WAIT"
    elif score >= 55:
        state = "WATCH"
        decision = "NO_TRADE"
    else:
        state = "NO_TRADE"
        decision = "NO_TRADE"

    liquidity_reasons = list(analysis.instrument.liquidity_reasons)
    if side == "short" and not analysis.shortable:
        liquidity_reasons.append("Bybit EU USDC spot-margin borrowability not confirmed")

    execution_status = (
        "DAY_TRADE_EXECUTABLE"
        if strict_execution
        else "DAY_TRADE_BLOCKED"
    )
    category = "STRICT" if strict else "WATCH_ONLY"
    technical_grade = setup_grade(score)
    displayed_grade = technical_grade if strict else (
        "WATCH" if score >= 55 else "NO_TRADE"
    )
    candidate_watch_bucket = (
        "STRICT"
        if strict
        else watch_bucket(
            analysis.instrument.tradeable,
            analysis.shortable,
            side,
            conflict_4h,
            expected_rr,
            score,
        )
    )
    if category == "WATCH_ONLY":
        decision = "NO_TRADE"

    trigger_condition = (
        "Closed 5m liquidity sweep below prior liquidity -> reclaim -> bullish 5m "
        f"structure shift confirmation near {round_to_tick(trigger_price, analysis.instrument.tick_size)}, "
        "with non-opposing closed 15m structure"
        if side == "long"
        else "Closed 5m liquidity sweep above prior liquidity -> reclaim -> bearish 5m "
        f"structure shift confirmation near {round_to_tick(trigger_price, analysis.instrument.tick_size)}, "
        "with non-opposing closed 15m structure"
    )

    derivatives = analysis.derivatives or {}
    missing = list(analysis.missing_data)
    if not derivatives:
        missing.append("Coinalyze OI/funding context unavailable for this symbol")

    weakest = (
        liquidity_reasons[0]
        if liquidity_reasons
        else (
            "Trigger not confirmed"
            if not triggered
            else "The setup has not been prospectively backtested"
        )
    )

    why_now = [
        f"15m structure: {analysis.structure_15m}",
        f"1H structure: {analysis.structure_1h}",
        f"5m relative volume: {analysis.volume_ratio_5m:.2f}x",
        f"Relative strength vs BTC: 1H {analysis.relative_strength_1h:+.2f}%, 4H {analysis.relative_strength_4h:+.2f}%",
    ]
    if triggered:
        why_now.append("Latest closed 5m bar completed the sweep/reclaim/structure confirmation sequence")
    if conflict_4h:
        why_now.append("4H structure conflicts with the side but is context-only in v0.7.3")
    if vwap_reclaim:
        why_now.append("Rolling 24H VWAP reclaim/rejection detected")

    return {
        "symbol": analysis.instrument.symbol,
        "base_asset": analysis.instrument.base,
        "quote_asset": "USDC",
        "strategy_mode": "DAY_TRADE",
        "side": side,
        "category": category,
        "state": state,
        "grade": displayed_grade,
        "technical_grade": technical_grade,
        "watch_bucket": candidate_watch_bucket,
        "decision": decision,
        "setup_type": setup_type,
        "last_price": round_to_tick(current, analysis.instrument.tick_size),
        "tradeable": analysis.instrument.tradeable,
        "shortable": analysis.shortable,
        "execution_status": execution_status,
        "execution_modes": (
            ["USDC_SPOT"]
            if side == "long"
            else (["USDC_SPOT_MARGIN_SHORT"] if analysis.shortable else [])
        ),
        "expansion_score": round(analysis.expansion_score, 2),
        "direction_score": round(analysis.direction_score, 2),
        "side_direction_score": round(side_direction, 2),
        "quality_score": round(analysis.quality_score, 2),
        "setup_score": round(score, 2),
        "context_4h": analysis.structure_4h,
        "structure_1h": analysis.structure_1h,
        "structure_15m": analysis.structure_15m,
        "timeframe_conflict": conflict_4h,
        "trigger": {
            "timeframe": "5m",
            "condition": trigger_condition,
            "price": round_to_tick(trigger_price, analysis.instrument.tick_size),
            "requires_close": True,
            "volume_confirmation": f">={DAY_TRIGGER_VOLUME_RATIO:.1f}x prior 20-bar mean volume on confirmation",
            "triggered": triggered,
            "model": "LIQUIDITY_SWEEP_RECLAIM_5M_STRUCTURE_15M_CONFIRMATION",
            "sweep_confirmation": sweep_trigger,
        },
        "entry_zone": {
            "low": round_to_tick(min(entry_low, entry_high), analysis.instrument.tick_size),
            "high": round_to_tick(max(entry_low, entry_high), analysis.instrument.tick_size),
        },
        "stop": round_to_tick(stop, analysis.instrument.tick_size),
        "invalidation": invalidation,
        "targets": [
            round_to_tick(gross_tp1, analysis.instrument.tick_size),
            round_to_tick(gross_tp2, analysis.instrument.tick_size),
            round_to_tick(gross_tp3, analysis.instrument.tick_size),
        ],
        "expected_rr": round(expected_rr, 2),
        "expected_holding_time": "30 minutes to 8 hours",
        "metrics": {
            "turnover_24h_usdc": round(analysis.instrument.turnover_24h, 2),
            "spread_bps": round(analysis.instrument.spread_bps, 2),
            "volume_ratio_5m": round(analysis.volume_ratio_5m, 3),
            "volume_ratio_15m": round(analysis.volume_ratio_15m, 3),
            "return_15m_pct": round(analysis.return_15m_pct, 4),
            "return_1h_pct": round(analysis.return_1h_pct, 4),
            "return_4h_pct": round(analysis.return_4h_pct, 4),
            "relative_strength_1h_pct": round(analysis.relative_strength_1h, 4),
            "relative_strength_4h_pct": round(analysis.relative_strength_4h, 4),
            "rolling_vwap_24h": round_to_tick(
                analysis.rolling_vwap_24h, analysis.instrument.tick_size
            ),
            "atr_5m": round_to_tick(analysis.atr_5m, analysis.instrument.tick_size),
            "atr_15m": round_to_tick(analysis.atr_15m, analysis.instrument.tick_size),
            "atr_ratio_15m": round(analysis.atr_ratio_15m, 3),
            "distance_to_trigger_atr_5m": round(distance_atr, 3),
            "sweep_confirmation": sweep_trigger,
            "four_hour_conflict_context_only": conflict_4h,
            "nearest_structural_barrier": (
                None
                if barrier is None
                else round_to_tick(barrier, analysis.instrument.tick_size)
            ),
            "barrier_rr_gross": (
                None if barrier_rr is None else round(barrier_rr, 4)
            ),
            "barrier_rr_net": (
                None if barrier_net_rr is None else round(barrier_net_rr, 4)
            ),
            "expected_rr_without_barrier": round(expected_rr_without_barrier, 4),
            "expected_rr_with_barrier": round(expected_rr, 4),
            "target_path_valid": target_path_valid,
            "barrier_before_tp2": barrier_before_tp2,
            "barrier_source": barrier_info,
            "rr_denominator": "PRE_COST_STOP_DISTANCE",
            "target_definition": "NET_R_AFTER_ROUND_TRIP_COST",
            "assumed_round_trip_cost_bps": DAY_ASSUMED_ROUND_TRIP_COST_BPS,
            "liquidity_reasons": liquidity_reasons,
            "max_borrowing_amount": analysis.max_borrowing_amount,
        },
        "derivatives": derivatives,
        "why_now": why_now,
        "bullish_scenario": (
            "Closed 5m breakout/reclaim holds and 15m structure continues higher."
        ),
        "bearish_scenario": (
            "Closed 5m breakdown/rejection holds and 15m structure continues lower."
        ),
        "weakest_point": weakest,
        "risks": [
            "5m signals are vulnerable to false breakouts",
            "A 2–3% BTC move against the trade can invalidate intraday structure",
            "Model RR uses a configurable cost assumption, not the account's exact fee tier",
            "Only backtests and journal records matching strategy v0.7.3 are comparable with this live engine",
        ],
        "data_quality": "GOOD" if analysis.instrument.tradeable else "PARTIAL",
        "missing_data": sorted(set(missing)),
        "data_as_of": now.isoformat(),
    }


def build_day_regime(
    analyses: list[DayAnalysis],
    now: datetime,
    coinalyze_request_ok: bool,
    coinalyze_enriched_symbols: int,
    borrowability_ok: bool,
) -> dict[str, Any]:
    btc = next(
        (item for item in analyses if item.instrument.symbol == "BTCUSDC"), None
    )
    bullish = sum(1 for item in analyses if item.direction_score >= 20)
    bearish = sum(1 for item in analyses if item.direction_score <= -20)
    breadth = 100.0 * bullish / max(len(analyses), 1)

    if bullish >= bearish * 1.5 and bullish >= 4:
        preferred = "long"
    elif bearish >= bullish * 1.5 and bearish >= 4:
        preferred = "short"
    else:
        preferred = "neutral"

    volatility = "normal"
    if btc:
        if btc.atr_ratio_15m >= 1.5:
            volatility = "expanding"
        elif btc.atr_ratio_15m <= 0.75:
            volatility = "compressed"

    if len(analyses) > 0 and coinalyze_enriched_symbols == len(analyses):
        coinalyze_quality = "GOOD"
    elif coinalyze_enriched_symbols > 0:
        coinalyze_quality = "PARTIAL"
    else:
        coinalyze_quality = "DEGRADED" if not coinalyze_request_ok else "PARTIAL"

    overall = (
        "GOOD"
        if coinalyze_quality == "GOOD" and borrowability_ok
        else "PARTIAL"
    )
    return {
        "strategy_mode": "DAY_TRADE",
        "data_as_of": now.isoformat(),
        "data_quality": overall,
        "btc_structure_4h": btc.structure_4h if btc else None,
        "btc_structure_1h": btc.structure_1h if btc else None,
        "btc_structure_15m": btc.structure_15m if btc else None,
        "alt_breadth": round(breadth, 2),
        "bullish_symbols": bullish,
        "bearish_symbols": bearish,
        "volatility_regime": volatility,
        "preferred_side": preferred,
        "source_quality": {
            "Bybit EU market data": "GOOD",
            "Bybit EU Spot Margin": "GOOD" if borrowability_ok else "PARTIAL",
            "Coinalyze derivatives": coinalyze_quality,
        },
        "notes": [
            "Day-trade v0.7.3 uses 4H/1H as context; live trigger is closed 5m sweep/reclaim/structure confirmation with non-opposing closed 15m structure.",
            "4H conflict is context-only and does not veto strict eligibility or execution.",
            "Coinalyze data is aggregated and not Bybit EU-specific unless explicitly marked.",
        ],
    }


async def upsert_cache(
    connection: asyncpg.Connection,
    key: str,
    payload: dict[str, Any],
) -> None:
    await connection.execute(
        """
        INSERT INTO radar_cache (cache_key, payload, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (cache_key)
        DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
        """,
        key,
        json.dumps(payload, ensure_ascii=False),
    )


async def persist_day_results(
    scan: dict[str, Any],
    setups: list[dict[str, Any]],
    status: dict[str, Any],
    bars_by_symbol: dict[str, list[Bar]],
    analyses: list[DayAnalysis],
) -> dict[str, Any]:
    connection = await asyncpg.connect(DATABASE_URL, timeout=30)
    try:
        async with connection.transaction():
            journal_status = await persist_day_journal(
                connection,
                setups,
                bars_by_symbol,
                scan,
                status,
            )
            scan["journal"] = journal_status
            status["journal"] = journal_status

            try:
                # Research-only sidecar: isolate recorder SQL in a savepoint so
                # any research failure cannot abort live journal/cache persistence.
                async with connection.transaction():
                    funnel_status = await persist_v073_prospective_funnel(
                        connection,
                        analyses,
                        captured_at=datetime.fromisoformat(scan["data_as_of"]),
                        source_commit_sha=SOURCE_COMMIT_SHA,
                        volume_confirmation_ratio=DAY_TRIGGER_VOLUME_RATIO,
                        live_setups=setups,
                    )
            except Exception as exc:
                funnel_status = {
                    "status": "DEGRADED",
                    "research_only": True,
                    "label_free": True,
                    "outcome_labels_stored": False,
                    "spec_version": "v073-prospective-funnel-v1",
                    "strategy_version": DAY_STRATEGY_VERSION,
                    "captured_at": scan["data_as_of"],
                    "source_commit_sha": SOURCE_COMMIT_SHA,
                    "reason": str(exc),
                }
            scan["prospective_funnel"] = funnel_status
            status["prospective_funnel"] = funnel_status

            await upsert_cache(connection, "day_trade_scan", scan)
            await upsert_cache(connection, "day_trade_status", status)

            best_by_symbol: dict[str, dict[str, Any]] = {}
            for setup in setups:
                current = best_by_symbol.get(setup["symbol"])
                if current is None or setup["setup_score"] > current["setup_score"]:
                    best_by_symbol[setup["symbol"]] = setup
            for setup in best_by_symbol.values():
                await upsert_cache(
                    connection,
                    f"day_trade_setup:{setup['symbol']}",
                    setup,
                )

            # Compact, two-sided symbol audit cache. This is deliberately
            # separate from the full scan so GPT Actions never need to pull
            # the oversized raw day_trade_scan payload for calculation audits.
            audit_by_symbol: dict[str, dict[str, Any]] = {}
            for setup in setups:
                record = audit_by_symbol.setdefault(
                    setup["symbol"],
                    {
                        "strategy_mode": "DAY_TRADE",
                        "strategy_version": DAY_STRATEGY_VERSION,
                        "data_as_of": scan["data_as_of"],
                        "data_as_of_budapest": scan["data_as_of_budapest"],
                        "symbol": setup["symbol"],
                        "long": None,
                        "short": None,
                        "notes": [
                            "Compact audit payload; no ranking or fallback transformation is applied.",
                            "Both sides come from the same cached day-trade worker snapshot.",
                            "Barrier provenance is emitted only for confirmed 15m pivots outside the trigger window.",
                        ],
                    },
                )
                record[setup["side"]] = compact_day_audit_side(setup)

            for symbol, audit_payload in audit_by_symbol.items():
                await upsert_cache(
                    connection,
                    f"day_trade_audit:{symbol}",
                    audit_payload,
                )
        return journal_status
    finally:
        await connection.close()


async def run() -> None:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    started = datetime.now(timezone.utc)
    timeout = httpx.Timeout(30.0, connect=15.0)
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=10)

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": f"Bybit-EU-Trading-Radar-Day/{DAY_STRATEGY_VERSION}"},
    ) as client:
        bybit = BybitAPI(client)
        coinalyze = CoinalyzeAPI(client)

        instruments, tickers = await asyncio.gather(
            bybit.instruments(),
            bybit.tickers(),
        )
        universe, exclusions, universe_stats = normalize_usdc_universe(
            instruments, tickers
        )
        if not universe:
            raise RuntimeError("No eligible Bybit EU USDC day-trade markets")

        fast_semaphore = asyncio.Semaphore(DAY_FAST_CONCURRENCY)
        fetched = await asyncio.gather(
            *(fetch_fast(bybit, item, fast_semaphore) for item in universe)
        )
        fast_by_symbol = {
            item[0].symbol: item for item in fetched if item is not None
        }

        for retry_pass in range(DAY_RETRY_PASSES):
            missing = [
                item for item in universe if item.symbol not in fast_by_symbol
            ]
            if not missing:
                break
            await asyncio.sleep(1.5 * (retry_pass + 1))
            retry_semaphore = asyncio.Semaphore(max(1, DAY_FAST_CONCURRENCY // 2))
            retried = await asyncio.gather(
                *(fetch_fast(bybit, item, retry_semaphore) for item in missing)
            )
            for item in retried:
                if item is not None:
                    fast_by_symbol[item[0].symbol] = item

        fast_failed_symbols = sorted(
            item.symbol for item in universe if item.symbol not in fast_by_symbol
        )
        fast_results: list[FastResult] = []
        fast_calculation_failures: list[dict[str, str]] = []
        for instrument, bars_5m, bars_15m in fast_by_symbol.values():
            try:
                fast_results.append(
                    calculate_fast_result(instrument, bars_5m, bars_15m)
                )
            except Exception as exc:
                fast_calculation_failures.append({
                    "symbol": instrument.symbol,
                    "reason": str(exc),
                })

        deep_universe = select_deep_universe(fast_results)
        context_semaphore = asyncio.Semaphore(DAY_CONTEXT_CONCURRENCY)
        context_fetched = await asyncio.gather(
            *(fetch_context(bybit, item, context_semaphore) for item in deep_universe)
        )
        context_valid = [item for item in context_fetched if item is not None]
        context_failed_symbols = sorted(
            item.instrument.symbol for item in deep_universe
            if item.instrument.symbol not in {
                row[0].instrument.symbol for row in context_valid
            }
        )

        btc_context = next(
            (item for item in context_valid if item[0].instrument.symbol == "BTCUSDC"),
            None,
        )
        btc_return_1h = (
            bar_return_pct(btc_context[0].bars_15m, 4) if btc_context else 0.0
        )
        btc_return_4h = (
            bar_return_pct(btc_context[1], 4) if btc_context else 0.0
        )

        analyses: list[DayAnalysis] = []
        calculation_failures: list[dict[str, str]] = []
        for fast, bars_1h, bars_4h in context_valid:
            try:
                analyses.append(
                    analyze_day_market(
                        fast,
                        bars_1h,
                        bars_4h,
                        btc_return_1h,
                        btc_return_4h,
                    )
                )
            except Exception as exc:
                calculation_failures.append({
                    "symbol": fast.instrument.symbol,
                    "reason": str(exc),
                })

        coinalyze_ok, coinalyze_error = await enrich_coinalyze(
            analyses, coinalyze
        )
        borrow_ok, borrow_error = await apply_shortability(analyses, bybit)
        now = datetime.now(timezone.utc)

        all_candidates: list[dict[str, Any]] = []
        for analysis in analyses:
            for side in ("long", "short"):
                candidate = build_day_candidate(analysis, side, now)
                if candidate is not None:
                    all_candidates.append(candidate)

        strict_longs = sorted(
            [
                item for item in all_candidates
                if item["side"] == "long" and item["category"] == "STRICT"
            ],
            key=lambda item: item["setup_score"],
            reverse=True,
        )
        strict_shorts = sorted(
            [
                item for item in all_candidates
                if item["side"] == "short" and item["category"] == "STRICT"
            ],
            key=lambda item: item["setup_score"],
            reverse=True,
        )
        watch_longs = sorted(
            [
                item for item in all_candidates
                if item["side"] == "long" and item["category"] == "WATCH_ONLY"
            ],
            key=watch_rank,
            reverse=True,
        )
        watch_shorts = sorted(
            [
                item for item in all_candidates
                if item["side"] == "short" and item["category"] == "WATCH_ONLY"
            ],
            key=watch_rank,
            reverse=True,
        )

        coinalyze_enriched_count = sum(
            1 for item in analyses if item.derivatives
        )
        regime = build_day_regime(
            analyses,
            now,
            coinalyze_ok,
            coinalyze_enriched_count,
            borrow_ok,
        )
        data_quality = regime["data_quality"]
        coverage = {
            **universe_stats,
            "fast_eligible_pairs": len(universe),
            "fast_scanned_pairs": len(fast_results),
            "fast_failed_pairs": len(fast_failed_symbols),
            "fast_failed_symbols": fast_failed_symbols,
            "fast_calculation_failed_pairs": len(fast_calculation_failures),
            "fast_calculation_failures": fast_calculation_failures,
            "deep_requested_pairs": len(deep_universe),
            "deep_analyzed_pairs": len(analyses),
            "deep_context_failed_pairs": len(context_failed_symbols),
            "deep_context_failed_symbols": context_failed_symbols,
            "deep_calculation_failed_pairs": len(calculation_failures),
            "deep_calculation_failures": calculation_failures,
            "coinalyze_enriched_symbols": coinalyze_enriched_count,
            "borrowability_checked_symbols": len(analyses) if borrow_ok else 0,
        }

        scan = {
            "strategy_mode": "DAY_TRADE",
            "data_as_of": now.isoformat(),
            "data_as_of_budapest": now.astimezone(BUDAPEST).isoformat(),
            "data_quality": data_quality,
            "market_regime": regime,
            "strict_longs": strict_longs[:DAY_OUTPUT_LIMIT],
            "strict_shorts": strict_shorts[:DAY_OUTPUT_LIMIT],
            "watch_only_longs": watch_longs[:DAY_OUTPUT_LIMIT],
            "watch_only_shorts": watch_shorts[:DAY_OUTPUT_LIMIT],
            "coverage": coverage,
            "assumptions": {
                "holding_time": "30 minutes to 8 hours",
                "context_timeframes": ["4H", "1H"],
                "setup_timeframe": "15m",
                "trigger_timeframe": "5m closed sweep/reclaim/structure confirmation",
                "confirmation_timeframe": "15m closed non-opposing structure",
                "four_hour_role": "CONTEXT_ONLY",
                "strategy_version": DAY_STRATEGY_VERSION,
                "minimum_expected_rr_after_assumed_cost": DAY_MIN_RR,
                "assumed_round_trip_cost_bps": DAY_ASSUMED_ROUND_TRIP_COST_BPS,
                "minimum_turnover_usdc": DAY_MIN_TURNOVER_USDC,
                "max_spread_bps": DAY_MAX_SPREAD_BPS,
                "rr_denominator": "PRE_COST_STOP_DISTANCE",
                "target_definition": "NET_R_AFTER_ROUND_TRIP_COST",
                "barrier_model": "CONFIRMED_15M_PIVOT_EXCLUDING_TRIGGER_WINDOW",
            },
            "exclusions": exclusions[:100],
            "notes": [
                "Prospective journal records are version-separated; v0.7.3 creates no historical backfill.",
                "Fast coverage scans all eligible USDC pairs on 5m/15m; 1H/4H deep context is limited to promoted symbols.",
                "WATCH_ONLY items are not entries.",
            ],
        }

        elapsed = (
            datetime.now(timezone.utc) - started
        ).total_seconds()
        status = {
            "checked_at": now.isoformat(),
            "worker": {
                "status": "ok",
                "strategy_mode": "DAY_TRADE",
                "source_commit_sha": SOURCE_COMMIT_SHA,
                "duration_seconds": round(elapsed, 2),
                "strict_long_candidates": len(strict_longs),
                "strict_short_candidates": len(strict_shorts),
                **coverage,
            },
            "sources": [
                {
                    "source": "Bybit EU",
                    "status": "ok",
                    "data_as_of": now.isoformat(),
                    "missing_fields": [],
                },
                {
                    "source": "Bybit EU Spot Margin",
                    "status": "ok" if borrow_ok else "partial",
                    "data_as_of": now.isoformat() if borrow_ok else None,
                    "missing_fields": [] if borrow_ok else [
                        borrow_error or "Borrowability unavailable"
                    ],
                },
                {
                    "source": "Coinalyze",
                    "status": (
                        "ok"
                        if coinalyze_enriched_count == len(analyses) and len(analyses) > 0
                        else "partial"
                        if coinalyze_enriched_count > 0
                        else "degraded"
                    ),
                    "data_as_of": (
                        now.isoformat() if coinalyze_enriched_count > 0 else None
                    ),
                    "coverage": f"{coinalyze_enriched_count}/{len(analyses)}",
                    "missing_fields": (
                        []
                        if coinalyze_enriched_count == len(analyses)
                        else [
                            coinalyze_error
                            or "Derivatives enrichment is only partially available"
                        ]
                    ),
                },
            ],
        }

        bars_by_symbol = {
            item.instrument.symbol: item.bars_5m for item in fast_results
        }
        journal_status = await persist_day_results(
            scan,
            all_candidates,
            status,
            bars_by_symbol,
            analyses,
        )
        print(
            "Day worker complete: "
            f"fast={len(fast_results)}/{len(universe)}, "
            f"deep={len(analyses)}/{len(deep_universe)}, "
            f"strict_longs={len(strict_longs)}, "
            f"strict_shorts={len(strict_shorts)}, "
            f"journal_new={journal_status.get('new_signals', 0)}, "
            f"journal_active={journal_status.get('active_signals', 0)}, "
            f"quality={data_quality}, duration={elapsed:.1f}s"
        )


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
