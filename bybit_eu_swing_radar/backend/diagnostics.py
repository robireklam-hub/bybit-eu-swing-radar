"""Strict-gate waterfall and edge diagnostics for Trading Radar v0.7.1.

This module replays the exact completed v0.7.0 time window and universe by
preference, then stores every closed-5m breakout trigger instead of only the
STRICT/SHADOW subset. It is diagnostic research infrastructure, not an
execution engine.

Primary questions answered:
- At which sequential gate does the STRICT sample disappear?
- Is volume confirmation, RR, timeframe alignment or a score component the
  dominant blocker?
- Does any near-strict cohort retain positive expectancy across development
  and untouched validation segments?
- How sensitive are outcomes to 2h/4h/8h holding horizons and 0/10/20/30 bps
  round-trip cost assumptions?
"""
from __future__ import annotations

import asyncio
import bisect
import hashlib
import json
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

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
    build_day_candidate,
    calculate_fast_result,
    normalize_usdc_universe,
)
from worker import Bar, Instrument, safe_float

STRATEGY_VERSION = "0.7.1"


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer environment variable {name}={raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float environment variable {name}={raw!r}") from exc


def env_float_list(name: str, default: list[float]) -> list[float]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    values: list[float] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(float(item))
        except ValueError as exc:
            raise RuntimeError(f"Invalid numeric list {name}={raw!r}") from exc
    if not values:
        raise RuntimeError(f"{name} must contain at least one number")
    return sorted(set(values))


def env_int_list(name: str, default: list[int]) -> list[int]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    values: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as exc:
            raise RuntimeError(f"Invalid integer list {name}={raw!r}") from exc
    if not values:
        raise RuntimeError(f"{name} must contain at least one integer")
    return sorted(set(values))


DATABASE_URL = os.getenv("DATABASE_URL", "")
DIAGNOSTIC_ENABLED = env_bool("DIAGNOSTIC_ENABLED", True)
DIAGNOSTIC_JOB_NAME = os.getenv(
    "DIAGNOSTIC_JOB_NAME", "v071-90d-strict-gate-diagnostics"
).strip()
DIAGNOSTIC_REUSE_LATEST_BACKTEST = env_bool(
    "DIAGNOSTIC_REUSE_LATEST_BACKTEST", True
)
DIAGNOSTIC_LOOKBACK_DAYS = min(max(env_int("DIAGNOSTIC_LOOKBACK_DAYS", 90), 30), 365)
DIAGNOSTIC_WARMUP_DAYS = min(max(env_int("DIAGNOSTIC_WARMUP_DAYS", 14), 14), 45)
DIAGNOSTIC_DEVELOPMENT_DAYS = min(
    max(env_int("DIAGNOSTIC_DEVELOPMENT_DAYS", 60), 7),
    DIAGNOSTIC_LOOKBACK_DAYS - 7,
)
DIAGNOSTIC_SYMBOL_LIMIT = min(max(env_int("DIAGNOSTIC_SYMBOL_LIMIT", 30), 3), 60)
DIAGNOSTIC_BATCH_SYMBOLS = min(max(env_int("DIAGNOSTIC_BATCH_SYMBOLS", 3), 1), 6)
DIAGNOSTIC_HTTP_CONCURRENCY = min(max(env_int("DIAGNOSTIC_HTTP_CONCURRENCY", 3), 1), 6)
DIAGNOSTIC_MIN_TURNOVER_USDC = env_float(
    "DIAGNOSTIC_MIN_TURNOVER_USDC", DAY_MIN_TURNOVER_USDC
)
DIAGNOSTIC_MAX_MODELED_SPREAD_BPS = env_float(
    "DIAGNOSTIC_MAX_MODELED_SPREAD_BPS", DAY_MAX_SPREAD_BPS
)
DIAGNOSTIC_SHORT_MODE = os.getenv(
    "DIAGNOSTIC_SHORT_MODE", "technical_only"
).strip().lower()
DIAGNOSTIC_PRIMARY_NO_OVERLAP = env_bool(
    "DIAGNOSTIC_PRIMARY_NO_OVERLAP", True
)
DIAGNOSTIC_STALE_RUN_MINUTES = min(
    max(env_int("DIAGNOSTIC_STALE_RUN_MINUTES", 30), 10), 240
)
DIAGNOSTIC_HORIZON_HOURS = env_int_list(
    "DIAGNOSTIC_HORIZON_HOURS", [2, 4, 8]
)
DIAGNOSTIC_COST_BPS = env_float_list(
    "DIAGNOSTIC_COST_BPS", [0.0, 10.0, 20.0, 30.0]
)
DIAGNOSTIC_BASE_HORIZON_HOURS = max(DIAGNOSTIC_HORIZON_HOURS)
DIAGNOSTIC_BASE_COST_BPS = (
    20.0 if 20.0 in DIAGNOSTIC_COST_BPS else DIAGNOSTIC_COST_BPS[-1]
)
DIAGNOSTIC_MAJOR_SYMBOLS = {
    item.strip().upper()
    for item in os.getenv(
        "DIAGNOSTIC_MAJOR_SYMBOLS", "BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC,HYPEUSDC"
    ).split(",")
    if item.strip()
}
DIAGNOSTIC_SYMBOLS = [
    item.strip().upper()
    for item in os.getenv("DIAGNOSTIC_SYMBOLS", "").split(",")
    if item.strip()
]

if DIAGNOSTIC_SHORT_MODE not in {"disabled", "technical_only", "current_proxy"}:
    raise RuntimeError(
        "DIAGNOSTIC_SHORT_MODE must be disabled, technical_only or current_proxy"
    )
if any(value <= 0 or value > 24 for value in DIAGNOSTIC_HORIZON_HOURS):
    raise RuntimeError("DIAGNOSTIC_HORIZON_HOURS values must be between 1 and 24")
if any(value < 0 or value > 200 for value in DIAGNOSTIC_COST_BPS):
    raise RuntimeError("DIAGNOSTIC_COST_BPS values must be between 0 and 200")


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS day_trade_diagnostic_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE,
    job_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    source_backtest_job_id BIGINT,
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','PARTIAL','FAILED')),
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    warmup_start_at TIMESTAMPTZ NOT NULL,
    development_end_at TIMESTAMPTZ NOT NULL,
    parameters JSONB NOT NULL,
    universe JSONB NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_symbols INTEGER NOT NULL DEFAULT 0,
    completed_symbols INTEGER NOT NULL DEFAULT 0,
    failed_symbols INTEGER NOT NULL DEFAULT 0,
    total_events INTEGER NOT NULL DEFAULT 0,
    primary_events INTEGER NOT NULL DEFAULT 0,
    strict_eligible_events INTEGER NOT NULL DEFAULT 0,
    strict_trade_events INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_day_diagnostic_jobs_status
    ON day_trade_diagnostic_jobs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS day_trade_diagnostic_symbols (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES day_trade_diagnostic_jobs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    bars_fetched INTEGER NOT NULL DEFAULT 0,
    evaluation_bars INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    primary_event_count INTEGER NOT NULL DEFAULT 0,
    strict_eligible_count INTEGER NOT NULL DEFAULT 0,
    strict_trade_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_error TEXT,
    UNIQUE(job_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_day_diagnostic_symbols_queue
    ON day_trade_diagnostic_symbols (job_id, status, id);

CREATE TABLE IF NOT EXISTS day_trade_diagnostic_events (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES day_trade_diagnostic_jobs(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL UNIQUE,
    strategy_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long','short')),
    opened_at TIMESTAMPTZ NOT NULL,
    dataset_split TEXT NOT NULL CHECK (dataset_split IN ('DEVELOPMENT','VALIDATION')),
    universe_group TEXT NOT NULL,
    execution_assumption TEXT NOT NULL,
    borrowability_status TEXT NOT NULL,
    included_primary BOOLEAN NOT NULL DEFAULT TRUE,
    primary_exclusion_reason TEXT,
    candidate_built BOOLEAN NOT NULL,
    pass_tradeable BOOLEAN NOT NULL,
    pass_side_execution_model BOOLEAN NOT NULL,
    pass_no_timeframe_conflict BOOLEAN NOT NULL,
    pass_expansion BOOLEAN NOT NULL,
    pass_direction BOOLEAN NOT NULL,
    pass_quality BOOLEAN NOT NULL,
    pass_setup BOOLEAN NOT NULL,
    pass_rr BOOLEAN NOT NULL,
    pass_volume_confirmation BOOLEAN NOT NULL,
    pass_score_gates BOOLEAN NOT NULL,
    pass_strict_eligible BOOLEAN NOT NULL,
    pass_strict_trade BOOLEAN NOT NULL,
    near_strict BOOLEAN NOT NULL,
    first_failed_gate TEXT NOT NULL,
    setup_type TEXT,
    entry_price DOUBLE PRECISION,
    trigger_price DOUBLE PRECISION,
    stop_price DOUBLE PRECISION,
    tp1 DOUBLE PRECISION,
    tp2 DOUBLE PRECISION,
    tp3 DOUBLE PRECISION,
    expected_rr DOUBLE PRECISION,
    expansion_score DOUBLE PRECISION,
    direction_score DOUBLE PRECISION,
    side_direction_score DOUBLE PRECISION,
    quality_score DOUBLE PRECISION,
    setup_score DOUBLE PRECISION,
    volume_ratio_5m DOUBLE PRECISION,
    turnover_24h_usdc DOUBLE PRECISION NOT NULL,
    modeled_spread_bps DOUBLE PRECISION NOT NULL,
    timeframe_conflict BOOLEAN NOT NULL,
    btc_structure_1h TEXT,
    btc_structure_4h TEXT,
    btc_volatility_regime TEXT,
    base_horizon_hours INTEGER NOT NULL,
    base_cost_bps DOUBLE PRECISION NOT NULL,
    base_exit_reason TEXT,
    base_gross_r DOUBLE PRECISION,
    base_net_r DOUBLE PRECISION,
    base_mfe_r DOUBLE PRECISION,
    base_mae_r DOUBLE PRECISION,
    sensitivity JSONB NOT NULL DEFAULT '{}'::jsonb,
    candidate_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_day_diagnostic_events_job
    ON day_trade_diagnostic_events (job_id, opened_at);
CREATE INDEX IF NOT EXISTS idx_day_diagnostic_events_gate
    ON day_trade_diagnostic_events (job_id, first_failed_gate, side);
CREATE INDEX IF NOT EXISTS idx_day_diagnostic_events_strict
    ON day_trade_diagnostic_events (job_id, pass_strict_eligible, pass_strict_trade, included_primary);
CREATE INDEX IF NOT EXISTS idx_day_diagnostic_events_split
    ON day_trade_diagnostic_events (job_id, dataset_split, universe_group);
"""


@dataclass
class DiagnosticReplayResult:
    events: list[dict[str, Any]]
    bars_fetched: int
    evaluation_bars: int


def modeled_spread_bps(turnover_24h: float) -> float:
    """Conservative turnover-bucket proxy; not historical bid/ask data."""
    if turnover_24h >= 10_000_000:
        return 3.0
    if turnover_24h >= 2_000_000:
        return 6.0
    if turnover_24h >= 750_000:
        return 10.0
    if turnover_24h >= 400_000:
        return 15.0
    if turnover_24h >= DIAGNOSTIC_MIN_TURNOVER_USDC:
        return 22.0
    return 60.0


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
        horizon = future_bars[: hours * 12]
        path = evaluate_path(side, entry, stop, tp1, tp2, tp3, horizon)
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


def gate_snapshot(
    candidate: dict[str, Any] | None,
    side: str,
    current_shortable_proxy: bool,
) -> dict[str, Any]:
    if candidate is None:
        return {
            "candidate_built": False,
            "pass_tradeable": False,
            "pass_side_execution_model": False,
            "pass_no_timeframe_conflict": False,
            "pass_expansion": False,
            "pass_direction": False,
            "pass_quality": False,
            "pass_setup": False,
            "pass_rr": False,
            "pass_volume_confirmation": False,
            "pass_score_gates": False,
            "pass_strict_eligible": False,
            "pass_strict_trade": False,
            "near_strict": False,
            "first_failed_gate": "CANDIDATE_BUILD",
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
            "CURRENT_PROXY_CONFIRMED" if current_shortable_proxy else "CURRENT_PROXY_BLOCKED"
        )
    elif DIAGNOSTIC_SHORT_MODE == "technical_only":
        execution_model = tradeable
        borrowability_status = "HISTORICAL_UNVERIFIED_TECHNICAL_ONLY"
    else:
        execution_model = False
        borrowability_status = "DISABLED"

    no_conflict = not bool(candidate.get("timeframe_conflict"))
    pass_expansion = safe_float(candidate.get("expansion_score")) >= DAY_MIN_EXPANSION_SCORE
    pass_direction = safe_float(candidate.get("side_direction_score")) >= DAY_MIN_DIRECTION_SCORE
    pass_quality = safe_float(candidate.get("quality_score")) >= DAY_MIN_QUALITY_SCORE
    pass_setup = safe_float(candidate.get("setup_score")) >= DAY_MIN_SETUP_SCORE
    pass_rr = safe_float(candidate.get("expected_rr")) >= DAY_MIN_RR
    pass_volume = safe_float(metrics.get("volume_ratio_5m")) >= DAY_TRIGGER_VOLUME_RATIO
    pass_score_gates = pass_expansion and pass_direction and pass_quality and pass_setup
    strict_eligible = (
        tradeable
        and execution_model
        and no_conflict
        and pass_score_gates
        and pass_rr
    )
    strict_trade = strict_eligible and pass_volume
    near_strict = (
        tradeable
        and execution_model
        and no_conflict
        and safe_float(candidate.get("setup_score")) >= 65.0
        and safe_float(candidate.get("expected_rr")) >= 1.2
    )

    ordered = [
        ("LIQUIDITY_EXECUTION", tradeable),
        ("SIDE_EXECUTION_MODEL", execution_model),
        ("TIMEFRAME_ALIGNMENT", no_conflict),
        ("EXPANSION_55", pass_expansion),
        ("DIRECTION_35", pass_direction),
        ("QUALITY_65", pass_quality),
        ("SETUP_70", pass_setup),
        ("NET_RR_1_8", pass_rr),
        ("VOLUME_1_3X", pass_volume),
    ]
    first_failed = "PASSED_STRICT_TRADE"
    for name, passed in ordered:
        if not passed:
            first_failed = name
            break

    return {
        "candidate_built": True,
        "pass_tradeable": tradeable,
        "pass_side_execution_model": execution_model,
        "pass_no_timeframe_conflict": no_conflict,
        "pass_expansion": pass_expansion,
        "pass_direction": pass_direction,
        "pass_quality": pass_quality,
        "pass_setup": pass_setup,
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
    btc_starts = [bar.start_ms for bar in btc_bars_5m]

    start_ms = _ms(start_at)
    end_ms = _ms(end_at)
    development_end_ms = _ms(development_end_at)
    start_index = bisect.bisect_left(bar_starts, start_ms)
    horizon_bars = DIAGNOSTIC_BASE_HORIZON_HOURS * 12
    events: list[dict[str, Any]] = []
    last_primary_exit: dict[str, int] = {"long": 0, "short": 0}
    evaluation_bars = 0

    current_shortable_proxy = bool(symbol_meta.get("current_shortable_proxy"))
    tick_size = max(safe_float(symbol_meta.get("tick_size"), 0.0), 1e-12)

    for index in range(start_index, len(bars_5m) - 1):
        current_bar = bars_5m[index]
        evaluation_time_ms = current_bar.start_ms + FIVE_MIN_MS
        if evaluation_time_ms > end_ms:
            break
        if index + horizon_bars >= len(bars_5m):
            break
        previous_window = bars_5m[index - 12:index]
        if len(previous_window) < 12:
            continue
        previous_close = bars_5m[index - 1].close
        prior_high = max(bar.high for bar in previous_window)
        prior_low = min(bar.low for bar in previous_window)
        trigger_sides: list[str] = []
        if current_bar.close > prior_high and previous_close <= prior_high:
            trigger_sides.append("long")
        if current_bar.close < prior_low and previous_close >= prior_low:
            trigger_sides.append("short")
        if not trigger_sides:
            continue

        bars5_slice = bars_5m[max(0, index - 219):index + 1]
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
            continue
        evaluation_bars += 1
        turnover24 = _rolling_sum(turnover_prefix, index, 288)
        spread_bps = modeled_spread_bps(turnover24)
        tradeable = (
            turnover24 >= DIAGNOSTIC_MIN_TURNOVER_USDC
            and spread_bps <= DIAGNOSTIC_MAX_MODELED_SPREAD_BPS
        )
        current = current_bar.close
        half_spread = spread_bps / 20_000.0
        instrument = Instrument(
            symbol=symbol,
            base=str(symbol_meta.get("base", symbol.removesuffix("USDC"))),
            quote="USDC",
            margin_trading=str(symbol_meta.get("margin_trading", "none")),
            tick_size=tick_size,
            turnover_24h=turnover24,
            volume_24h=_rolling_sum(volume_prefix, index, 288),
            last_price=current,
            bid=current * (1.0 - half_spread),
            ask=current * (1.0 + half_spread),
            spread_bps=spread_bps,
            price_change_24h_pct=(
                (current / bars_5m[index - 288].close - 1.0) * 100.0
                if index >= 288 and bars_5m[index - 288].close > 0
                else 0.0
            ),
            tradeable=tradeable,
            liquidity_reasons=(
                []
                if tradeable
                else [
                    "Historical rolling turnover/modelled spread gate failed: "
                    f"turnover={turnover24:.2f}, spread_model={spread_bps:.2f}bps"
                ]
            ),
            discovery_source="historical_gate_diagnostics",
        )
        fast = calculate_fast_result(instrument, bars5_slice, symbol15)
        btc_r1h = _return_pct(btc15_slice, 4)
        btc_r4h = _return_pct(btc1h_slice, 4)
        analysis = analyze_day_market(fast, symbol1h, symbol4h, btc_r1h, btc_r4h)
        analysis.shortable = DIAGNOSTIC_SHORT_MODE != "disabled"

        btc_end = bisect.bisect_right(btc_starts, current_bar.start_ms)
        btc5_slice = btc_bars_5m[max(0, btc_end - 220):btc_end]
        if len(btc5_slice) < 100:
            continue
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
            discovery_source="historical_gate_diagnostics",
        )
        btc_fast = calculate_fast_result(btc_instrument, btc5_slice, btc15_slice)
        btc_analysis = analyze_day_market(
            btc_fast, btc1h_slice, btc4h_slice, btc_r1h, btc_r4h
        )

        for side in trigger_sides:
            if side == "short" and DIAGNOSTIC_SHORT_MODE == "disabled":
                continue
            candidate = build_day_candidate(
                analysis, side, _dt_from_ms(evaluation_time_ms)
            )
            gates = gate_snapshot(candidate, side, current_shortable_proxy)
            opened_at = _dt_from_ms(evaluation_time_ms)
            split = (
                "DEVELOPMENT"
                if evaluation_time_ms < development_end_ms
                else "VALIDATION"
            )
            if side == "long":
                execution_assumption = "SPOT_LONG_HISTORICAL_TURNOVER_AND_SPREAD_MODEL"
            elif DIAGNOSTIC_SHORT_MODE == "current_proxy" and current_shortable_proxy:
                execution_assumption = "SHORT_CURRENT_BORROWABILITY_PROXY"
            else:
                execution_assumption = "SHORT_TECHNICAL_BORROW_UNVERIFIED"

            sensitivity: dict[str, Any] = {}
            base_path: dict[str, Any] | None = None
            included_primary = True
            primary_exclusion_reason = None
            targets: list[Any] = []
            if candidate is not None:
                targets = list(candidate.get("targets") or [])
                if len(targets) >= 3:
                    future = bars_5m[
                        index + 1:min(len(bars_5m), index + 1 + horizon_bars)
                    ]
                    sensitivity = build_sensitivity(
                        side,
                        current_bar.close,
                        safe_float(candidate.get("stop")),
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
                            primary_exclusion_reason = (
                                "OVERLAPPING_SAME_SYMBOL_SIDE_TRIGGER"
                            )
                        else:
                            last_primary_exit[side] = _ms(closed_at)

            raw_key = (
                f"{STRATEGY_VERSION}|{job_id}|{symbol}|{side}|{current_bar.start_ms}"
            )
            event_key = "diagnostic:" + hashlib.sha256(raw_key.encode()).hexdigest()[:28]
            metrics = (candidate or {}).get("metrics") or {}
            events.append({
                "job_id": job_id,
                "event_key": event_key,
                "strategy_version": STRATEGY_VERSION,
                "symbol": symbol,
                "side": side,
                "opened_at": opened_at,
                "dataset_split": split,
                "universe_group": (
                    "MAJOR_LIQUID" if symbol in DIAGNOSTIC_MAJOR_SYMBOLS else "OTHER"
                ),
                "execution_assumption": execution_assumption,
                "borrowability_status": gates["borrowability_status"],
                "included_primary": included_primary,
                "primary_exclusion_reason": primary_exclusion_reason,
                **{key: value for key, value in gates.items() if key != "borrowability_status"},
                "setup_type": None if candidate is None else str(candidate.get("setup_type")),
                "entry_price": current_bar.close if candidate is not None else None,
                "trigger_price": (
                    None
                    if candidate is None
                    else safe_float((candidate.get("trigger") or {}).get("price"), current_bar.close)
                ),
                "stop_price": None if candidate is None else safe_float(candidate.get("stop")),
                "tp1": None if len(targets) < 1 else safe_float(targets[0]),
                "tp2": None if len(targets) < 2 else safe_float(targets[1]),
                "tp3": None if len(targets) < 3 else safe_float(targets[2]),
                "expected_rr": None if candidate is None else safe_float(candidate.get("expected_rr")),
                "expansion_score": None if candidate is None else safe_float(candidate.get("expansion_score")),
                "direction_score": None if candidate is None else safe_float(candidate.get("direction_score")),
                "side_direction_score": None if candidate is None else safe_float(candidate.get("side_direction_score")),
                "quality_score": None if candidate is None else safe_float(candidate.get("quality_score")),
                "setup_score": None if candidate is None else safe_float(candidate.get("setup_score")),
                "volume_ratio_5m": None if candidate is None else safe_float(metrics.get("volume_ratio_5m")),
                "turnover_24h_usdc": turnover24,
                "modeled_spread_bps": spread_bps,
                "timeframe_conflict": False if candidate is None else bool(candidate.get("timeframe_conflict")),
                "btc_structure_1h": btc_analysis.structure_1h,
                "btc_structure_4h": btc_analysis.structure_4h,
                "btc_volatility_regime": _volatility_regime(btc_analysis),
                "base_horizon_hours": DIAGNOSTIC_BASE_HORIZON_HOURS,
                "base_cost_bps": DIAGNOSTIC_BASE_COST_BPS,
                "base_exit_reason": None if base_path is None else str(base_path.get("exit_reason")),
                "base_gross_r": None if base_path is None else safe_float(base_path.get("gross_r")),
                "base_net_r": None if base_path is None else safe_float(base_path.get("net_r")),
                "base_mfe_r": None if base_path is None else safe_float(base_path.get("mfe_r")),
                "base_mae_r": None if base_path is None else safe_float(base_path.get("mae_r")),
                "sensitivity": sensitivity,
                "candidate_payload": candidate or {},
            })

    return DiagnosticReplayResult(events, len(bars_5m), evaluation_bars)


async def ensure_schema(connection: asyncpg.Connection) -> None:
    await connection.execute(SCHEMA_SQL)


def job_parameters(source_backtest_job_id: int | None) -> dict[str, Any]:
    return {
        "source_backtest_job_id": source_backtest_job_id,
        "reuse_latest_backtest": DIAGNOSTIC_REUSE_LATEST_BACKTEST,
        "lookback_days": DIAGNOSTIC_LOOKBACK_DAYS,
        "warmup_days": DIAGNOSTIC_WARMUP_DAYS,
        "development_days": DIAGNOSTIC_DEVELOPMENT_DAYS,
        "symbol_limit": DIAGNOSTIC_SYMBOL_LIMIT,
        "batch_symbols": DIAGNOSTIC_BATCH_SYMBOLS,
        "horizon_hours": DIAGNOSTIC_HORIZON_HOURS,
        "cost_bps": DIAGNOSTIC_COST_BPS,
        "base_horizon_hours": DIAGNOSTIC_BASE_HORIZON_HOURS,
        "base_cost_bps": DIAGNOSTIC_BASE_COST_BPS,
        "minimum_turnover_usdc": DIAGNOSTIC_MIN_TURNOVER_USDC,
        "max_modeled_spread_bps": DIAGNOSTIC_MAX_MODELED_SPREAD_BPS,
        "strict_setup_min": DAY_MIN_SETUP_SCORE,
        "strict_expansion_min": DAY_MIN_EXPANSION_SCORE,
        "strict_side_direction_min": DAY_MIN_DIRECTION_SCORE,
        "strict_quality_min": DAY_MIN_QUALITY_SCORE,
        "strict_net_rr_min": DAY_MIN_RR,
        "strict_volume_ratio_min": DAY_TRIGGER_VOLUME_RATIO,
        "short_mode": DIAGNOSTIC_SHORT_MODE,
        "primary_no_overlap": DIAGNOSTIC_PRIMARY_NO_OVERLAP,
        "major_symbols": sorted(DIAGNOSTIC_MAJOR_SYMBOLS),
        "higher_timeframes": "AGGREGATED_FROM_5M_CLOSED_BARS",
    }


WARNINGS = [
    "Gate diagnostics are research-only and must not be presented as a trade signal.",
    "Historical spread is modelled from rolling 24h turnover; it is not bid/ask history.",
    "Historical short borrowability is unavailable; technical shorts are not execution evidence.",
    "Coinalyze OI/funding is excluded from v0.7.1 diagnostics.",
    "The default development/validation split is chronological 60/30 days; validation must remain untouched during rule selection.",
    "Multiple cost/horizon combinations are sensitivity analysis, not permission to cherry-pick the best result.",
    "Same-candle stop and TP2 is treated as stop-first.",
]


async def _latest_completed_backtest(
    connection: asyncpg.Connection,
) -> dict[str, Any] | None:
    try:
        row = await connection.fetchrow(
            """
            SELECT * FROM day_trade_backtest_jobs
            WHERE status IN ('COMPLETED','PARTIAL')
            ORDER BY id DESC LIMIT 1
            """
        )
    except asyncpg.exceptions.UndefinedTableError:
        return None
    return None if row is None else dict(row)


async def _fresh_universe(api: HistoricalBybitAPI) -> tuple[list[dict[str, Any]], datetime, datetime, datetime, int | None]:
    instruments_raw, tickers = await asyncio.gather(api.instruments(), api.tickers())
    margin_data: dict[str, dict[str, Any]] = {}
    if DIAGNOSTIC_SHORT_MODE == "current_proxy":
        try:
            margin_data = await api.vip_margin_data()
        except Exception:
            margin_data = {}
    instruments, _, _ = normalize_usdc_universe(instruments_raw, tickers)
    by_symbol = {item.symbol: item for item in instruments}
    if DIAGNOSTIC_SYMBOLS:
        selected = [by_symbol[s] for s in DIAGNOSTIC_SYMBOLS if s in by_symbol]
    else:
        selected = sorted(
            instruments, key=lambda item: item.turnover_24h, reverse=True
        )[:DIAGNOSTIC_SYMBOL_LIMIT]
    btc = by_symbol.get("BTCUSDC")
    if btc and all(item.symbol != "BTCUSDC" for item in selected):
        selected = ([btc] + selected)[:DIAGNOSTIC_SYMBOL_LIMIT]
    if not selected:
        raise RuntimeError("No eligible current Bybit EU USDC symbols for diagnostics")
    raw_map = {str(row.get("symbol", "")).upper(): row for row in instruments_raw}
    universe: list[dict[str, Any]] = []
    for item in selected:
        raw = raw_map.get(item.symbol, {})
        borrow = margin_data.get(item.base, {})
        current_shortable = (
            str(raw.get("marginTrading", "none")).lower() != "none"
            and str(borrow.get("borrowable", "")).lower() in {"1", "true", "yes"}
            and safe_float(borrow.get("maxBorrowingAmount")) > 0
        )
        universe.append({
            "symbol": item.symbol,
            "base": item.base,
            "margin_trading": item.margin_trading,
            "tick_size": item.tick_size,
            "selection_turnover_24h": item.turnover_24h,
            "current_shortable_proxy": current_shortable,
        })
    now_ms = int(time.time() * 1000)
    closed_end_ms = (now_ms // FIVE_MIN_MS) * FIVE_MIN_MS
    end_at = _dt_from_ms(closed_end_ms)
    start_at = end_at - timedelta(days=DIAGNOSTIC_LOOKBACK_DAYS)
    warmup_start = start_at - timedelta(days=DIAGNOSTIC_WARMUP_DAYS)
    return universe, start_at, end_at, warmup_start, None


async def create_job_if_needed(
    connection: asyncpg.Connection,
    api: HistoricalBybitAPI,
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

    source_backtest_job_id: int | None = None
    source = (
        await _latest_completed_backtest(connection)
        if DIAGNOSTIC_REUSE_LATEST_BACKTEST
        else None
    )
    if source is not None:
        source_backtest_job_id = int(source["id"])
        universe = source.get("universe") or []
        if isinstance(universe, str):
            universe = json.loads(universe)
        start_at = source["start_at"]
        end_at = source["end_at"]
        warmup_start = source["warmup_start_at"]
        universe = list(universe)
    else:
        universe, start_at, end_at, warmup_start, source_backtest_job_id = (
            await _fresh_universe(api)
        )
    if not universe:
        raise RuntimeError("Diagnostic universe is empty")

    actual_days = max(1, int((end_at - start_at).total_seconds() // 86_400))
    development_days = min(DIAGNOSTIC_DEVELOPMENT_DAYS, max(1, actual_days - 7))
    development_end = start_at + timedelta(days=development_days)
    params = job_parameters(source_backtest_job_id)
    params["actual_lookback_days"] = actual_days
    params["actual_development_days"] = development_days
    raw_key = (
        f"{STRATEGY_VERSION}|{DIAGNOSTIC_JOB_NAME}|{start_at.isoformat()}|"
        f"{end_at.isoformat()}|{json.dumps(params, sort_keys=True)}"
    )
    job_key = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await connection.fetchrow(
        """
        INSERT INTO day_trade_diagnostic_jobs (
            job_key,job_name,strategy_version,source_backtest_job_id,status,
            start_at,end_at,warmup_start_at,development_end_at,parameters,
            universe,warnings,total_symbols
        ) VALUES ($1,$2,$3,$4,'PENDING',$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12)
        RETURNING *
        """,
        job_key,
        DIAGNOSTIC_JOB_NAME,
        STRATEGY_VERSION,
        source_backtest_job_id,
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
        [(job["id"], item["symbol"], json.dumps(item)) for item in universe],
    )
    return job


async def reset_stale_symbols(connection: asyncpg.Connection, job_id: int) -> None:
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
    connection: asyncpg.Connection,
    job_id: int,
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
                pass_expansion,pass_direction,pass_quality,pass_setup,pass_rr,
                pass_volume_confirmation,pass_score_gates,pass_strict_eligible,
                pass_strict_trade,near_strict,first_failed_gate,setup_type,
                entry_price,trigger_price,stop_price,tp1,tp2,tp3,expected_rr,
                expansion_score,direction_score,side_direction_score,quality_score,
                setup_score,volume_ratio_5m,turnover_24h_usdc,modeled_spread_bps,
                timeframe_conflict,btc_structure_1h,btc_structure_4h,
                btc_volatility_regime,base_horizon_hours,base_cost_bps,
                base_exit_reason,base_gross_r,base_net_r,base_mfe_r,base_mae_r,
                sensitivity,candidate_payload
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,
                $34,$35,$36,$37,$38,$39,$40,$41,$42,$43,$44,$45,$46,$47,$48,$49,
                $50,$51,$52,$53,$54,$55::jsonb,$56::jsonb
            )
            ON CONFLICT (event_key) DO NOTHING RETURNING id
            """,
            item["job_id"],item["event_key"],item["strategy_version"],item["symbol"],
            item["side"],item["opened_at"],item["dataset_split"],item["universe_group"],
            item["execution_assumption"],item["borrowability_status"],
            item["included_primary"],item["primary_exclusion_reason"],
            item["candidate_built"],item["pass_tradeable"],
            item["pass_side_execution_model"],item["pass_no_timeframe_conflict"],
            item["pass_expansion"],item["pass_direction"],item["pass_quality"],
            item["pass_setup"],item["pass_rr"],item["pass_volume_confirmation"],
            item["pass_score_gates"],item["pass_strict_eligible"],
            item["pass_strict_trade"],item["near_strict"],item["first_failed_gate"],
            item["setup_type"],item["entry_price"],item["trigger_price"],
            item["stop_price"],item["tp1"],item["tp2"],item["tp3"],
            item["expected_rr"],item["expansion_score"],item["direction_score"],
            item["side_direction_score"],item["quality_score"],item["setup_score"],
            item["volume_ratio_5m"],item["turnover_24h_usdc"],
            item["modeled_spread_bps"],item["timeframe_conflict"],
            item["btc_structure_1h"],item["btc_structure_4h"],
            item["btc_volatility_regime"],item["base_horizon_hours"],
            item["base_cost_bps"],item["base_exit_reason"],item["base_gross_r"],
            item["base_net_r"],item["base_mfe_r"],item["base_mae_r"],
            json.dumps(item["sensitivity"], default=str),
            json.dumps(item["candidate_payload"], default=str),
        )
        inserted += 1 if row else 0
    return inserted


async def update_job_counts(connection: asyncpg.Connection, job_id: int) -> dict[str, Any]:
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
            completed_at=CASE WHEN $9::timestamptz IS NULL THEN completed_at ELSE COALESCE(completed_at,$9) END,
            updated_at=NOW()
        WHERE id=$1
        """,
        job_id,status,completed,failed,total_events,primary_events,
        strict_eligible,strict_trade,completed_at,
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
        try:
            await ensure_schema(connection)
            job = await create_job_if_needed(connection, api)
            job_id = int(job["id"])
            if str(job["status"]) in {"COMPLETED", "PARTIAL", "FAILED"}:
                counts = await update_job_counts(connection, job_id)
                return {
                    "enabled": True,
                    "job_id": job_id,
                    "job_name": DIAGNOSTIC_JOB_NAME,
                    **counts,
                }
            await connection.execute(
                """
                UPDATE day_trade_diagnostic_jobs
                SET status='RUNNING',started_at=COALESCE(started_at,NOW()),
                    last_run_at=NOW(),updated_at=NOW() WHERE id=$1
                """,
                job_id,
            )
            await reset_stale_symbols(connection, job_id)
            claimed = await claim_symbols(connection, job_id)
            if not claimed:
                counts = await update_job_counts(connection, job_id)
                return {
                    "enabled": True,
                    "job_id": job_id,
                    "job_name": DIAGNOSTIC_JOB_NAME,
                    **counts,
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
                        job_id, metadata, bars, btc_bars,
                        start_at, end_at, development_end,
                    )
                    async with connection.transaction():
                        inserted = await insert_events(connection, replay.events)
                        stored = await connection.fetchrow(
                            """
                            SELECT COUNT(*) AS total,
                                   COUNT(*) FILTER (WHERE included_primary) AS primary_count,
                                   COUNT(*) FILTER (WHERE pass_strict_eligible) AS strict_eligible,
                                   COUNT(*) FILTER (WHERE pass_strict_trade) AS strict_trade
                            FROM day_trade_diagnostic_events
                            WHERE job_id=$1 AND symbol=$2
                            """,
                            job_id, symbol,
                        )
                        await connection.execute(
                            """
                            UPDATE day_trade_diagnostic_symbols
                            SET status='COMPLETED',bars_fetched=$2,evaluation_bars=$3,
                                event_count=$4,primary_event_count=$5,
                                strict_eligible_count=$6,strict_trade_count=$7,
                                completed_at=NOW(),last_error=NULL
                            WHERE id=$1
                            """,
                            symbol_id,replay.bars_fetched,replay.evaluation_bars,
                            int(stored["total"] or 0),int(stored["primary_count"] or 0),
                            int(stored["strict_eligible"] or 0),int(stored["strict_trade"] or 0),
                        )
                    batch_results.append({
                        "symbol": symbol,
                        "status": "COMPLETED",
                        "bars": replay.bars_fetched,
                        "evaluation_bars": replay.evaluation_bars,
                        "events_inserted_this_run": inserted,
                        "events_stored": int(stored["total"] or 0),
                        "primary_events": int(stored["primary_count"] or 0),
                        "strict_eligible": int(stored["strict_eligible"] or 0),
                        "strict_trade": int(stored["strict_trade"] or 0),
                    })
                except Exception as exc:
                    await connection.execute(
                        """
                        UPDATE day_trade_diagnostic_symbols
                        SET status='FAILED',completed_at=NOW(),last_error=$2
                        WHERE id=$1
                        """,
                        symbol_id, f"{type(exc).__name__}: {exc}"[:4000],
                    )
                    batch_results.append({
                        "symbol": symbol,
                        "status": "FAILED",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
            counts = await update_job_counts(connection, job_id)
            return {
                "enabled": True,
                "job_id": job_id,
                "job_name": DIAGNOSTIC_JOB_NAME,
                "processed": batch_results,
                **counts,
            }
        finally:
            await connection.close()
