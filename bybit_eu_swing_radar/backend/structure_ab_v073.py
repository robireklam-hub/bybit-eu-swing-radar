"""Single-hypothesis v0.7.3 structure A/B research replay.

RESEARCH ONLY. This module never mutates live day-trade strategy state.

A: current v0.7.3 5m structure level = extreme of the six bars before sweep.
B: latest fully confirmed 2-left/2-right pivot inside the 12-bar liquidity
   window, confirmed entirely before the sweep. No fallback and no tuning.

All downstream live v0.7.3 gates remain unchanged. The replay uses a fixed
180-day window ending at the source v0.7.3 backtest end, 20 bps costs, an
8-hour horizon, current active USDC universe, and six 30-day reporting blocks.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import json
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import asyncpg
import httpx

from backtest import (
    HistoricalBybitAPI,
    _higher_prefix,
    _ms,
    _prefix_sums,
    _return_pct,
    _rolling_sum,
    aggregate_bars,
)
from day_worker import (
    DAY_MAX_SPREAD_BPS,
    DAY_MIN_TURNOVER_USDC,
    DAY_TRIGGER_VOLUME_RATIO,
    analyze_day_market,
    calculate_fast_result,
)
from diagnostics_v073 import (
    DIAGNOSTIC_BASE_COST_BPS,
    DIAGNOSTIC_BASE_HORIZON_HOURS,
    DIAGNOSTIC_SHORT_MODE,
    build_research_candidate,
    evaluate_path,
    gate_snapshot,
    modeled_spread_bps,
)
from diagnostics_v073_perf import fast_classify_15m_structure, fast_scan_sweep_setups
from sweep_research import (
    FIVE_MIN_MS,
    SweepResearchConfig,
    iso_from_ms,
    normalize_bars,
    volume_ratio_at_index,
)
from worker import Bar, Instrument, safe_float

STRATEGY_VERSION = "0.7.3"
STRUCTURE_AB_JOB_NAME = os.getenv(
    "V073_STRUCTURE_AB_JOB_NAME", "v073-180d-pivot2l2r-structure-ab-v1"
).strip()
DATABASE_URL = os.getenv("DATABASE_URL", "")
STRUCTURE_AB_ENABLED = os.getenv(
    "V073_STRUCTURE_AB_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
STRUCTURE_AB_LOOKBACK_DAYS = 180
STRUCTURE_AB_WARMUP_DAYS = 14
STRUCTURE_AB_BLOCK_DAYS = 30
STRUCTURE_AB_BATCH_SYMBOLS = min(
    max(int(os.getenv("V073_STRUCTURE_AB_BATCH_SYMBOLS", "2")), 1), 3
)
STRUCTURE_AB_HTTP_CONCURRENCY = min(
    max(int(os.getenv("V073_STRUCTURE_AB_HTTP_CONCURRENCY", "3")), 1), 6
)
STRUCTURE_AB_STALE_RUN_MINUTES = min(
    max(int(os.getenv("V073_STRUCTURE_AB_STALE_RUN_MINUTES", "30")), 10), 240
)
STRUCTURE_AB_RUN_LOCK_NAME = "trading-radar:day-research:v073-pivot-structure-ab"

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
PIVOT_SEARCH_BARS = 12

GO_MIN_PRIMARY = 300
GO_MIN_SIDE_PRIMARY = 100
GO_MIN_AVG_NET_R = 0.10
GO_MIN_PROFIT_FACTOR = 1.15
GO_MIN_NON_NEGATIVE_BLOCKS = 4
GO_MAX_POSITIVE_BLOCK_CONCENTRATION = 0.50

WARNINGS = [
    "Research-only A/B replay; live v0.7.3 trigger/scoring/execution is never changed.",
    "Exactly one structural hypothesis is tested: fixed 6-bar range extreme versus latest confirmed 2L/2R pivot.",
    "The pivot must be fully confirmed before the sweep and lie inside the existing 12-bar liquidity window; there is no fallback.",
    "All downstream v0.7.3 gates remain fixed, including volume 1.3x, 15m confirmation, scores, target path, net RR 1.8 and liquidity/execution.",
    "The 180-day universe is the current completed v0.7.3 backtest universe, so survivorship bias remains.",
    "Historical spread is modelled from rolling 24h turnover; historical short borrowability is unavailable and shorts are technical research only.",
    "Coinalyze OI/funding is excluded and never acts as a hard gate.",
    "Six fixed chronological 30-day blocks are reported; no block is used to tune the pivot model.",
    "Same-candle stop and TP2 is conservatively treated as stop-first.",
]

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS day_trade_structure_ab_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE,
    job_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','PARTIAL','FAILED')),
    source_backtest_job_id BIGINT,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    warmup_start_at TIMESTAMPTZ NOT NULL,
    parameters JSONB NOT NULL,
    universe JSONB NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_symbols INTEGER NOT NULL DEFAULT 0,
    completed_symbols INTEGER NOT NULL DEFAULT 0,
    failed_symbols INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_day_structure_ab_jobs
    ON day_trade_structure_ab_jobs (strategy_version, job_name, created_at DESC);

CREATE TABLE IF NOT EXISTS day_trade_structure_ab_symbols (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES day_trade_structure_ab_jobs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    bars_fetched INTEGER NOT NULL DEFAULT 0,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_error TEXT,
    UNIQUE(job_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_day_structure_ab_symbols_queue
    ON day_trade_structure_ab_symbols (job_id, status, id);
"""


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _parse_iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    return _ms(datetime.fromisoformat(value))


def _pivot_value(bar: Any, side: str) -> float:
    return float(bar.high if side == "long" else bar.low)


def is_confirmed_pivot(
    bars: list[Any],
    index: int,
    side: str,
    *,
    left: int = PIVOT_LEFT,
    right: int = PIVOT_RIGHT,
) -> bool:
    """Return True only for a strict pivot with all confirmation bars present."""
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    if index - left < 0 or index + right >= len(bars):
        return False
    center = _pivot_value(bars[index], side)
    neighbors = [
        _pivot_value(bars[i], side)
        for i in range(index - left, index + right + 1)
        if i != index
    ]
    if side == "long":
        return all(center > value for value in neighbors)
    return all(center < value for value in neighbors)


def last_confirmed_pivot_before_sweep(
    bars: list[Any],
    sweep_index: int,
    side: str,
    *,
    search_bars: int = PIVOT_SEARCH_BARS,
) -> dict[str, Any] | None:
    """Find latest 2L/2R pivot whose right confirmation closes before sweep."""
    last_allowed = sweep_index - 1 - PIVOT_RIGHT
    first_allowed = max(PIVOT_LEFT, sweep_index - search_bars)
    if last_allowed < first_allowed:
        return None
    for index in range(last_allowed, first_allowed - 1, -1):
        if is_confirmed_pivot(bars, index, side):
            return {
                "index": index,
                "level": _pivot_value(bars[index], side),
                "time": iso_from_ms(int(bars[index].start_ms)),
                "bars_before_sweep": sweep_index - index,
            }
    return None


def _15m_confirms(side: str, state: str) -> bool:
    if state == "INSUFFICIENT_DATA":
        return False
    if side == "long":
        return state != "BEARISH_SHIFT"
    return state != "BULLISH_SHIFT"


def pivot_structure_event(
    baseline_event: dict[str, Any],
    bars_5m: list[Any],
    bars_15m: list[Any],
    bar_index: dict[int, int],
    *,
    config: SweepResearchConfig,
) -> dict[str, Any]:
    """Transform one baseline raw sweep into the fixed pivot-structure model."""
    event = dict(baseline_event)
    event["structure_model"] = "LAST_CONFIRMED_PIVOT_2L2R"
    event["pivot_left"] = PIVOT_LEFT
    event["pivot_right"] = PIVOT_RIGHT
    event["pivot_search_bars"] = PIVOT_SEARCH_BARS
    event["pivot_index"] = None
    event["pivot_time"] = None
    event["pivot_bars_before_sweep"] = None

    if not event.get("sweep_detected") or not event.get("reclaim_confirmed"):
        event["structure_shift_5m"] = False
        event["entry_ready"] = False
        return event

    sweep_index = int(event["sweep_index"])
    pivot = last_confirmed_pivot_before_sweep(bars_5m, sweep_index, str(event["side"]))
    event["structure_shift_5m"] = False
    event["structure_shift_level_5m"] = None
    event["structure_shift_time_5m"] = None
    event["bars_from_sweep_to_confirmation"] = None
    event["candidate_entry"] = None
    event["volume_ratio_5m"] = None
    event["volume_confirmed"] = False
    event["structure_15m_state"] = "NOT_EVALUATED"
    event["structure_confirmed_15m"] = False
    event["entry_ready"] = False

    reasons = [
        reason
        for reason in list(event.get("failure_reasons") or [])
        if reason not in {
            "NO_5M_STRUCTURE_SHIFT",
            "VOLUME_NOT_CONFIRMED",
            "15M_STRUCTURE_OPPOSES_OR_UNAVAILABLE",
        }
    ]
    event["failure_reasons"] = reasons

    if pivot is None:
        event["failure_reasons"].append("NO_CONFIRMED_PIVOT_2L2R")
        return event

    event["pivot_index"] = pivot["index"]
    event["pivot_time"] = pivot["time"]
    event["pivot_bars_before_sweep"] = pivot["bars_before_sweep"]
    event["structure_shift_level_5m"] = float(pivot["level"])

    reclaim_ms = _parse_iso_ms(event.get("reclaim_time"))
    reclaim_index = None if reclaim_ms is None else bar_index.get(reclaim_ms)
    if reclaim_index is None:
        event["failure_reasons"].append("RECLAIM_INDEX_UNAVAILABLE")
        return event

    confirmation_end = min(
        len(bars_5m) - 1,
        sweep_index + config.max_confirmation_bars,
    )
    side = str(event["side"])
    level = float(pivot["level"])
    confirmation_index: int | None = None
    for index in range(reclaim_index, confirmation_end + 1):
        close = float(bars_5m[index].close)
        shifted = close > level if side == "long" else close < level
        if shifted:
            confirmation_index = index
            break

    if confirmation_index is None:
        event["failure_reasons"].append("NO_PIVOT_STRUCTURE_SHIFT_WITHIN_WINDOW")
        return event

    confirmation_bar = bars_5m[confirmation_index]
    event["structure_shift_5m"] = True
    event["structure_shift_time_5m"] = iso_from_ms(int(confirmation_bar.start_ms))
    event["bars_from_sweep_to_confirmation"] = confirmation_index - sweep_index
    event["candidate_entry"] = float(confirmation_bar.close)

    volume_ratio = volume_ratio_at_index(
        bars_5m,
        confirmation_index,
        config.volume_lookback,
    )
    event["volume_ratio_5m"] = volume_ratio
    event["volume_confirmed"] = bool(
        volume_ratio is not None
        and volume_ratio >= config.volume_confirmation_ratio
    )
    if not event["volume_confirmed"]:
        event["failure_reasons"].append("VOLUME_NOT_CONFIRMED")

    confirmation_close_ms = int(confirmation_bar.start_ms) + FIVE_MIN_MS
    state_15m = fast_classify_15m_structure(
        bars_15m,
        confirmation_close_ms,
        config.structure_lookback_15m,
    )
    event["structure_15m_state"] = state_15m
    event["structure_confirmed_15m"] = _15m_confirms(side, state_15m)
    if not event["structure_confirmed_15m"]:
        event["failure_reasons"].append("15M_STRUCTURE_OPPOSES_OR_UNAVAILABLE")

    event["entry_ready"] = bool(
        event["sweep_detected"]
        and event["reclaim_confirmed"]
        and event["structure_shift_5m"]
        and event["volume_confirmed"]
        and event["structure_confirmed_15m"]
    )
    return event


def _empty_counter() -> dict[str, int]:
    return {
        "sweeps": 0,
        "reclaims": 0,
        "structure_shifts": 0,
        "volume_confirmed": 0,
        "structure_15m_confirmed": 0,
        "candidates": 0,
        "strict_trade_raw": 0,
        "primary_strict_trades": 0,
    }


def _count_event(counter: dict[str, int], event: dict[str, Any]) -> None:
    counter["sweeps"] += int(bool(event.get("sweep_detected")))
    counter["reclaims"] += int(bool(event.get("reclaim_confirmed")))
    counter["structure_shifts"] += int(bool(event.get("structure_shift_5m")))
    counter["volume_confirmed"] += int(bool(event.get("volume_confirmed")))
    counter["structure_15m_confirmed"] += int(bool(event.get("structure_confirmed_15m")))


def _build_analysis_cache(
    symbol: str,
    symbol_meta: dict[str, Any],
    bars_5m: list[Bar],
    btc_bars_5m: list[Bar],
):
    bars15 = aggregate_bars(bars_5m, 15)
    bars1h = aggregate_bars(bars_5m, 60)
    bars4h = aggregate_bars(bars_5m, 240)
    btc15 = aggregate_bars(btc_bars_5m, 15)
    btc1h = aggregate_bars(btc_bars_5m, 60)
    btc4h = aggregate_bars(btc_bars_5m, 240)

    closes15 = [bar.start_ms + 15 * 60 * 1000 for bar in bars15]
    closes1h = [bar.start_ms + 60 * 60 * 1000 for bar in bars1h]
    closes4h = [bar.start_ms + 240 * 60 * 1000 for bar in bars4h]
    btc_closes15 = [bar.start_ms + 15 * 60 * 1000 for bar in btc15]
    btc_closes1h = [bar.start_ms + 60 * 60 * 1000 for bar in btc1h]
    btc_closes4h = [bar.start_ms + 240 * 60 * 1000 for bar in btc4h]

    turnover_prefix = _prefix_sums(bar.turnover for bar in bars_5m)
    volume_prefix = _prefix_sums(bar.volume for bar in bars_5m)
    tick_size = max(safe_float(symbol_meta.get("tick_size"), 0.0), 1e-12)
    cache: dict[int, tuple[Any, float, float] | None] = {}

    def get(confirm_index: int) -> tuple[Any, float, float] | None:
        if confirm_index in cache:
            return cache[confirm_index]
        evaluation_time_ms = bars_5m[confirm_index].start_ms + FIVE_MIN_MS
        bars5_slice = bars_5m[max(0, confirm_index - 219):confirm_index + 1]
        symbol15 = _higher_prefix(bars15, closes15, evaluation_time_ms, 220)
        symbol1h = _higher_prefix(bars1h, closes1h, evaluation_time_ms, 140)
        symbol4h = _higher_prefix(bars4h, closes4h, evaluation_time_ms, 100)
        btc15_slice = _higher_prefix(btc15, btc_closes15, evaluation_time_ms, 220)
        btc1h_slice = _higher_prefix(btc1h, btc_closes1h, evaluation_time_ms, 140)
        btc4h_slice = _higher_prefix(btc4h, btc_closes4h, evaluation_time_ms, 100)
        if (
            len(bars5_slice) < 100
            or len(symbol15) < 100
            or len(symbol1h) < 100
            or len(symbol4h) < 80
            or len(btc15_slice) < 100
            or len(btc1h_slice) < 100
            or len(btc4h_slice) < 80
        ):
            cache[confirm_index] = None
            return None

        turnover24 = _rolling_sum(turnover_prefix, confirm_index, 288)
        spread_bps = modeled_spread_bps(turnover24)
        tradeable = (
            turnover24 >= DAY_MIN_TURNOVER_USDC
            and spread_bps <= DAY_MAX_SPREAD_BPS
        )
        current = bars_5m[confirm_index].close
        half_spread = spread_bps / 20_000.0
        instrument = Instrument(
            symbol=symbol,
            base=str(symbol_meta.get("base", symbol.removesuffix("USDC"))),
            quote="USDC",
            margin_trading=str(symbol_meta.get("margin_trading", "none")),
            tick_size=tick_size,
            turnover_24h=turnover24,
            volume_24h=_rolling_sum(volume_prefix, confirm_index, 288),
            last_price=current,
            bid=current * (1.0 - half_spread),
            ask=current * (1.0 + half_spread),
            spread_bps=spread_bps,
            price_change_24h_pct=(
                ((current / bars_5m[confirm_index - 288].close) - 1.0) * 100.0
                if confirm_index >= 288 and bars_5m[confirm_index - 288].close > 0
                else 0.0
            ),
            tradeable=tradeable,
            liquidity_reasons=[] if tradeable else ["HISTORICAL_LIQUIDITY_GATE_FAILED"],
            discovery_source="v073_pivot_structure_ab",
        )
        fast = calculate_fast_result(instrument, bars5_slice, symbol15)
        btc_r1h = _return_pct(btc15_slice, 4)
        btc_r4h = _return_pct(btc1h_slice, 4)
        analysis = analyze_day_market(
            fast,
            symbol1h,
            symbol4h,
            btc_r1h,
            btc_r4h,
        )
        cache[confirm_index] = (analysis, turnover24, spread_bps)
        return cache[confirm_index]

    return bars15, get


def replay_symbol(
    symbol_meta: dict[str, Any],
    bars_5m: list[Bar],
    btc_bars_5m: list[Bar],
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    """Replay the fixed A and B models for one symbol."""
    symbol = str(symbol_meta["symbol"]).upper()
    if len(bars_5m) < 500 or len(btc_bars_5m) < 500:
        return {
            "symbol": symbol,
            "bars_fetched": len(bars_5m),
            "models": {
                "A_RANGE6": {"counters": _empty_counter(), "trades": []},
                "B_PIVOT2L2R": {"counters": _empty_counter(), "trades": []},
            },
            "warning": "INSUFFICIENT_HISTORY",
        }

    bars_5m = sorted(bars_5m, key=lambda bar: bar.start_ms)
    bar_index = {bar.start_ms: index for index, bar in enumerate(bars_5m)}
    research_bars = normalize_bars(bars_5m)
    bars15_worker, analysis_for = _build_analysis_cache(
        symbol, symbol_meta, bars_5m, btc_bars_5m
    )
    research15 = normalize_bars(bars15_worker)

    config = SweepResearchConfig(volume_confirmation_ratio=DAY_TRIGGER_VOLUME_RATIO)
    raw_events: list[dict[str, Any]] = []
    for side in ("long", "short"):
        if side == "short" and DIAGNOSTIC_SHORT_MODE == "disabled":
            continue
        for raw in fast_scan_sweep_setups(
            research_bars,
            side,
            bars_15m=research15,
            config=config,
            include_incomplete=True,
        ):
            item = dict(raw)
            item["side"] = side
            raw_events.append(item)

    raw_events.sort(
        key=lambda event: (
            _parse_iso_ms(event.get("sweep_time")) or 0,
            str(event.get("side")),
            int(event.get("sweep_index") or 0),
        )
    )

    start_ms = _ms(start_at)
    end_ms = _ms(end_at)
    horizon_bars = DIAGNOSTIC_BASE_HORIZON_HOURS * 12
    current_shortable_proxy = bool(symbol_meta.get("current_shortable_proxy"))
    models = {
        "A_RANGE6": {"counters": _empty_counter(), "trades": []},
        "B_PIVOT2L2R": {"counters": _empty_counter(), "trades": []},
    }
    last_primary_exit = {
        ("A_RANGE6", "long"): 0,
        ("A_RANGE6", "short"): 0,
        ("B_PIVOT2L2R", "long"): 0,
        ("B_PIVOT2L2R", "short"): 0,
    }

    for baseline in raw_events:
        sweep_ms = _parse_iso_ms(baseline.get("sweep_time"))
        if sweep_ms is None or sweep_ms < start_ms or sweep_ms >= end_ms:
            continue
        pivot = pivot_structure_event(
            baseline,
            research_bars,
            research15,
            bar_index,
            config=config,
        )
        for model_name, event in (
            ("A_RANGE6", baseline),
            ("B_PIVOT2L2R", pivot),
        ):
            counter = models[model_name]["counters"]
            _count_event(counter, event)
            side = str(event["side"])
            structure_ms = _parse_iso_ms(event.get("structure_shift_time_5m"))
            if structure_ms is None:
                continue
            confirm_index = bar_index.get(structure_ms)
            if confirm_index is None:
                continue
            opened_ms = structure_ms + FIVE_MIN_MS
            if opened_ms < start_ms or opened_ms >= end_ms:
                continue

            cached = analysis_for(confirm_index)
            if cached is None:
                continue
            base_analysis, _, _ = cached
            analysis = copy.copy(base_analysis)
            if side == "long":
                analysis.shortable = False
            elif DIAGNOSTIC_SHORT_MODE == "technical_only":
                analysis.shortable = True
            elif DIAGNOSTIC_SHORT_MODE == "current_proxy":
                analysis.shortable = current_shortable_proxy
            else:
                analysis.shortable = False

            candidate = build_research_candidate(analysis, side, event)
            if candidate is not None:
                counter["candidates"] += 1
            gates = gate_snapshot(
                candidate,
                side,
                event,
                current_shortable_proxy,
            )
            if gates.get("pass_strict_trade"):
                counter["strict_trade_raw"] += 1

            if candidate is None or confirm_index + horizon_bars >= len(bars_5m):
                continue
            targets = list(candidate.get("targets") or [])
            if len(targets) < 3:
                continue
            entry = safe_float(candidate.get("entry"))
            stop = safe_float(candidate.get("stop"))
            future = bars_5m[
                confirm_index + 1:
                min(len(bars_5m), confirm_index + 1 + horizon_bars)
            ]
            path = evaluate_path(
                side,
                entry,
                stop,
                safe_float(targets[0]),
                safe_float(targets[1]),
                safe_float(targets[2]),
                future,
            )
            if path is None:
                continue
            closed_ms = _ms(path["closed_at"])
            key = (model_name, side)
            if opened_ms < last_primary_exit[key]:
                included_primary = False
            else:
                included_primary = True
                last_primary_exit[key] = closed_ms

            if not (included_primary and gates.get("pass_strict_trade")):
                continue

            risk = abs(entry - stop)
            if risk <= 0:
                continue
            cost_r = (entry * DIAGNOSTIC_BASE_COST_BPS / 10_000.0) / risk
            net_r = float(path["gross_r"]) - cost_r
            block_index = int(
                (datetime.fromtimestamp(opened_ms / 1000, tz=timezone.utc) - start_at)
                .total_seconds()
                // (STRUCTURE_AB_BLOCK_DAYS * 86_400)
            )
            if block_index < 0 or block_index >= STRUCTURE_AB_LOOKBACK_DAYS // STRUCTURE_AB_BLOCK_DAYS:
                continue
            trade = {
                "symbol": symbol,
                "side": side,
                "opened_at": datetime.fromtimestamp(
                    opened_ms / 1000, tz=timezone.utc
                ).isoformat(),
                "closed_at": path["closed_at"].isoformat(),
                "block_index": block_index,
                "entry": round(entry, 12),
                "stop": round(stop, 12),
                "net_r": round(net_r, 6),
                "gross_r": round(float(path["gross_r"]), 6),
                "mfe_r": round(float(path["mfe_r"]), 6),
                "mae_r": round(float(path["mae_r"]), 6),
                "exit_reason": str(path["exit_reason"]),
                "structure_level": event.get("structure_shift_level_5m"),
                "bars_from_sweep_to_confirmation": event.get(
                    "bars_from_sweep_to_confirmation"
                ),
            }
            if model_name == "B_PIVOT2L2R":
                trade["pivot_bars_before_sweep"] = event.get(
                    "pivot_bars_before_sweep"
                )
            models[model_name]["trades"].append(trade)
            counter["primary_strict_trades"] += 1

    return {
        "symbol": symbol,
        "bars_fetched": len(bars_5m),
        "models": models,
    }


def aggregate_trades(trades: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(trades)
    net = [float(row["net_r"]) for row in rows]
    positives = [value for value in net if value > 0]
    negatives = [value for value in net if value < 0]
    gains = sum(positives)
    losses = abs(sum(negatives))
    return {
        "sample_size": len(rows),
        "total_net_r": round(sum(net), 6),
        "average_net_r": round(statistics.fmean(net), 6) if net else None,
        "median_net_r": round(statistics.median(net), 6) if net else None,
        "positive_net_rate_pct": (
            round(len(positives) / len(rows) * 100.0, 3) if rows else None
        ),
        "profit_factor": (
            round(gains / losses, 6) if losses > 0 else None
        ),
        "profit_factor_unbounded": bool(gains > 0 and losses == 0),
        "tp2_count": sum(1 for row in rows if row.get("exit_reason") == "TP2"),
        "stop_count": sum(
            1
            for row in rows
            if row.get("exit_reason") in {"STOP", "AMBIGUOUS_STOP_FIRST"}
        ),
        "time_exit_count": sum(
            1 for row in rows if row.get("exit_reason") == "TIME_EXIT"
        ),
        "average_mfe_r": (
            round(statistics.fmean(float(row["mfe_r"]) for row in rows), 6)
            if rows
            else None
        ),
        "average_mae_r": (
            round(statistics.fmean(float(row["mae_r"]) for row in rows), 6)
            if rows
            else None
        ),
    }


def build_report_from_symbol_results(
    symbol_results: list[dict[str, Any]],
    start_at: datetime,
    end_at: datetime,
    *,
    expected_symbols: int | None = None,
) -> dict[str, Any]:
    model_reports: dict[str, Any] = {}
    block_count = STRUCTURE_AB_LOOKBACK_DAYS // STRUCTURE_AB_BLOCK_DAYS
    for model_name in ("A_RANGE6", "B_PIVOT2L2R"):
        trades: list[dict[str, Any]] = []
        counters = _empty_counter()
        for item in symbol_results:
            model = ((item.get("models") or {}).get(model_name) or {})
            trades.extend(list(model.get("trades") or []))
            for key, value in (model.get("counters") or {}).items():
                if key in counters:
                    counters[key] += int(value or 0)

        by_side = {
            side: aggregate_trades(
                row for row in trades if row.get("side") == side
            )
            for side in ("long", "short")
        }
        blocks: list[dict[str, Any]] = []
        for index in range(block_count):
            block_start = start_at + timedelta(
                days=index * STRUCTURE_AB_BLOCK_DAYS
            )
            block_end = min(
                end_at,
                block_start + timedelta(days=STRUCTURE_AB_BLOCK_DAYS),
            )
            metrics = aggregate_trades(
                row for row in trades if int(row.get("block_index", -1)) == index
            )
            blocks.append(
                {
                    "index": index + 1,
                    "start_at": block_start.isoformat(),
                    "end_at": block_end.isoformat(),
                    **metrics,
                }
            )
        positive_block_totals = [
            float(block["total_net_r"])
            for block in blocks
            if float(block["total_net_r"]) > 0
        ]
        positive_total = sum(positive_block_totals)
        concentration = (
            max(positive_block_totals) / positive_total
            if positive_total > 0
            else None
        )
        non_negative_blocks = sum(
            1
            for block in blocks
            if int(block["sample_size"]) > 0
            and float(block["total_net_r"]) >= 0
        )
        model_reports[model_name] = {
            "counters": counters,
            "overall": aggregate_trades(trades),
            "by_side": by_side,
            "blocks_30d": blocks,
            "non_negative_blocks": non_negative_blocks,
            "positive_block_concentration": (
                round(concentration, 6) if concentration is not None else None
            ),
        }

    b = model_reports["B_PIVOT2L2R"]
    overall = b["overall"]
    long_metrics = b["by_side"]["long"]
    short_metrics = b["by_side"]["short"]
    pf = overall.get("profit_factor")
    pf_unbounded = bool(overall.get("profit_factor_unbounded"))
    concentration = b.get("positive_block_concentration")
    checks = {
        "all_symbols_completed": (
            True if expected_symbols is None else len(symbol_results) == expected_symbols
        ),
        "primary_sample_gte_300": int(overall["sample_size"]) >= GO_MIN_PRIMARY,
        "long_sample_gte_100": int(long_metrics["sample_size"]) >= GO_MIN_SIDE_PRIMARY,
        "short_sample_gte_100": int(short_metrics["sample_size"]) >= GO_MIN_SIDE_PRIMARY,
        "average_net_r_gt_0_10": (
            overall["average_net_r"] is not None
            and float(overall["average_net_r"]) > GO_MIN_AVG_NET_R
        ),
        "profit_factor_gte_1_15": (
            pf_unbounded or (pf is not None and float(pf) >= GO_MIN_PROFIT_FACTOR)
        ),
        "non_negative_blocks_gte_4_of_6": (
            int(b["non_negative_blocks"]) >= GO_MIN_NON_NEGATIVE_BLOCKS
        ),
        "positive_block_concentration_lte_0_50": (
            concentration is not None
            and float(concentration) <= GO_MAX_POSITIVE_BLOCK_CONCENTRATION
        ),
    }
    decision = "GO" if all(checks.values()) else "NO_GO"

    a_overall = model_reports["A_RANGE6"]["overall"]
    return {
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "live_strategy_mutated": False,
        "hypothesis": (
            "Replace only the current six-bar pre-sweep range extreme structure "
            "level with the latest fully confirmed 2L/2R pivot inside the existing "
            "12-bar liquidity window."
        ),
        "window": {
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "lookback_days": STRUCTURE_AB_LOOKBACK_DAYS,
            "block_days": STRUCTURE_AB_BLOCK_DAYS,
            "blocks": block_count,
        },
        "models": model_reports,
        "ab_delta": {
            "primary_sample": (
                int(overall["sample_size"]) - int(a_overall["sample_size"])
            ),
            "average_net_r": (
                None
                if overall["average_net_r"] is None
                or a_overall["average_net_r"] is None
                else round(
                    float(overall["average_net_r"])
                    - float(a_overall["average_net_r"]),
                    6,
                )
            ),
            "profit_factor": (
                None
                if overall.get("profit_factor") is None
                or a_overall.get("profit_factor") is None
                else round(
                    float(overall["profit_factor"])
                    - float(a_overall["profit_factor"]),
                    6,
                )
            ),
        },
        "go_criteria": {
            "fixed_before_run": True,
            "checks": checks,
            "decision": decision,
            "thresholds": {
                "primary_sample": GO_MIN_PRIMARY,
                "min_each_side": GO_MIN_SIDE_PRIMARY,
                "average_net_r_strictly_greater_than": GO_MIN_AVG_NET_R,
                "profit_factor_min": GO_MIN_PROFIT_FACTOR,
                "non_negative_blocks_min": GO_MIN_NON_NEGATIVE_BLOCKS,
                "positive_block_concentration_max": GO_MAX_POSITIVE_BLOCK_CONCENTRATION,
            },
        },
        "next_action": (
            "If GO: review one isolated v0.7.4 shadow/live proposal; do not tune "
            "the pivot parameters. If NO_GO: close the structure-shift hypothesis "
            "and move next to structural target-path research."
        ),
    }


async def ensure_schema(connection: asyncpg.Connection) -> None:
    await connection.execute(SCHEMA_SQL)


async def _latest_completed_v073_backtest(
    connection: asyncpg.Connection,
) -> dict[str, Any] | None:
    try:
        row = await connection.fetchrow(
            """
            SELECT * FROM day_trade_backtest_jobs
            WHERE strategy_version='0.7.3'
              AND status IN ('COMPLETED','PARTIAL')
            ORDER BY id DESC LIMIT 1
            """
        )
    except asyncpg.exceptions.UndefinedTableError:
        return None
    return None if row is None else dict(row)


def job_parameters(source_id: int) -> dict[str, Any]:
    return {
        "source_backtest_job_id": source_id,
        "lookback_days": STRUCTURE_AB_LOOKBACK_DAYS,
        "warmup_days": STRUCTURE_AB_WARMUP_DAYS,
        "block_days": STRUCTURE_AB_BLOCK_DAYS,
        "batch_symbols": STRUCTURE_AB_BATCH_SYMBOLS,
        "model_a": "PRE_SWEEP_RANGE_EXTREME_6_BARS",
        "model_b": "LAST_CONFIRMED_PIVOT_2L2R",
        "pivot_left": PIVOT_LEFT,
        "pivot_right": PIVOT_RIGHT,
        "pivot_search_bars": PIVOT_SEARCH_BARS,
        "volume_ratio": DAY_TRIGGER_VOLUME_RATIO,
        "horizon_hours": DIAGNOSTIC_BASE_HORIZON_HOURS,
        "cost_bps": DIAGNOSTIC_BASE_COST_BPS,
        "short_mode": DIAGNOSTIC_SHORT_MODE,
        "go_thresholds": {
            "primary_sample": GO_MIN_PRIMARY,
            "min_each_side": GO_MIN_SIDE_PRIMARY,
            "avg_net_r": GO_MIN_AVG_NET_R,
            "profit_factor": GO_MIN_PROFIT_FACTOR,
            "non_negative_blocks": GO_MIN_NON_NEGATIVE_BLOCKS,
            "positive_block_concentration": GO_MAX_POSITIVE_BLOCK_CONCENTRATION,
        },
    }


async def create_job_if_needed(
    connection: asyncpg.Connection,
) -> dict[str, Any]:
    existing = await connection.fetchrow(
        """
        SELECT * FROM day_trade_structure_ab_jobs
        WHERE job_name=$1 AND strategy_version=$2
        ORDER BY id DESC LIMIT 1
        """,
        STRUCTURE_AB_JOB_NAME,
        STRATEGY_VERSION,
    )
    if existing:
        return dict(existing)

    source = await _latest_completed_v073_backtest(connection)
    if source is None:
        raise RuntimeError("No completed v0.7.3 backtest exists")
    universe = _json_value(source.get("universe"), [])
    if not universe:
        raise RuntimeError("Completed v0.7.3 backtest universe is empty")
    end_at = source["end_at"]
    start_at = end_at - timedelta(days=STRUCTURE_AB_LOOKBACK_DAYS)
    warmup_start = start_at - timedelta(days=STRUCTURE_AB_WARMUP_DAYS)
    params = job_parameters(int(source["id"]))
    raw_key = (
        f"{STRATEGY_VERSION}|{STRUCTURE_AB_JOB_NAME}|{source['id']}|"
        f"{start_at.isoformat()}|{end_at.isoformat()}|"
        f"{json.dumps(params, sort_keys=True)}"
    )
    job_key = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await connection.fetchrow(
        """
        INSERT INTO day_trade_structure_ab_jobs (
            job_key,job_name,strategy_version,source_backtest_job_id,status,
            start_at,end_at,warmup_start_at,parameters,universe,warnings,total_symbols
        ) VALUES (
            $1,$2,$3,$4,'PENDING',$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11
        )
        RETURNING *
        """,
        job_key,
        STRUCTURE_AB_JOB_NAME,
        STRATEGY_VERSION,
        int(source["id"]),
        start_at,
        end_at,
        warmup_start,
        json.dumps(params),
        json.dumps(universe),
        json.dumps(WARNINGS),
        len(universe),
    )
    job = dict(row)
    await connection.executemany(
        """
        INSERT INTO day_trade_structure_ab_symbols (job_id,symbol,status,metadata)
        VALUES ($1,$2,'PENDING',$3::jsonb)
        ON CONFLICT (job_id,symbol) DO NOTHING
        """,
        [
            (int(job["id"]), str(item["symbol"]), json.dumps(item))
            for item in universe
        ],
    )
    return job


async def reset_stale_symbols(connection: asyncpg.Connection, job_id: int) -> None:
    await connection.execute(
        """
        UPDATE day_trade_structure_ab_symbols
        SET status='PENDING',started_at=NULL,
            last_error=COALESCE(last_error,'') || ' | stale run reset'
        WHERE job_id=$1 AND status='RUNNING'
          AND started_at < NOW() - ($2::int * INTERVAL '1 minute')
        """,
        job_id,
        STRUCTURE_AB_STALE_RUN_MINUTES,
    )


async def claim_symbols(
    connection: asyncpg.Connection,
    job_id: int,
) -> list[dict[str, Any]]:
    async with connection.transaction():
        rows = await connection.fetch(
            """
            SELECT id,symbol,metadata
            FROM day_trade_structure_ab_symbols
            WHERE job_id=$1 AND status='PENDING'
            ORDER BY CASE WHEN symbol='BTCUSDC' THEN 0 ELSE 1 END,id
            FOR UPDATE SKIP LOCKED LIMIT $2
            """,
            job_id,
            STRUCTURE_AB_BATCH_SYMBOLS,
        )
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        await connection.execute(
            """
            UPDATE day_trade_structure_ab_symbols
            SET status='RUNNING',started_at=NOW(),last_error=NULL
            WHERE id=ANY($1::bigint[])
            """,
            ids,
        )
    return [dict(row) for row in rows]


async def update_job_counts(
    connection: asyncpg.Connection,
    job_id: int,
) -> dict[str, Any]:
    row = await connection.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status='COMPLETED') AS completed,
            COUNT(*) FILTER (WHERE status='FAILED') AS failed,
            COUNT(*) FILTER (WHERE status='PENDING') AS pending,
            COUNT(*) FILTER (WHERE status='RUNNING') AS running
        FROM day_trade_structure_ab_symbols WHERE job_id=$1
        """,
        job_id,
    )
    completed = int(row["completed"] or 0)
    failed = int(row["failed"] or 0)
    pending = int(row["pending"] or 0)
    running = int(row["running"] or 0)
    if pending == 0 and running == 0:
        status = "COMPLETED" if failed == 0 else ("PARTIAL" if completed else "FAILED")
        completed_at = datetime.now(timezone.utc)
    else:
        status = "RUNNING"
        completed_at = None
    await connection.execute(
        """
        UPDATE day_trade_structure_ab_jobs
        SET status=$2,completed_symbols=$3,failed_symbols=$4,
            last_run_at=NOW(),started_at=COALESCE(started_at,NOW()),
            completed_at=CASE
                WHEN $5::timestamptz IS NULL THEN completed_at
                ELSE COALESCE(completed_at,$5)
            END,
            updated_at=NOW()
        WHERE id=$1
        """,
        job_id,
        status,
        completed,
        failed,
        completed_at,
    )
    return {
        "status": status,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "running": running,
    }


async def run_structure_ab_batch() -> dict[str, Any]:
    if not STRUCTURE_AB_ENABLED:
        return {"enabled": False, "status": "DISABLED"}
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    timeout = httpx.Timeout(45.0, connect=15.0)
    limits = httpx.Limits(max_connections=STRUCTURE_AB_HTTP_CONCURRENCY)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = HistoricalBybitAPI(client)
        connection = await asyncpg.connect(DATABASE_URL, timeout=30)
        lock_acquired = False
        try:
            lock_row = await connection.fetchrow(
                "SELECT pg_try_advisory_lock(hashtext($1)) AS acquired",
                STRUCTURE_AB_RUN_LOCK_NAME,
            )
            lock_acquired = bool(lock_row["acquired"])
            if not lock_acquired:
                return {
                    "enabled": True,
                    "job_name": STRUCTURE_AB_JOB_NAME,
                    "status": "SKIPPED_ALREADY_RUNNING",
                }
            await ensure_schema(connection)
            job = await create_job_if_needed(connection)
            job_id = int(job["id"])
            if str(job["status"]) in {"COMPLETED", "PARTIAL", "FAILED"}:
                return {
                    "enabled": True,
                    "job_id": job_id,
                    "job_name": STRUCTURE_AB_JOB_NAME,
                    **(await update_job_counts(connection, job_id)),
                }

            await connection.execute(
                """
                UPDATE day_trade_structure_ab_jobs
                SET status='RUNNING',started_at=COALESCE(started_at,NOW()),
                    last_run_at=NOW(),updated_at=NOW()
                WHERE id=$1
                """,
                job_id,
            )
            await reset_stale_symbols(connection, job_id)
            claimed = await claim_symbols(connection, job_id)
            if not claimed:
                return {
                    "enabled": True,
                    "job_id": job_id,
                    "job_name": STRUCTURE_AB_JOB_NAME,
                    **(await update_job_counts(connection, job_id)),
                }

            warmup_start = job["warmup_start_at"]
            end_at = job["end_at"]
            start_at = job["start_at"]
            btc_bars = await api.klines_range(
                "BTCUSDC",
                _ms(warmup_start),
                _ms(end_at),
            )
            batch_results: list[dict[str, Any]] = []
            for row in claimed:
                symbol_id = int(row["id"])
                symbol = str(row["symbol"])
                metadata = _json_value(row.get("metadata"), {})
                try:
                    bars = (
                        btc_bars
                        if symbol == "BTCUSDC"
                        else await api.klines_range(
                            symbol,
                            _ms(warmup_start),
                            _ms(end_at),
                        )
                    )
                    result = replay_symbol(
                        metadata,
                        bars,
                        btc_bars,
                        start_at,
                        end_at,
                    )
                    await connection.execute(
                        """
                        UPDATE day_trade_structure_ab_symbols
                        SET status='COMPLETED',bars_fetched=$2,result=$3::jsonb,
                            completed_at=NOW(),last_error=NULL
                        WHERE id=$1
                        """,
                        symbol_id,
                        len(bars),
                        json.dumps(result),
                    )
                    batch_results.append(
                        {
                            "symbol": symbol,
                            "status": "COMPLETED",
                            "bars": len(bars),
                            "a_primary": int(
                                result["models"]["A_RANGE6"]["counters"][
                                    "primary_strict_trades"
                                ]
                            ),
                            "b_primary": int(
                                result["models"]["B_PIVOT2L2R"]["counters"][
                                    "primary_strict_trades"
                                ]
                            ),
                        }
                    )
                except Exception as exc:
                    await connection.execute(
                        """
                        UPDATE day_trade_structure_ab_symbols
                        SET status='FAILED',completed_at=NOW(),last_error=$2
                        WHERE id=$1
                        """,
                        symbol_id,
                        f"{type(exc).__name__}: {exc}"[:4000],
                    )
                    batch_results.append(
                        {
                            "symbol": symbol,
                            "status": "FAILED",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

            return {
                "enabled": True,
                "job_id": job_id,
                "job_name": STRUCTURE_AB_JOB_NAME,
                "processed": batch_results,
                **(await update_job_counts(connection, job_id)),
            }
        finally:
            if lock_acquired:
                try:
                    await connection.execute(
                        "SELECT pg_advisory_unlock(hashtext($1))",
                        STRUCTURE_AB_RUN_LOCK_NAME,
                    )
                except Exception:
                    pass
            await connection.close()


__all__ = [
    "GO_MAX_POSITIVE_BLOCK_CONCENTRATION",
    "GO_MIN_AVG_NET_R",
    "GO_MIN_NON_NEGATIVE_BLOCKS",
    "GO_MIN_PRIMARY",
    "GO_MIN_PROFIT_FACTOR",
    "GO_MIN_SIDE_PRIMARY",
    "PIVOT_LEFT",
    "PIVOT_RIGHT",
    "PIVOT_SEARCH_BARS",
    "SCHEMA_SQL",
    "STRATEGY_VERSION",
    "STRUCTURE_AB_BLOCK_DAYS",
    "STRUCTURE_AB_JOB_NAME",
    "STRUCTURE_AB_LOOKBACK_DAYS",
    "WARNINGS",
    "aggregate_trades",
    "build_report_from_symbol_results",
    "is_confirmed_pivot",
    "last_confirmed_pivot_before_sweep",
    "pivot_structure_event",
    "replay_symbol",
    "run_structure_ab_batch",
]
