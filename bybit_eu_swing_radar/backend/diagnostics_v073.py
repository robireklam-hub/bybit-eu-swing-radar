"""Trading Radar v0.7.3 gate-level sweep diagnostics.

Research-only replay. It reuses the completed v0.7.3 backtest window/universe,
stores every detected liquidity sweep (including rejected/incomplete sequences),
and evaluates the live gate chain without changing production strategy logic.

The diagnostic funnel is:
    liquidity sweep
    -> reclaim
    -> 5m structure shift
    -> 5m volume confirmation
    -> fully closed non-opposing 15m structure
    -> candidate/context build
    -> liquidity/execution model
    -> expansion/direction/quality/setup
    -> structural target path
    -> net RR
    -> STRICT trade

4H conflict is recorded for A/B research but is context-only in v0.7.3 and is
not a strict eligibility gate.
"""
from __future__ import annotations

import asyncio
import bisect
import hashlib
import json
import math
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx

from backtest import (
    FIVE_MIN_MS,
    HistoricalBybitAPI,
    _dt_from_ms,
    _higher_prefix,
    _ms,
    _prefix_sums,
    _return_pct,
    _rolling_sum,
    _volatility_regime,
    aggregate_bars,
)
from day_worker import (
    DAY_ASSUMED_ROUND_TRIP_COST_BPS,
    DAY_MAX_SPREAD_BPS,
    DAY_MIN_DIRECTION_SCORE,
    DAY_MIN_EXPANSION_SCORE,
    DAY_MIN_QUALITY_SCORE,
    DAY_MIN_RR,
    DAY_MIN_SETUP_SCORE,
    DAY_MIN_TURNOVER_USDC,
    DAY_TRIGGER_VOLUME_RATIO,
    DayAnalysis,
    analyze_day_market,
    calculate_fast_result,
    nearest_structural_barrier,
)
from diagnostics import SCHEMA_SQL as BASE_DIAGNOSTIC_SCHEMA_SQL
from sweep_research import SweepResearchConfig, scan_sweep_setups
from worker import Bar, Instrument, clamp, safe_float

STRATEGY_VERSION = "0.7.3"
DIAGNOSTIC_JOB_NAME = os.getenv(
    "V073_DIAGNOSTIC_JOB_NAME", "v073-90d-sweep-gate-diagnostics"
).strip()
DATABASE_URL = os.getenv("DATABASE_URL", "")
DIAGNOSTIC_ENABLED = os.getenv("V073_DIAGNOSTIC_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
DIAGNOSTIC_BATCH_SYMBOLS = min(
    max(int(os.getenv("V073_DIAGNOSTIC_BATCH_SYMBOLS", "2")), 1), 6
)
DIAGNOSTIC_HTTP_CONCURRENCY = min(
    max(int(os.getenv("V073_DIAGNOSTIC_HTTP_CONCURRENCY", "3")), 1), 6
)
DIAGNOSTIC_STALE_RUN_MINUTES = min(
    max(int(os.getenv("V073_DIAGNOSTIC_STALE_RUN_MINUTES", "30")), 10), 240
)
DIAGNOSTIC_DEVELOPMENT_DAYS = min(
    max(int(os.getenv("V073_DIAGNOSTIC_DEVELOPMENT_DAYS", "60")), 7), 83
)
DIAGNOSTIC_SHORT_MODE = os.getenv(
    "V073_DIAGNOSTIC_SHORT_MODE", "technical_only"
).strip().lower()
DIAGNOSTIC_PRIMARY_NO_OVERLAP = os.getenv(
    "V073_DIAGNOSTIC_PRIMARY_NO_OVERLAP", "true"
).strip().lower() in {"1", "true", "yes", "on"}
DIAGNOSTIC_HORIZON_HOURS = (2, 4, 8)
DIAGNOSTIC_COST_BPS = (0.0, 10.0, 20.0, 30.0)
DIAGNOSTIC_BASE_HORIZON_HOURS = 8
DIAGNOSTIC_BASE_COST_BPS = 20.0
DIAGNOSTIC_RUN_LOCK_NAME = "trading-radar:day-diagnostic:v073-sweep-gates"
DIAGNOSTIC_MAJOR_SYMBOLS = {
    "BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC", "HYPEUSDC"
}

if DIAGNOSTIC_SHORT_MODE not in {"disabled", "technical_only", "current_proxy"}:
    raise RuntimeError(
        "V073_DIAGNOSTIC_SHORT_MODE must be disabled, technical_only or current_proxy"
    )

V073_SCHEMA_SQL = BASE_DIAGNOSTIC_SCHEMA_SQL + r"""
ALTER TABLE day_trade_diagnostic_events
    ADD COLUMN IF NOT EXISTS pass_reclaim BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE day_trade_diagnostic_events
    ADD COLUMN IF NOT EXISTS pass_structure_5m BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE day_trade_diagnostic_events
    ADD COLUMN IF NOT EXISTS pass_structure_15m BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE day_trade_diagnostic_events
    ADD COLUMN IF NOT EXISTS sweep_depth_atr DOUBLE PRECISION;
ALTER TABLE day_trade_diagnostic_events
    ADD COLUMN IF NOT EXISTS bars_from_sweep_to_confirmation INTEGER;
"""

WARNINGS = [
    "v0.7.3 gate diagnostics are research-only and never alter live strategy state.",
    "The source universe/window is the completed v0.7.3 backtest, preserving the same survivorship-bias limitation.",
    "Historical spread is modelled from rolling 24h turnover; it is not bid/ask history.",
    "Historical short borrowability is unavailable; technical_only shorts are research-only.",
    "Coinalyze OI/funding remains excluded from replay scoring and is not a hard gate.",
    "4H conflict is recorded for A/B analysis but is context-only, not a v0.7.3 strict gate.",
    "Development/validation is chronological; validation must remain untouched during threshold selection.",
    "Same-candle stop and TP2 is conservatively treated as stop-first.",
]


@dataclass
class DiagnosticReplayResult:
    events: list[dict[str, Any]]
    bars_fetched: int
    evaluation_bars: int


def modeled_spread_bps(turnover_24h: float) -> float:
    if turnover_24h >= 10_000_000:
        return 3.0
    if turnover_24h >= 2_000_000:
        return 6.0
    if turnover_24h >= 750_000:
        return 10.0
    if turnover_24h >= 400_000:
        return 15.0
    if turnover_24h >= DAY_MIN_TURNOVER_USDC:
        return 22.0
    return 60.0


def _parse_iso_start(value: str | None) -> int | None:
    if not value:
        return None
    return _ms(datetime.fromisoformat(value))


def evaluate_path(
    side: str,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    future_bars: list[Bar],
) -> dict[str, Any] | None:
    if side == "long" and not (stop < entry < tp2):
        return None
    if side == "short" and not (tp2 < entry < stop):
        return None
    risk = abs(entry - stop)
    if risk <= 0 or not future_bars:
        return None

    mfe = 0.0
    mae = 0.0
    exit_reason: str | None = None
    exit_price: float | None = None
    closed_bar: Bar | None = None
    observed = 0
    for bar in future_bars:
        observed += 1
        if side == "long":
            favorable = max(0.0, (bar.high - entry) / risk)
            adverse = max(0.0, (entry - bar.low) / risk)
            stop_hit = bar.low <= stop
            tp1_hit = bar.high >= tp1
            tp2_hit = bar.high >= tp2
            tp3_hit = bar.high >= tp3
        else:
            favorable = max(0.0, (entry - bar.low) / risk)
            adverse = max(0.0, (bar.high - stop + stop - entry) / risk)
            # Expanded form above keeps the branch explicit; equivalent to (bar.high-entry)/risk.
            adverse = max(0.0, (bar.high - entry) / risk)
            stop_hit = bar.high >= stop
            tp1_hit = bar.low <= tp1
            tp2_hit = bar.low <= tp2
            tp3_hit = bar.low <= tp3

        if stop_hit:
            mae = max(mae, 1.0)
            exit_reason = (
                "AMBIGUOUS_STOP_FIRST"
                if tp1_hit or tp2_hit or tp3_hit
                else "STOP"
            )
            exit_price = stop
            closed_bar = bar
            break

        mfe = max(mfe, favorable)
        mae = max(mae, adverse)
        if tp2_hit:
            exit_reason = "TP2"
            exit_price = tp2
            closed_bar = bar
            break

    if exit_reason is None:
        closed_bar = future_bars[-1]
        exit_reason = "TIME_EXIT"
        exit_price = closed_bar.close

    multiplier = 1.0 if side == "long" else -1.0
    gross_r = multiplier * (exit_price - entry) / risk
    return {
        "closed_at": _dt_from_ms(closed_bar.start_ms + FIVE_MIN_MS),
        "bars_observed": observed,
        "mfe_r": round(mfe, 6),
        "mae_r": round(mae, 6),
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "gross_r": round(gross_r, 6),
        "risk_per_unit": risk,
    }


def build_sensitivity(
    side: str,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    future_bars: list[Bar],
) -> dict[str, Any]:
    risk = abs(entry - stop)
    if risk <= 0:
        return {}
    output: dict[str, Any] = {}
    for hours in DIAGNOSTIC_HORIZON_HOURS:
        path = evaluate_path(
            side, entry, stop, tp1, tp2, tp3, future_bars[: hours * 12]
        )
        if path is None:
            continue
        net_by_cost: dict[str, float] = {}
        cost_r_by_cost: dict[str, float] = {}
        for cost_bps in DIAGNOSTIC_COST_BPS:
            cost_r = (entry * cost_bps / 10_000.0) / risk
            key = f"{cost_bps:g}"
            cost_r_by_cost[key] = round(cost_r, 6)
            net_by_cost[key] = round(float(path["gross_r"]) - cost_r, 6)
        output[str(hours)] = {
            "horizon_hours": hours,
            "closed_at": path["closed_at"].isoformat(),
            "bars_observed": path["bars_observed"],
            "exit_reason": path["exit_reason"],
            "gross_r": path["gross_r"],
            "mfe_r": path["mfe_r"],
            "mae_r": path["mae_r"],
            "net_r_by_cost": net_by_cost,
            "cost_r_by_cost": cost_r_by_cost,
        }
    return output


def build_research_candidate(
    analysis: DayAnalysis,
    side: str,
    sweep_event: dict[str, Any],
) -> dict[str, Any] | None:
    if not sweep_event.get("structure_shift_5m"):
        return None
    entry = safe_float(sweep_event.get("candidate_entry"))
    stop = safe_float(sweep_event.get("candidate_invalidation"))
    if entry <= 0 or stop <= 0:
        return None
    if side == "long" and stop >= entry:
        return None
    if side == "short" and stop <= entry:
        return None

    risk = abs(entry - stop)
    if risk <= max(analysis.instrument.tick_size * 3.0, entry * 0.0002):
        return None

    side_direction = (
        analysis.direction_score if side == "long" else -analysis.direction_score
    )
    setup_score = clamp(
        0.35 * analysis.expansion_score
        + 0.35 * max(side_direction, 0.0)
        + 0.30 * analysis.quality_score
    )
    direction_multiplier = 1.0 if side == "long" else -1.0
    assumed_cost = entry * DAY_ASSUMED_ROUND_TRIP_COST_BPS / 10_000.0

    def target_for_net_r(net_r: float) -> float:
        return entry + direction_multiplier * (net_r * risk + assumed_cost)

    tp1 = target_for_net_r(1.0)
    tp2 = target_for_net_r(DAY_MIN_RR)
    tp3 = target_for_net_r(2.5)

    sweep_start_ms = _parse_iso_start(sweep_event.get("sweep_time"))
    if sweep_start_ms is None:
        return None
    barrier_info = nearest_structural_barrier(
        analysis, side, entry, sweep_start_ms
    )
    barrier = None if barrier_info is None else float(barrier_info["price"])
    barrier_before_tp2 = False
    if barrier is not None:
        barrier_before_tp2 = (
            entry < barrier < tp2 if side == "long" else tp2 < barrier < entry
        )
    reward_reference = barrier if barrier_before_tp2 else tp2
    expected_rr = max(
        0.0,
        (abs(reward_reference - entry) - assumed_cost) / max(risk, 1e-12),
    )
    barrier_net_rr = (
        max(
            0.0,
            (abs(barrier - entry) - assumed_cost) / max(risk, 1e-12),
        )
        if barrier is not None
        else None
    )
    target_path_valid = (
        not barrier_before_tp2
        or (barrier_net_rr is not None and barrier_net_rr + 1e-9 >= DAY_MIN_RR)
    )
    conflict_4h = (
        "bearish" in analysis.structure_4h
        if side == "long"
        else "bullish" in analysis.structure_4h
    )
    return {
        "symbol": analysis.instrument.symbol,
        "side": side,
        "setup_type": "LIQUIDITY_SWEEP_RECLAIM",
        "entry": entry,
        "stop": stop,
        "targets": [tp1, tp2, tp3],
        "tradeable": analysis.instrument.tradeable,
        "shortable": analysis.shortable,
        "expansion_score": float(analysis.expansion_score),
        "direction_score": float(analysis.direction_score),
        "side_direction_score": float(side_direction),
        "quality_score": float(analysis.quality_score),
        "setup_score": float(setup_score),
        "expected_rr": float(expected_rr),
        "timeframe_conflict": bool(conflict_4h),
        "trigger": {
            "triggered": bool(sweep_event.get("entry_ready")),
            "price": entry,
            "volume_confirmed": bool(sweep_event.get("volume_confirmed")),
            "structure_confirmed_15m": bool(
                sweep_event.get("structure_confirmed_15m")
            ),
        },
        "metrics": {
            "volume_ratio_5m": sweep_event.get("volume_ratio_5m"),
            "target_path_valid": target_path_valid,
            "nearest_structural_barrier": barrier_info,
            "barrier_before_tp2": barrier_before_tp2,
            "barrier_net_rr": barrier_net_rr,
            "turnover_24h_usdc": analysis.instrument.turnover_24h,
            "spread_bps": analysis.instrument.spread_bps,
        },
        "research_trigger": sweep_event,
    }


def gate_snapshot(
    candidate: dict[str, Any] | None,
    side: str,
    sweep_event: dict[str, Any],
    current_shortable_proxy: bool,
) -> dict[str, Any]:
    pass_reclaim = bool(sweep_event.get("reclaim_confirmed"))
    pass_structure_5m = bool(sweep_event.get("structure_shift_5m"))
    pass_volume = bool(sweep_event.get("volume_confirmed"))
    pass_structure_15m = bool(sweep_event.get("structure_confirmed_15m"))

    if candidate is None:
        ordered = [
            ("RECLAIM", pass_reclaim),
            ("STRUCTURE_SHIFT_5M", pass_structure_5m),
            ("VOLUME_1_3X", pass_volume),
            ("STRUCTURE_15M", pass_structure_15m),
            ("CANDIDATE_BUILD", False),
        ]
        first_failed = next((name for name, passed in ordered if not passed), "CANDIDATE_BUILD")
        return {
            "candidate_built": False,
            "pass_reclaim": pass_reclaim,
            "pass_structure_5m": pass_structure_5m,
            "pass_structure_15m": pass_structure_15m,
            "pass_tradeable": False,
            "pass_side_execution_model": False,
            "pass_no_timeframe_conflict": True,
            "pass_expansion": False,
            "pass_direction": False,
            "pass_quality": False,
            "pass_setup": False,
            "pass_target_path": False,
            "pass_rr": False,
            "pass_volume_confirmation": pass_volume,
            "pass_score_gates": False,
            "pass_strict_eligible": False,
            "pass_strict_trade": False,
            "near_strict": False,
            "first_failed_gate": first_failed,
            "borrowability_status": (
                "NOT_APPLICABLE" if side == "long" else "UNVERIFIED"
            ),
        }

    metrics = candidate.get("metrics") or {}
    tradeable = bool(candidate.get("tradeable"))
    if side == "long":
        execution_model = tradeable
        borrowability_status = "NOT_APPLICABLE"
    elif DIAGNOSTIC_SHORT_MODE == "current_proxy":
        execution_model = tradeable and current_shortable_proxy
        borrowability_status = (
            "CURRENT_PROXY_CONFIRMED"
            if current_shortable_proxy
            else "CURRENT_PROXY_BLOCKED"
        )
    elif DIAGNOSTIC_SHORT_MODE == "technical_only":
        execution_model = tradeable
        borrowability_status = "HISTORICAL_UNVERIFIED_TECHNICAL_ONLY"
    else:
        execution_model = False
        borrowability_status = "DISABLED"

    pass_expansion = safe_float(candidate.get("expansion_score")) >= DAY_MIN_EXPANSION_SCORE
    pass_direction = safe_float(candidate.get("side_direction_score")) >= DAY_MIN_DIRECTION_SCORE
    pass_quality = safe_float(candidate.get("quality_score")) >= DAY_MIN_QUALITY_SCORE
    pass_setup = safe_float(candidate.get("setup_score")) >= DAY_MIN_SETUP_SCORE
    pass_target_path = bool(metrics.get("target_path_valid", False))
    pass_rr = safe_float(candidate.get("expected_rr")) + 1e-9 >= DAY_MIN_RR
    pass_score_gates = pass_expansion and pass_direction and pass_quality and pass_setup
    strict_eligible = (
        tradeable
        and execution_model
        and pass_score_gates
        and pass_target_path
        and pass_rr
    )
    strict_trade = (
        strict_eligible
        and pass_reclaim
        and pass_structure_5m
        and pass_volume
        and pass_structure_15m
    )
    near_strict = (
        tradeable
        and execution_model
        and safe_float(candidate.get("setup_score")) >= 65.0
        and pass_target_path
        and safe_float(candidate.get("expected_rr")) >= 1.2
    )

    ordered = [
        ("RECLAIM", pass_reclaim),
        ("STRUCTURE_SHIFT_5M", pass_structure_5m),
        ("VOLUME_1_3X", pass_volume),
        ("STRUCTURE_15M", pass_structure_15m),
        ("CANDIDATE_BUILD", True),
        ("LIQUIDITY_EXECUTION", tradeable),
        ("SIDE_EXECUTION_MODEL", execution_model),
        ("EXPANSION_55", pass_expansion),
        ("DIRECTION_35", pass_direction),
        ("QUALITY_65", pass_quality),
        ("SETUP_70", pass_setup),
        ("TARGET_PATH", pass_target_path),
        ("NET_RR_1_8", pass_rr),
    ]
    first_failed = "PASSED_STRICT_TRADE"
    for name, passed in ordered:
        if not passed:
            first_failed = name
            break

    return {
        "candidate_built": True,
        "pass_reclaim": pass_reclaim,
        "pass_structure_5m": pass_structure_5m,
        "pass_structure_15m": pass_structure_15m,
        "pass_tradeable": tradeable,
        "pass_side_execution_model": execution_model,
        # Compatibility column only: 4H conflict is context-only in v0.7.3.
        "pass_no_timeframe_conflict": True,
        "pass_expansion": pass_expansion,
        "pass_direction": pass_direction,
        "pass_quality": pass_quality,
        "pass_setup": pass_setup,
        "pass_target_path": pass_target_path,
        "pass_rr": pass_rr,
        "pass_volume_confirmation": pass_volume,
        "pass_score_gates": pass_score_gates,
        "pass_strict_eligible": strict_eligible,
        "pass_strict_trade": strict_trade,
        "near_strict": near_strict,
        "first_failed_gate": first_failed,
        "borrowability_status": borrowability_status,
    }


def replay_diagnostic_symbol(
    job_id: int,
    symbol_meta: dict[str, Any],
    bars_5m: list[Bar],
    btc_bars_5m: list[Bar],
    start_at: datetime,
    end_at: datetime,
    development_end_at: datetime,
) -> DiagnosticReplayResult:
    symbol = str(symbol_meta["symbol"]).upper()
    if len(bars_5m) < 500 or len(btc_bars_5m) < 500:
        return DiagnosticReplayResult([], len(bars_5m), 0)

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
    bar_starts = [bar.start_ms for bar in bars_5m]
    bar_index = {value: index for index, value in enumerate(bar_starts)}
    btc_starts = [bar.start_ms for bar in btc_bars_5m]

    start_ms = _ms(start_at)
    end_ms = _ms(end_at)
    development_end_ms = _ms(development_end_at)
    horizon_bars = DIAGNOSTIC_BASE_HORIZON_HOURS * 12
    current_shortable_proxy = bool(symbol_meta.get("current_shortable_proxy"))
    tick_size = max(safe_float(symbol_meta.get("tick_size"), 0.0), 1e-12)

    config = SweepResearchConfig(volume_confirmation_ratio=DAY_TRIGGER_VOLUME_RATIO)
    raw_events: list[dict[str, Any]] = []
    for side in ("long", "short"):
        if side == "short" and DIAGNOSTIC_SHORT_MODE == "disabled":
            continue
        for event in scan_sweep_setups(
            bars_5m,
            side,
            bars_15m=bars15,
            config=config,
            include_incomplete=True,
        ):
            event = dict(event)
            event["side"] = side
            raw_events.append(event)

    raw_events.sort(
        key=lambda event: (
            _parse_iso_start(
                event.get("structure_shift_time_5m")
                or event.get("reclaim_time")
                or event.get("sweep_time")
            )
            or 0,
            str(event.get("side")),
            int(event.get("sweep_index") or 0),
        )
    )

    events: list[dict[str, Any]] = []
    evaluation_bars: set[int] = set()
    last_primary_exit: dict[str, int] = {"long": 0, "short": 0}

    for sweep_event in raw_events:
        side = str(sweep_event["side"])
        event_start_ms = _parse_iso_start(
            sweep_event.get("structure_shift_time_5m")
            or sweep_event.get("reclaim_time")
            or sweep_event.get("sweep_time")
        )
        if event_start_ms is None:
            continue
        evaluation_time_ms = event_start_ms + FIVE_MIN_MS
        if evaluation_time_ms < start_ms or evaluation_time_ms > end_ms:
            continue

        split = (
            "DEVELOPMENT"
            if evaluation_time_ms < development_end_ms
            else "VALIDATION"
        )
        candidate: dict[str, Any] | None = None
        btc_analysis: DayAnalysis | None = None
        turnover24 = 0.0
        spread_bps = 60.0
        confirm_index: int | None = None

        structure_start_ms = _parse_iso_start(sweep_event.get("structure_shift_time_5m"))
        if structure_start_ms is not None:
            confirm_index = bar_index.get(structure_start_ms)

        if confirm_index is not None:
            evaluation_bars.add(confirm_index)
            bars5_slice = bars_5m[max(0, confirm_index - 219):confirm_index + 1]
            symbol15 = _higher_prefix(bars15, closes15, evaluation_time_ms, 220)
            symbol1h = _higher_prefix(bars1h, closes1h, evaluation_time_ms, 140)
            symbol4h = _higher_prefix(bars4h, closes4h, evaluation_time_ms, 100)
            btc15_slice = _higher_prefix(btc15, btc_closes15, evaluation_time_ms, 220)
            btc1h_slice = _higher_prefix(btc1h, btc_closes1h, evaluation_time_ms, 140)
            btc4h_slice = _higher_prefix(btc4h, btc_closes4h, evaluation_time_ms, 100)

            if (
                len(bars5_slice) >= 100
                and len(symbol15) >= 100
                and len(symbol1h) >= 100
                and len(symbol4h) >= 80
                and len(btc15_slice) >= 100
                and len(btc1h_slice) >= 100
                and len(btc4h_slice) >= 80
            ):
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
                        (
                            current / bars_5m[confirm_index - 288].close - 1.0
                        )
                        * 100.0
                        if confirm_index >= 288
                        and bars_5m[confirm_index - 288].close > 0
                        else 0.0
                    ),
                    tradeable=tradeable,
                    liquidity_reasons=[] if tradeable else ["HISTORICAL_LIQUIDITY_GATE_FAILED"],
                    discovery_source="v073_sweep_gate_diagnostics",
                )
                fast = calculate_fast_result(instrument, bars5_slice, symbol15)
                btc_r1h = _return_pct(btc15_slice, 4)
                btc_r4h = _return_pct(btc1h_slice, 4)
                analysis = analyze_day_market(
                    fast, symbol1h, symbol4h, btc_r1h, btc_r4h
                )
                if side == "long":
                    analysis.shortable = False
                elif DIAGNOSTIC_SHORT_MODE == "technical_only":
                    analysis.shortable = True
                elif DIAGNOSTIC_SHORT_MODE == "current_proxy":
                    analysis.shortable = current_shortable_proxy
                else:
                    analysis.shortable = False
                candidate = build_research_candidate(analysis, side, sweep_event)

                btc_end = bisect.bisect_right(btc_starts, structure_start_ms)
                btc5_slice = btc_bars_5m[max(0, btc_end - 220):btc_end]
                if len(btc5_slice) >= 100:
                    btc_price = btc5_slice[-1].close
                    btc_instrument = Instrument(
                        symbol="BTCUSDC",
                        base="BTC",
                        quote="USDC",
                        margin_trading="both",
                        tick_size=0.01,
                        turnover_24h=10_000_000,
                        volume_24h=1,
                        last_price=btc_price,
                        bid=btc_price * 0.99985,
                        ask=btc_price * 1.00015,
                        spread_bps=3,
                        price_change_24h_pct=0,
                        tradeable=True,
                        liquidity_reasons=[],
                        discovery_source="v073_sweep_gate_diagnostics",
                    )
                    btc_fast = calculate_fast_result(
                        btc_instrument, btc5_slice, btc15_slice
                    )
                    btc_analysis = analyze_day_market(
                        btc_fast,
                        btc1h_slice,
                        btc4h_slice,
                        btc_r1h,
                        btc_r4h,
                    )

        gates = gate_snapshot(
            candidate, side, sweep_event, current_shortable_proxy
        )
        if side == "long":
            execution_assumption = "SPOT_LONG_HISTORICAL_TURNOVER_AND_SPREAD_MODEL"
        elif DIAGNOSTIC_SHORT_MODE == "current_proxy" and current_shortable_proxy:
            execution_assumption = "SHORT_CURRENT_BORROWABILITY_PROXY"
        else:
            execution_assumption = "SHORT_TECHNICAL_BORROW_UNVERIFIED"

        sensitivity: dict[str, Any] = {}
        base_path: dict[str, Any] | None = None
        included_primary = False
        primary_exclusion_reason: str | None = "NO_EVALUABLE_PATH"
        targets = list((candidate or {}).get("targets") or [])
        entry = safe_float((candidate or {}).get("entry"))
        stop = safe_float((candidate or {}).get("stop"))

        if (
            candidate is not None
            and confirm_index is not None
            and len(targets) >= 3
            and confirm_index + horizon_bars < len(bars_5m)
        ):
            future = bars_5m[
                confirm_index + 1:min(
                    len(bars_5m), confirm_index + 1 + horizon_bars
                )
            ]
            sensitivity = build_sensitivity(
                side,
                entry,
                stop,
                safe_float(targets[0]),
                safe_float(targets[1]),
                safe_float(targets[2]),
                future,
            )
            base_data = sensitivity.get(str(DIAGNOSTIC_BASE_HORIZON_HOURS))
            if base_data:
                base_path = {
                    **base_data,
                    "net_r": safe_float(
                        (base_data.get("net_r_by_cost") or {}).get(
                            f"{DIAGNOSTIC_BASE_COST_BPS:g}"
                        )
                    ),
                }
                closed_at = datetime.fromisoformat(base_data["closed_at"])
                if (
                    DIAGNOSTIC_PRIMARY_NO_OVERLAP
                    and evaluation_time_ms < last_primary_exit[side]
                ):
                    included_primary = False
                    primary_exclusion_reason = "OVERLAPPING_SAME_SYMBOL_SIDE_SWEEP"
                else:
                    included_primary = True
                    primary_exclusion_reason = None
                    last_primary_exit[side] = _ms(closed_at)

        metrics = (candidate or {}).get("metrics") or {}
        raw_key = (
            f"{STRATEGY_VERSION}|{job_id}|{symbol}|{side}|"
            f"{sweep_event.get('sweep_index')}|{sweep_event.get('sweep_time')}"
        )
        event_key = "diagnostic-v073:" + hashlib.sha256(
            raw_key.encode()
        ).hexdigest()[:28]
        events.append(
            {
                "job_id": job_id,
                "event_key": event_key,
                "strategy_version": STRATEGY_VERSION,
                "symbol": symbol,
                "side": side,
                "opened_at": _dt_from_ms(evaluation_time_ms),
                "dataset_split": split,
                "universe_group": (
                    "MAJOR_LIQUID" if symbol in DIAGNOSTIC_MAJOR_SYMBOLS else "OTHER"
                ),
                "execution_assumption": execution_assumption,
                "borrowability_status": gates["borrowability_status"],
                "included_primary": included_primary,
                "primary_exclusion_reason": primary_exclusion_reason,
                **{
                    key: value
                    for key, value in gates.items()
                    if key != "borrowability_status"
                },
                "setup_type": (
                    None if candidate is None else str(candidate.get("setup_type"))
                ),
                "entry_price": None if candidate is None else entry,
                "trigger_price": None if candidate is None else entry,
                "stop_price": None if candidate is None else stop,
                "tp1": None if len(targets) < 1 else safe_float(targets[0]),
                "tp2": None if len(targets) < 2 else safe_float(targets[1]),
                "tp3": None if len(targets) < 3 else safe_float(targets[2]),
                "expected_rr": (
                    None
                    if candidate is None
                    else safe_float(candidate.get("expected_rr"))
                ),
                "expansion_score": (
                    None
                    if candidate is None
                    else safe_float(candidate.get("expansion_score"))
                ),
                "direction_score": (
                    None
                    if candidate is None
                    else safe_float(candidate.get("direction_score"))
                ),
                "side_direction_score": (
                    None
                    if candidate is None
                    else safe_float(candidate.get("side_direction_score"))
                ),
                "quality_score": (
                    None
                    if candidate is None
                    else safe_float(candidate.get("quality_score"))
                ),
                "setup_score": (
                    None
                    if candidate is None
                    else safe_float(candidate.get("setup_score"))
                ),
                "volume_ratio_5m": safe_float(
                    sweep_event.get("volume_ratio_5m"), 0.0
                ),
                "turnover_24h_usdc": turnover24,
                "modeled_spread_bps": spread_bps,
                "timeframe_conflict": (
                    False
                    if candidate is None
                    else bool(candidate.get("timeframe_conflict"))
                ),
                "btc_structure_1h": (
                    None if btc_analysis is None else btc_analysis.structure_1h
                ),
                "btc_structure_4h": (
                    None if btc_analysis is None else btc_analysis.structure_4h
                ),
                "btc_volatility_regime": (
                    None if btc_analysis is None else _volatility_regime(btc_analysis)
                ),
                "base_horizon_hours": DIAGNOSTIC_BASE_HORIZON_HOURS,
                "base_cost_bps": DIAGNOSTIC_BASE_COST_BPS,
                "base_exit_reason": (
                    None if base_path is None else str(base_path.get("exit_reason"))
                ),
                "base_gross_r": (
                    None if base_path is None else safe_float(base_path.get("gross_r"))
                ),
                "base_net_r": (
                    None if base_path is None else safe_float(base_path.get("net_r"))
                ),
                "base_mfe_r": (
                    None if base_path is None else safe_float(base_path.get("mfe_r"))
                ),
                "base_mae_r": (
                    None if base_path is None else safe_float(base_path.get("mae_r"))
                ),
                "sensitivity": sensitivity,
                "candidate_payload": {
                    "candidate": candidate or {},
                    "sweep_event": sweep_event,
                },
                "sweep_depth_atr": (
                    None
                    if sweep_event.get("sweep_depth_atr") is None
                    else safe_float(sweep_event.get("sweep_depth_atr"))
                ),
                "bars_from_sweep_to_confirmation": (
                    None
                    if sweep_event.get("bars_from_sweep_to_confirmation") is None
                    else int(sweep_event["bars_from_sweep_to_confirmation"])
                ),
            }
        )

    return DiagnosticReplayResult(
        events=events,
        bars_fetched=len(bars_5m),
        evaluation_bars=len(evaluation_bars),
    )


async def ensure_schema(connection: asyncpg.Connection) -> None:
    await connection.execute(V073_SCHEMA_SQL)


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


def job_parameters(source_backtest_job_id: int) -> dict[str, Any]:
    return {
        "source_backtest_job_id": source_backtest_job_id,
        "source_strategy_version": "0.7.3",
        "development_days": DIAGNOSTIC_DEVELOPMENT_DAYS,
        "batch_symbols": DIAGNOSTIC_BATCH_SYMBOLS,
        "horizon_hours": list(DIAGNOSTIC_HORIZON_HOURS),
        "cost_bps": list(DIAGNOSTIC_COST_BPS),
        "base_horizon_hours": DIAGNOSTIC_BASE_HORIZON_HOURS,
        "base_cost_bps": DIAGNOSTIC_BASE_COST_BPS,
        "strict_setup_min": DAY_MIN_SETUP_SCORE,
        "strict_expansion_min": DAY_MIN_EXPANSION_SCORE,
        "strict_side_direction_min": DAY_MIN_DIRECTION_SCORE,
        "strict_quality_min": DAY_MIN_QUALITY_SCORE,
        "strict_net_rr_min": DAY_MIN_RR,
        "strict_volume_ratio_min": DAY_TRIGGER_VOLUME_RATIO,
        "short_mode": DIAGNOSTIC_SHORT_MODE,
        "primary_no_overlap": DIAGNOSTIC_PRIMARY_NO_OVERLAP,
        "trigger_model": (
            "LIQUIDITY_SWEEP->RECLAIM->5M_STRUCTURE_SHIFT->"
            "VOLUME->15M_NON_OPPOSING"
        ),
        "four_hour_policy": "CONTEXT_ONLY_NOT_HARD_GATE",
    }


async def create_job_if_needed(
    connection: asyncpg.Connection,
) -> dict[str, Any]:
    existing = await connection.fetchrow(
        """
        SELECT * FROM day_trade_diagnostic_jobs
        WHERE job_name=$1 AND strategy_version=$2
        ORDER BY id DESC LIMIT 1
        """,
        DIAGNOSTIC_JOB_NAME,
        STRATEGY_VERSION,
    )
    if existing:
        return dict(existing)

    source = await _latest_completed_v073_backtest(connection)
    if source is None:
        raise RuntimeError("No completed v0.7.3 backtest exists for diagnostics")
    source_id = int(source["id"])
    universe = source.get("universe") or []
    if isinstance(universe, str):
        universe = json.loads(universe)
    universe = list(universe)
    if not universe:
        raise RuntimeError("Completed v0.7.3 backtest has an empty universe")

    start_at = source["start_at"]
    end_at = source["end_at"]
    warmup_start = source["warmup_start_at"]
    actual_days = max(
        1, int((end_at - start_at).total_seconds() // 86_400)
    )
    development_days = min(
        DIAGNOSTIC_DEVELOPMENT_DAYS, max(1, actual_days - 7)
    )
    development_end = start_at + timedelta(days=development_days)
    params = job_parameters(source_id)
    params["actual_lookback_days"] = actual_days
    params["actual_development_days"] = development_days
    raw_key = (
        f"{STRATEGY_VERSION}|{DIAGNOSTIC_JOB_NAME}|{source_id}|"
        f"{start_at.isoformat()}|{end_at.isoformat()}|"
        f"{json.dumps(params, sort_keys=True)}"
    )
    job_key = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await connection.fetchrow(
        """
        INSERT INTO day_trade_diagnostic_jobs (
            job_key,job_name,strategy_version,source_backtest_job_id,status,
            start_at,end_at,warmup_start_at,development_end_at,parameters,
            universe,warnings,total_symbols
        ) VALUES (
            $1,$2,$3,$4,'PENDING',$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12
        )
        RETURNING *
        """,
        job_key,
        DIAGNOSTIC_JOB_NAME,
        STRATEGY_VERSION,
        source_id,
        start_at,
        end_at,
        warmup_start,
        development_end,
        json.dumps(params),
        json.dumps(universe),
        json.dumps(WARNINGS),
        len(universe),
    )
    job = dict(row)
    await connection.executemany(
        """
        INSERT INTO day_trade_diagnostic_symbols (job_id,symbol,status,metadata)
        VALUES ($1,$2,'PENDING',$3::jsonb)
        ON CONFLICT (job_id,symbol) DO NOTHING
        """,
        [
            (job["id"], item["symbol"], json.dumps(item))
            for item in universe
        ],
    )
    return job


async def reset_stale_symbols(
    connection: asyncpg.Connection, job_id: int
) -> None:
    await connection.execute(
        """
        UPDATE day_trade_diagnostic_symbols
        SET status='PENDING',started_at=NULL,
            last_error=COALESCE(last_error,'') || ' | stale run reset'
        WHERE job_id=$1 AND status='RUNNING'
          AND started_at < NOW() - ($2::int * INTERVAL '1 minute')
        """,
        job_id,
        DIAGNOSTIC_STALE_RUN_MINUTES,
    )


async def claim_symbols(
    connection: asyncpg.Connection, job_id: int
) -> list[dict[str, Any]]:
    async with connection.transaction():
        rows = await connection.fetch(
            """
            SELECT id,symbol,metadata
            FROM day_trade_diagnostic_symbols
            WHERE job_id=$1 AND status='PENDING'
            ORDER BY CASE WHEN symbol='BTCUSDC' THEN 0 ELSE 1 END,id
            FOR UPDATE SKIP LOCKED LIMIT $2
            """,
            job_id,
            DIAGNOSTIC_BATCH_SYMBOLS,
        )
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        await connection.execute(
            """
            UPDATE day_trade_diagnostic_symbols
            SET status='RUNNING',started_at=NOW(),last_error=NULL
            WHERE id=ANY($1::bigint[])
            """,
            ids,
        )
    return [dict(row) for row in rows]


async def insert_events(
    connection: asyncpg.Connection,
    events: list[dict[str, Any]],
) -> int:
    inserted = 0
    for item in events:
        row = await connection.fetchrow(
            """
            INSERT INTO day_trade_diagnostic_events (
                job_id,event_key,strategy_version,symbol,side,opened_at,
                dataset_split,universe_group,execution_assumption,borrowability_status,
                included_primary,primary_exclusion_reason,candidate_built,
                pass_tradeable,pass_side_execution_model,pass_no_timeframe_conflict,
                pass_expansion,pass_direction,pass_quality,pass_setup,pass_target_path,
                pass_rr,pass_volume_confirmation,pass_score_gates,pass_strict_eligible,
                pass_strict_trade,near_strict,first_failed_gate,setup_type,
                entry_price,trigger_price,stop_price,tp1,tp2,tp3,expected_rr,
                expansion_score,direction_score,side_direction_score,quality_score,
                setup_score,volume_ratio_5m,turnover_24h_usdc,modeled_spread_bps,
                timeframe_conflict,btc_structure_1h,btc_structure_4h,
                btc_volatility_regime,base_horizon_hours,base_cost_bps,
                base_exit_reason,base_gross_r,base_net_r,base_mfe_r,base_mae_r,
                sensitivity,candidate_payload,pass_reclaim,pass_structure_5m,
                pass_structure_15m,sweep_depth_atr,bars_from_sweep_to_confirmation
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,
                $33,$34,$35,$36,$37,$38,$39,$40,$41,$42,$43,$44,$45,$46,$47,
                $48,$49,$50,$51,$52,$53,$54,$55,$56::jsonb,$57::jsonb,$58,$59,
                $60,$61,$62
            )
            ON CONFLICT (event_key) DO NOTHING
            RETURNING id
            """,
            item["job_id"],
            item["event_key"],
            item["strategy_version"],
            item["symbol"],
            item["side"],
            item["opened_at"],
            item["dataset_split"],
            item["universe_group"],
            item["execution_assumption"],
            item["borrowability_status"],
            item["included_primary"],
            item["primary_exclusion_reason"],
            item["candidate_built"],
            item["pass_tradeable"],
            item["pass_side_execution_model"],
            item["pass_no_timeframe_conflict"],
            item["pass_expansion"],
            item["pass_direction"],
            item["pass_quality"],
            item["pass_setup"],
            item["pass_target_path"],
            item["pass_rr"],
            item["pass_volume_confirmation"],
            item["pass_score_gates"],
            item["pass_strict_eligible"],
            item["pass_strict_trade"],
            item["near_strict"],
            item["first_failed_gate"],
            item["setup_type"],
            item["entry_price"],
            item["trigger_price"],
            item["stop_price"],
            item["tp1"],
            item["tp2"],
            item["tp3"],
            item["expected_rr"],
            item["expansion_score"],
            item["direction_score"],
            item["side_direction_score"],
            item["quality_score"],
            item["setup_score"],
            item["volume_ratio_5m"],
            item["turnover_24h_usdc"],
            item["modeled_spread_bps"],
            item["timeframe_conflict"],
            item["btc_structure_1h"],
            item["btc_structure_4h"],
            item["btc_volatility_regime"],
            item["base_horizon_hours"],
            item["base_cost_bps"],
            item["base_exit_reason"],
            item["base_gross_r"],
            item["base_net_r"],
            item["base_mfe_r"],
            item["base_mae_r"],
            json.dumps(item["sensitivity"], default=str),
            json.dumps(item["candidate_payload"], default=str),
            item["pass_reclaim"],
            item["pass_structure_5m"],
            item["pass_structure_15m"],
            item["sweep_depth_atr"],
            item["bars_from_sweep_to_confirmation"],
        )
        inserted += 1 if row else 0
    return inserted


async def update_job_counts(
    connection: asyncpg.Connection, job_id: int
) -> dict[str, Any]:
    counts = await connection.fetchrow(
        """
        SELECT
          COUNT(*) FILTER (WHERE status='COMPLETED') AS completed,
          COUNT(*) FILTER (WHERE status='FAILED') AS failed,
          COUNT(*) FILTER (WHERE status='PENDING') AS pending,
          COUNT(*) FILTER (WHERE status='RUNNING') AS running
        FROM day_trade_diagnostic_symbols WHERE job_id=$1
        """,
        job_id,
    )
    event_counts = await connection.fetchrow(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE included_primary) AS primary_count,
               COUNT(*) FILTER (WHERE pass_strict_eligible) AS strict_eligible,
               COUNT(*) FILTER (WHERE pass_strict_trade) AS strict_trade
        FROM day_trade_diagnostic_events WHERE job_id=$1
        """,
        job_id,
    )
    completed = int(counts["completed"] or 0)
    failed = int(counts["failed"] or 0)
    pending = int(counts["pending"] or 0)
    running = int(counts["running"] or 0)
    total_events = int(event_counts["total"] or 0)
    primary_events = int(event_counts["primary_count"] or 0)
    strict_eligible = int(event_counts["strict_eligible"] or 0)
    strict_trade = int(event_counts["strict_trade"] or 0)
    if pending == 0 and running == 0:
        status = "COMPLETED" if failed == 0 else "PARTIAL"
        completed_at = datetime.now(timezone.utc)
    else:
        status = "RUNNING"
        completed_at = None
    await connection.execute(
        """
        UPDATE day_trade_diagnostic_jobs
        SET status=$2,completed_symbols=$3,failed_symbols=$4,total_events=$5,
            primary_events=$6,strict_eligible_events=$7,strict_trade_events=$8,
            last_run_at=NOW(),started_at=COALESCE(started_at,NOW()),
            completed_at=CASE
                WHEN $9::timestamptz IS NULL THEN completed_at
                ELSE COALESCE(completed_at,$9)
            END,
            updated_at=NOW()
        WHERE id=$1
        """,
        job_id,
        status,
        completed,
        failed,
        total_events,
        primary_events,
        strict_eligible,
        strict_trade,
        completed_at,
    )
    return {
        "status": status,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "running": running,
        "total_events": total_events,
        "primary_events": primary_events,
        "strict_eligible_events": strict_eligible,
        "strict_trade_events": strict_trade,
    }


async def run_diagnostic_batch() -> dict[str, Any]:
    if not DIAGNOSTIC_ENABLED:
        return {"enabled": False, "status": "DISABLED"}
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    timeout = httpx.Timeout(45.0, connect=15.0)
    limits = httpx.Limits(max_connections=DIAGNOSTIC_HTTP_CONCURRENCY)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = HistoricalBybitAPI(client)
        connection = await asyncpg.connect(DATABASE_URL, timeout=30)
        lock_acquired = False
        try:
            lock_row = await connection.fetchrow(
                "SELECT pg_try_advisory_lock(hashtext($1)) AS acquired",
                DIAGNOSTIC_RUN_LOCK_NAME,
            )
            lock_acquired = bool(lock_row["acquired"])
            if not lock_acquired:
                return {
                    "enabled": True,
                    "job_name": DIAGNOSTIC_JOB_NAME,
                    "status": "SKIPPED_ALREADY_RUNNING",
                }

            await ensure_schema(connection)
            job = await create_job_if_needed(connection)
            job_id = int(job["id"])
            if str(job["status"]) in {"COMPLETED", "PARTIAL", "FAILED"}:
                return {
                    "enabled": True,
                    "job_id": job_id,
                    "job_name": DIAGNOSTIC_JOB_NAME,
                    **(await update_job_counts(connection, job_id)),
                }

            await connection.execute(
                """
                UPDATE day_trade_diagnostic_jobs
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
                    "job_name": DIAGNOSTIC_JOB_NAME,
                    **(await update_job_counts(connection, job_id)),
                }

            warmup_start = job["warmup_start_at"]
            start_at = job["start_at"]
            end_at = job["end_at"]
            development_end = job["development_end_at"]
            btc_bars = await api.klines_range(
                "BTCUSDC", _ms(warmup_start), _ms(end_at)
            )
            batch_results: list[dict[str, Any]] = []

            for row in claimed:
                symbol_id = int(row["id"])
                symbol = str(row["symbol"])
                metadata = row["metadata"]
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                try:
                    bars = (
                        btc_bars
                        if symbol == "BTCUSDC"
                        else await api.klines_range(
                            symbol, _ms(warmup_start), _ms(end_at)
                        )
                    )
                    replay = replay_diagnostic_symbol(
                        job_id,
                        metadata,
                        bars,
                        btc_bars,
                        start_at,
                        end_at,
                        development_end,
                    )
                    async with connection.transaction():
                        inserted = await insert_events(
                            connection, replay.events
                        )
                        stored = await connection.fetchrow(
                            """
                            SELECT COUNT(*) AS total,
                                   COUNT(*) FILTER (
                                       WHERE included_primary
                                   ) AS primary_count,
                                   COUNT(*) FILTER (
                                       WHERE pass_strict_eligible
                                   ) AS strict_eligible,
                                   COUNT(*) FILTER (
                                       WHERE pass_strict_trade
                                   ) AS strict_trade
                            FROM day_trade_diagnostic_events
                            WHERE job_id=$1 AND symbol=$2
                            """,
                            job_id,
                            symbol,
                        )
                        await connection.execute(
                            """
                            UPDATE day_trade_diagnostic_symbols
                            SET status='COMPLETED',bars_fetched=$2,
                                evaluation_bars=$3,event_count=$4,
                                primary_event_count=$5,
                                strict_eligible_count=$6,
                                strict_trade_count=$7,
                                completed_at=NOW(),last_error=NULL
                            WHERE id=$1
                            """,
                            symbol_id,
                            replay.bars_fetched,
                            replay.evaluation_bars,
                            int(stored["total"] or 0),
                            int(stored["primary_count"] or 0),
                            int(stored["strict_eligible"] or 0),
                            int(stored["strict_trade"] or 0),
                        )
                    batch_results.append(
                        {
                            "symbol": symbol,
                            "status": "COMPLETED",
                            "bars": replay.bars_fetched,
                            "evaluation_bars": replay.evaluation_bars,
                            "events_inserted_this_run": inserted,
                            "events_stored": int(stored["total"] or 0),
                            "primary_events": int(stored["primary_count"] or 0),
                            "strict_eligible": int(stored["strict_eligible"] or 0),
                            "strict_trade": int(stored["strict_trade"] or 0),
                        }
                    )
                except Exception as exc:
                    await connection.execute(
                        """
                        UPDATE day_trade_diagnostic_symbols
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
                "job_name": DIAGNOSTIC_JOB_NAME,
                "processed": batch_results,
                **(await update_job_counts(connection, job_id)),
            }
        finally:
            if lock_acquired:
                try:
                    await connection.execute(
                        "SELECT pg_advisory_unlock(hashtext($1))",
                        DIAGNOSTIC_RUN_LOCK_NAME,
                    )
                except Exception:
                    pass
            await connection.close()
