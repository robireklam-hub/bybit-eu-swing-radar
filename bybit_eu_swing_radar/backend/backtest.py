"""Incremental historical replay engine for Trading Radar v0.7.5.

The replay is research infrastructure, not an execution guarantee.
It reuses the live day-trade scoring functions while enforcing closed-bar
look-ahead protection. Historical 5m spot klines are fetched from Bybit EU and
aggregated into 15m/1h/4h bars locally.

Known limitations are stored with every job:
- current active-symbol selection creates survivorship bias;
- historical bid/ask spread and borrowability are unavailable from the kline
  endpoint, so spread is modelled and short borrowability is not verified;
- Coinalyze derivatives context is not part of v0.7.5 replay scoring;
- the selected liquid universe is evaluated directly rather than recreating the
  live full-universe top-30 promotion at every 5m timestamp.
"""
from __future__ import annotations

import asyncio
import bisect
import hashlib
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import asyncpg
import httpx

from day_worker import (
    DAY_ASSUMED_ROUND_TRIP_COST_BPS,
    DAY_BREAKOUT_ACTIVE_BARS,
    DAY_MAX_SPREAD_BPS,
    DAY_MIN_RR,
    DAY_MIN_TURNOVER_USDC,
    DAY_TRIGGER_VOLUME_RATIO,
    DayAnalysis,
    analyze_day_market,
    build_day_candidate,
    calculate_fast_result,
    normalize_usdc_universe,
    recent_closed_5m_range_breakout,
)
from sweep_research import SweepResearchConfig, latest_bar_sweep_setup
from worker import Bar, BybitAPI, Instrument, safe_float

STRATEGY_VERSION = "0.7.5"
FIVE_MIN_MS = 5 * 60 * 1000


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


DATABASE_URL = os.getenv("DATABASE_URL", "")
BACKTEST_ENABLED = env_bool("BACKTEST_ENABLED", True)
BACKTEST_JOB_NAME = os.getenv("BACKTEST_JOB_NAME", "v075-90d-netrr-structural-barrier").strip()
BACKTEST_LOOKBACK_DAYS = min(max(env_int("BACKTEST_LOOKBACK_DAYS", 90), 7), 365)
BACKTEST_WARMUP_DAYS = min(max(env_int("BACKTEST_WARMUP_DAYS", 14), 14), 45)
BACKTEST_SYMBOL_LIMIT = min(max(env_int("BACKTEST_SYMBOL_LIMIT", 30), 3), 60)
BACKTEST_BATCH_SYMBOLS = min(max(env_int("BACKTEST_BATCH_SYMBOLS", 2), 1), 6)
BACKTEST_HTTP_CONCURRENCY = min(max(env_int("BACKTEST_HTTP_CONCURRENCY", 3), 1), 6)
BACKTEST_PAGE_LIMIT = min(max(env_int("BACKTEST_PAGE_LIMIT", 1000), 200), 1000)
BACKTEST_REQUEST_PAUSE_SECONDS = max(env_float("BACKTEST_REQUEST_PAUSE_SECONDS", 0.05), 0.0)
BACKTEST_HORIZON_HOURS = min(max(env_int("BACKTEST_HORIZON_HOURS", 8), 1), 24)
BACKTEST_COST_BPS = env_float(
    "BACKTEST_COST_BPS", DAY_ASSUMED_ROUND_TRIP_COST_BPS
)
BACKTEST_MIN_TURNOVER_USDC = env_float(
    "BACKTEST_MIN_TURNOVER_USDC", DAY_MIN_TURNOVER_USDC
)
BACKTEST_MAX_MODELED_SPREAD_BPS = env_float(
    "BACKTEST_MAX_MODELED_SPREAD_BPS", DAY_MAX_SPREAD_BPS
)
BACKTEST_SHADOW_MIN_SETUP = env_float("BACKTEST_SHADOW_MIN_SETUP", 65.0)
BACKTEST_SHADOW_MIN_RR = env_float("BACKTEST_SHADOW_MIN_RR", 1.2)
BACKTEST_SHORT_MODE = os.getenv("BACKTEST_SHORT_MODE", "technical_only").strip().lower()
BACKTEST_PRIMARY_NO_OVERLAP = env_bool("BACKTEST_PRIMARY_NO_OVERLAP", True)
BACKTEST_STALE_RUN_MINUTES = min(max(env_int("BACKTEST_STALE_RUN_MINUTES", 30), 10), 240)
BACKTEST_SYMBOLS = [
    item.strip().upper()
    for item in os.getenv("BACKTEST_SYMBOLS", "").split(",")
    if item.strip()
]

if BACKTEST_SHORT_MODE not in {"disabled", "technical_only", "current_proxy"}:
    raise RuntimeError(
        "BACKTEST_SHORT_MODE must be disabled, technical_only or current_proxy"
    )


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS day_trade_backtest_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE,
    job_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','PARTIAL','FAILED')),
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    warmup_start_at TIMESTAMPTZ NOT NULL,
    parameters JSONB NOT NULL,
    universe JSONB NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_symbols INTEGER NOT NULL DEFAULT 0,
    completed_symbols INTEGER NOT NULL DEFAULT 0,
    failed_symbols INTEGER NOT NULL DEFAULT 0,
    total_signals INTEGER NOT NULL DEFAULT 0,
    primary_signals INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_day_backtest_jobs_status
    ON day_trade_backtest_jobs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS day_trade_backtest_symbols (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES day_trade_backtest_jobs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    bars_fetched INTEGER NOT NULL DEFAULT 0,
    evaluation_bars INTEGER NOT NULL DEFAULT 0,
    signal_count INTEGER NOT NULL DEFAULT 0,
    primary_signal_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_error TEXT,
    UNIQUE(job_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_day_backtest_symbols_queue
    ON day_trade_backtest_symbols (job_id, status, id);

CREATE TABLE IF NOT EXISTS day_trade_backtest_signals (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES day_trade_backtest_jobs(id) ON DELETE CASCADE,
    signal_key TEXT NOT NULL UNIQUE,
    strategy_version TEXT NOT NULL,
    signal_class TEXT NOT NULL CHECK (signal_class IN ('STRICT','SHADOW')),
    execution_assumption TEXT NOT NULL,
    included_primary BOOLEAN NOT NULL DEFAULT TRUE,
    primary_exclusion_reason TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long','short')),
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ NOT NULL,
    setup_type TEXT NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    trigger_price DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION NOT NULL,
    tp1 DOUBLE PRECISION NOT NULL,
    tp2 DOUBLE PRECISION NOT NULL,
    tp3 DOUBLE PRECISION NOT NULL,
    risk_per_unit DOUBLE PRECISION NOT NULL,
    expected_rr DOUBLE PRECISION NOT NULL,
    modeled_tp2_r DOUBLE PRECISION NOT NULL,
    expansion_score DOUBLE PRECISION NOT NULL,
    direction_score DOUBLE PRECISION NOT NULL,
    side_direction_score DOUBLE PRECISION NOT NULL,
    quality_score DOUBLE PRECISION NOT NULL,
    setup_score DOUBLE PRECISION NOT NULL,
    turnover_24h_usdc DOUBLE PRECISION NOT NULL,
    modeled_spread_bps DOUBLE PRECISION NOT NULL,
    cost_bps DOUBLE PRECISION NOT NULL,
    cost_r DOUBLE PRECISION NOT NULL,
    timeframe_conflict BOOLEAN NOT NULL,
    btc_structure_1h TEXT,
    btc_structure_4h TEXT,
    btc_volatility_regime TEXT,
    bars_observed INTEGER NOT NULL,
    mfe_r DOUBLE PRECISION NOT NULL,
    mae_r DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION NOT NULL,
    exit_reason TEXT NOT NULL,
    gross_r DOUBLE PRECISION NOT NULL,
    net_r DOUBLE PRECISION NOT NULL,
    candidate_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_day_backtest_signals_job
    ON day_trade_backtest_signals (job_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_day_backtest_signals_primary
    ON day_trade_backtest_signals (job_id, included_primary, signal_class, side);
CREATE INDEX IF NOT EXISTS idx_day_backtest_signals_symbol
    ON day_trade_backtest_signals (job_id, symbol, opened_at DESC);
"""


@dataclass
class ReplayResult:
    signals: list[dict[str, Any]]
    bars_fetched: int
    evaluation_bars: int


class HistoricalBybitAPI(BybitAPI):
    async def klines_range(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[Bar]:
        """Fetch closed 5m bars in [start_ms, end_ms), paging backwards."""
        cursor_end = end_ms - 1
        by_start: dict[int, Bar] = {}
        while cursor_end >= start_ms:
            payload = await self.public_get(
                "/v5/market/kline",
                {
                    "category": "spot",
                    "symbol": symbol,
                    "interval": "5",
                    "start": start_ms,
                    "end": cursor_end,
                    "limit": BACKTEST_PAGE_LIMIT,
                },
            )
            rows = payload.get("result", {}).get("list", [])
            if not rows:
                break
            starts: list[int] = []
            for row in rows:
                bar_start = int(row[0])
                starts.append(bar_start)
                if bar_start < start_ms or bar_start + FIVE_MIN_MS > end_ms:
                    continue
                by_start[bar_start] = Bar(
                    start_ms=bar_start,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    turnover=float(row[6]),
                )
            earliest = min(starts)
            if earliest <= start_ms or len(rows) < BACKTEST_PAGE_LIMIT:
                break
            next_end = earliest - 1
            if next_end >= cursor_end:
                break
            cursor_end = next_end
            if BACKTEST_REQUEST_PAUSE_SECONDS:
                await asyncio.sleep(BACKTEST_REQUEST_PAUSE_SECONDS)
        return [by_start[key] for key in sorted(by_start)]


def _dt_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def aggregate_bars(bars_5m: list[Bar], interval_minutes: int) -> list[Bar]:
    interval_ms = interval_minutes * 60 * 1000
    expected = interval_minutes // 5
    groups: dict[int, list[Bar]] = {}
    for bar in bars_5m:
        bucket = (bar.start_ms // interval_ms) * interval_ms
        groups.setdefault(bucket, []).append(bar)
    output: list[Bar] = []
    for bucket in sorted(groups):
        rows = sorted(groups[bucket], key=lambda item: item.start_ms)
        if len(rows) != expected:
            continue
        expected_starts = [bucket + index * FIVE_MIN_MS for index in range(expected)]
        if [row.start_ms for row in rows] != expected_starts:
            continue
        output.append(
            Bar(
                start_ms=bucket,
                open=rows[0].open,
                high=max(row.high for row in rows),
                low=min(row.low for row in rows),
                close=rows[-1].close,
                volume=sum(row.volume for row in rows),
                turnover=sum(row.turnover for row in rows),
            )
        )
    return output


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
    if turnover_24h >= BACKTEST_MIN_TURNOVER_USDC:
        return 22.0
    return 60.0


def _prefix_sums(values: Iterable[float]) -> list[float]:
    output = [0.0]
    total = 0.0
    for value in values:
        total += float(value)
        output.append(total)
    return output


def _rolling_sum(prefix: list[float], end_index_inclusive: int, count: int) -> float:
    end = end_index_inclusive + 1
    start = max(0, end - count)
    return prefix[end] - prefix[start]


def _higher_prefix(
    bars: list[Bar],
    close_times: list[int],
    evaluation_time_ms: int,
    limit: int,
) -> list[Bar]:
    end = bisect.bisect_right(close_times, evaluation_time_ms)
    return bars[max(0, end - limit):end]


def _return_pct(bars: list[Bar], periods: int) -> float:
    if len(bars) <= periods or bars[-periods - 1].close == 0:
        return 0.0
    return (bars[-1].close / bars[-periods - 1].close - 1.0) * 100.0


def _signal_class(candidate: dict[str, Any]) -> str | None:
    trigger = candidate.get("trigger") or {}
    if not bool(trigger.get("triggered")):
        return None
    if (
        candidate.get("category") == "STRICT"
        and candidate.get("state") == "TRIGGERED"
        and candidate.get("decision") == "TRADE"
    ):
        return "STRICT"
    side = str(candidate.get("side"))
    executable_side = bool(candidate.get("tradeable")) and (
        side == "long" or bool(candidate.get("shortable"))
    )
    if (
        candidate.get("category") == "WATCH_ONLY"
        and candidate.get("watch_bucket") == "NEAR_STRICT"
        and executable_side
        and safe_float(candidate.get("setup_score")) >= BACKTEST_SHADOW_MIN_SETUP
        and safe_float(candidate.get("expected_rr")) >= BACKTEST_SHADOW_MIN_RR
    ):
        return "SHADOW"
    return None


def evaluate_outcome(
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
    cost_r = (entry * BACKTEST_COST_BPS / 10_000.0) / risk
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
        "net_r": round(gross_r - cost_r, 6),
        "risk_per_unit": risk,
        "cost_r": cost_r,
    }


def _volatility_regime(analysis: DayAnalysis) -> str:
    ratio = analysis.atr_ratio_15m
    if ratio >= 1.35:
        return "EXPANDING"
    if ratio <= 0.80:
        return "COMPRESSED"
    return "NORMAL"


def replay_symbol(
    job_id: int,
    symbol_meta: dict[str, Any],
    bars_5m: list[Bar],
    btc_bars_5m: list[Bar],
    start_at: datetime,
    end_at: datetime,
) -> ReplayResult:
    symbol = str(symbol_meta["symbol"]).upper()
    if len(bars_5m) < 500 or len(btc_bars_5m) < 500:
        return ReplayResult([], len(bars_5m), 0)

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
    start_index = bisect.bisect_left(bar_starts, start_ms)
    horizon_bars = BACKTEST_HORIZON_HOURS * 12
    signals: list[dict[str, Any]] = []
    last_primary_exit: dict[str, int] = {"long": 0, "short": 0}
    evaluation_bars = 0

    current_shortable_proxy = bool(symbol_meta.get("current_shortable_proxy"))
    tick_size = max(safe_float(symbol_meta.get("tick_size"), 0.0), 1e-12)

    for index in range(start_index, len(bars_5m) - 1):
        current_bar = bars_5m[index]
        evaluation_time_ms = current_bar.start_ms + FIVE_MIN_MS
        if evaluation_time_ms > end_ms:
            break
        # A complete 8-hour outcome horizon is mandatory. Signals near the
        # replay endpoint are excluded rather than force-closed early.
        if index + horizon_bars >= len(bars_5m):
            break
        bars5_slice = bars_5m[max(0, index - 219):index + 1]
        sweep_config = SweepResearchConfig(
            volume_confirmation_ratio=DAY_TRIGGER_VOLUME_RATIO
        )
        trigger_sides = []
        for side in ("long", "short"):
            sweep_ready = latest_bar_sweep_setup(
                bars5_slice,
                side,
                config=sweep_config,
            ) is not None
            breakout_ready = recent_closed_5m_range_breakout(
                bars5_slice,
                side,
                active_bars=DAY_BREAKOUT_ACTIVE_BARS,
            ) is not None
            if sweep_ready or breakout_ready:
                trigger_sides.append(side)
        if not trigger_sides:
            continue
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
            turnover24 >= BACKTEST_MIN_TURNOVER_USDC
            and spread_bps <= BACKTEST_MAX_MODELED_SPREAD_BPS
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
                (current / bars_5m[max(0, index - 288)].close - 1.0) * 100.0
                if index >= 288 and bars_5m[index - 288].close > 0
                else 0.0
            ),
            tradeable=tradeable,
            liquidity_reasons=(
                []
                if tradeable
                else [
                    f"Historical rolling turnover/modelled spread gate failed: "
                    f"turnover={turnover24:.2f}, spread_model={spread_bps:.2f}bps"
                ]
            ),
            discovery_source="historical_replay",
        )
        fast = calculate_fast_result(instrument, bars5_slice, symbol15)
        btc_r1h = _return_pct(btc15_slice, 4)
        btc_r4h = _return_pct(btc1h_slice, 4)
        analysis = analyze_day_market(fast, symbol1h, symbol4h, btc_r1h, btc_r4h)
        # Technical short generation is separated from historical executability.
        analysis.shortable = BACKTEST_SHORT_MODE != "disabled"
        btc_end = bisect.bisect_right(btc_starts, current_bar.start_ms)
        btc5_slice = btc_bars_5m[max(0, btc_end - 220):btc_end]
        if len(btc5_slice) < 100:
            continue
        btc_price = btc5_slice[-1].close
        btc_instrument = Instrument(
            symbol="BTCUSDC", base="BTC", quote="USDC", margin_trading="both",
            tick_size=0.01, turnover_24h=10_000_000, volume_24h=1,
            last_price=btc_price, bid=btc_price * 0.99985,
            ask=btc_price * 1.00015, spread_bps=3, price_change_24h_pct=0,
            tradeable=True, liquidity_reasons=[], discovery_source="historical_replay",
        )
        btc_fast = calculate_fast_result(btc_instrument, btc5_slice, btc15_slice)
        btc_analysis = analyze_day_market(btc_fast, btc1h_slice, btc4h_slice, btc_r1h, btc_r4h)

        for side in trigger_sides:
            if side == "short" and BACKTEST_SHORT_MODE == "disabled":
                continue
            if (
                side == "short"
                and BACKTEST_SHORT_MODE == "current_proxy"
                and not current_shortable_proxy
            ):
                continue
            candidate = build_day_candidate(analysis, side, _dt_from_ms(evaluation_time_ms))
            if candidate is None:
                continue
            signal_class = _signal_class(candidate)
            if signal_class is None:
                continue
            if side == "long":
                execution_assumption = "SPOT_LONG_HISTORICAL_TURNOVER_AND_SPREAD_MODEL"
            elif BACKTEST_SHORT_MODE == "current_proxy" and current_shortable_proxy:
                execution_assumption = "SHORT_CURRENT_BORROWABILITY_PROXY"
            else:
                execution_assumption = "SHORT_TECHNICAL_BORROW_UNVERIFIED"

            targets = candidate.get("targets") or []
            if len(targets) < 3:
                continue
            future = bars_5m[index + 1:min(len(bars_5m), index + 1 + horizon_bars)]
            outcome = evaluate_outcome(
                side,
                current_bar.close,
                safe_float(candidate.get("stop")),
                safe_float(targets[0]),
                safe_float(targets[1]),
                safe_float(targets[2]),
                future,
            )
            if outcome is None:
                continue
            opened_at = _dt_from_ms(evaluation_time_ms)
            included_primary = True
            primary_exclusion_reason = None
            if BACKTEST_PRIMARY_NO_OVERLAP and evaluation_time_ms < last_primary_exit[side]:
                included_primary = False
                primary_exclusion_reason = "OVERLAPPING_SAME_SYMBOL_SIDE_SIGNAL"
            else:
                last_primary_exit[side] = _ms(outcome["closed_at"])
            raw_key = (
                f"{STRATEGY_VERSION}|{job_id}|{symbol}|{side}|"
                f"{current_bar.start_ms}|{signal_class}"
            )
            signal_key = "backtest:" + hashlib.sha256(raw_key.encode()).hexdigest()[:28]
            signals.append({
                "job_id": job_id,
                "signal_key": signal_key,
                "strategy_version": STRATEGY_VERSION,
                "signal_class": signal_class,
                "execution_assumption": execution_assumption,
                "included_primary": included_primary,
                "primary_exclusion_reason": primary_exclusion_reason,
                "symbol": symbol,
                "side": side,
                "opened_at": opened_at,
                "closed_at": outcome["closed_at"],
                "setup_type": str(candidate.get("setup_type", "UNKNOWN")),
                "entry_price": current_bar.close,
                "trigger_price": safe_float((candidate.get("trigger") or {}).get("price"), current_bar.close),
                "stop_price": safe_float(candidate.get("stop")),
                "tp1": safe_float(targets[0]),
                "tp2": safe_float(targets[1]),
                "tp3": safe_float(targets[2]),
                "risk_per_unit": outcome["risk_per_unit"],
                "expected_rr": safe_float(candidate.get("expected_rr")),
                "modeled_tp2_r": abs(safe_float(targets[1]) - current_bar.close) / outcome["risk_per_unit"],
                "expansion_score": safe_float(candidate.get("expansion_score")),
                "direction_score": safe_float(candidate.get("direction_score")),
                "side_direction_score": safe_float(candidate.get("side_direction_score")),
                "quality_score": safe_float(candidate.get("quality_score")),
                "setup_score": safe_float(candidate.get("setup_score")),
                "turnover_24h_usdc": turnover24,
                "modeled_spread_bps": spread_bps,
                "cost_bps": BACKTEST_COST_BPS,
                "cost_r": outcome["cost_r"],
                "timeframe_conflict": bool(candidate.get("timeframe_conflict")),
                "btc_structure_1h": btc_analysis.structure_1h,
                "btc_structure_4h": btc_analysis.structure_4h,
                "btc_volatility_regime": _volatility_regime(btc_analysis),
                "bars_observed": outcome["bars_observed"],
                "mfe_r": outcome["mfe_r"],
                "mae_r": outcome["mae_r"],
                "exit_price": outcome["exit_price"],
                "exit_reason": outcome["exit_reason"],
                "gross_r": outcome["gross_r"],
                "net_r": outcome["net_r"],
                "candidate_payload": candidate,
            })
    return ReplayResult(signals, len(bars_5m), evaluation_bars)


async def ensure_schema(connection: asyncpg.Connection) -> None:
    await connection.execute(SCHEMA_SQL)


def job_parameters() -> dict[str, Any]:
    return {
        "lookback_days": BACKTEST_LOOKBACK_DAYS,
        "warmup_days": BACKTEST_WARMUP_DAYS,
        "symbol_limit": BACKTEST_SYMBOL_LIMIT,
        "batch_symbols": BACKTEST_BATCH_SYMBOLS,
        "horizon_hours": BACKTEST_HORIZON_HOURS,
        "cost_bps": BACKTEST_COST_BPS,
        "minimum_turnover_usdc": BACKTEST_MIN_TURNOVER_USDC,
        "max_modeled_spread_bps": BACKTEST_MAX_MODELED_SPREAD_BPS,
        "shadow_min_setup": BACKTEST_SHADOW_MIN_SETUP,
        "shadow_min_rr": BACKTEST_SHADOW_MIN_RR,
        "short_mode": BACKTEST_SHORT_MODE,
        "primary_no_overlap": BACKTEST_PRIMARY_NO_OVERLAP,
        "universe_mode": "CURRENT_TOP_LIQUID_ACTIVE_USDC",
        "higher_timeframes": "AGGREGATED_FROM_5M_CLOSED_BARS",
    }


WARNINGS = [
    "Current active-symbol selection creates survivorship bias.",
    "Historical spread is modelled from rolling 24h turnover; it is not bid/ask history.",
    "Historical short borrowability is unavailable; technical shorts are research-only unless explicitly labelled current proxy.",
    "Coinalyze OI/funding is excluded from replay v0.7.5.",
    "The selected liquid universe is replayed directly; historical full-universe top-30 promotion is not reconstructed.",
    "Entry is modelled at the closed trigger-bar close and costs are a configurable assumption.",
    "Same-candle stop and TP2 is treated as stop-first.",
]


async def create_job_if_needed(
    connection: asyncpg.Connection,
    api: HistoricalBybitAPI,
) -> dict[str, Any]:
    existing = await connection.fetchrow(
        """
        SELECT * FROM day_trade_backtest_jobs
        WHERE job_name = $1 AND strategy_version = $2
        ORDER BY id DESC LIMIT 1
        """,
        BACKTEST_JOB_NAME,
        STRATEGY_VERSION,
    )
    if existing:
        return dict(existing)

    instruments_raw, tickers = await asyncio.gather(
        api.instruments(), api.tickers()
    )
    margin_data: dict[str, dict[str, Any]] = {}
    if BACKTEST_SHORT_MODE == "current_proxy":
        try:
            margin_data = await api.vip_margin_data()
        except Exception:
            margin_data = {}
    instruments, _, _ = normalize_usdc_universe(instruments_raw, tickers)
    by_symbol = {item.symbol: item for item in instruments}
    if BACKTEST_SYMBOLS:
        selected = [by_symbol[s] for s in BACKTEST_SYMBOLS if s in by_symbol]
    else:
        selected = sorted(
            instruments,
            key=lambda item: item.turnover_24h,
            reverse=True,
        )[:BACKTEST_SYMBOL_LIMIT]
    btc = by_symbol.get("BTCUSDC")
    if btc and all(item.symbol != "BTCUSDC" for item in selected):
        selected = ([btc] + selected)[:BACKTEST_SYMBOL_LIMIT]
    if not selected:
        raise RuntimeError("No eligible current Bybit EU USDC symbols for backtest")

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
    start_at = end_at - timedelta(days=BACKTEST_LOOKBACK_DAYS)
    warmup_start = start_at - timedelta(days=BACKTEST_WARMUP_DAYS)
    params = job_parameters()
    job_key_raw = f"{STRATEGY_VERSION}|{BACKTEST_JOB_NAME}|{start_at.isoformat()}|{end_at.isoformat()}|{json.dumps(params, sort_keys=True)}"
    job_key = hashlib.sha256(job_key_raw.encode()).hexdigest()
    row = await connection.fetchrow(
        """
        INSERT INTO day_trade_backtest_jobs (
            job_key, job_name, strategy_version, status,
            start_at, end_at, warmup_start_at, parameters,
            universe, warnings, total_symbols
        ) VALUES ($1,$2,$3,'PENDING',$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10)
        RETURNING *
        """,
        job_key,
        BACKTEST_JOB_NAME,
        STRATEGY_VERSION,
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
        INSERT INTO day_trade_backtest_symbols (job_id, symbol, status, metadata)
        VALUES ($1,$2,'PENDING',$3::jsonb)
        ON CONFLICT (job_id, symbol) DO NOTHING
        """,
        [(job["id"], item["symbol"], json.dumps(item)) for item in universe],
    )
    return job


async def reset_stale_symbols(connection: asyncpg.Connection, job_id: int) -> None:
    await connection.execute(
        """
        UPDATE day_trade_backtest_symbols
        SET status='PENDING', started_at=NULL,
            last_error=COALESCE(last_error,'') || ' | stale run reset'
        WHERE job_id=$1 AND status='RUNNING'
          AND started_at < NOW() - ($2::int * INTERVAL '1 minute')
        """,
        job_id,
        BACKTEST_STALE_RUN_MINUTES,
    )


async def claim_symbols(
    connection: asyncpg.Connection,
    job_id: int,
) -> list[dict[str, Any]]:
    async with connection.transaction():
        rows = await connection.fetch(
            """
            SELECT id, symbol, metadata
            FROM day_trade_backtest_symbols
            WHERE job_id=$1 AND status='PENDING'
            ORDER BY CASE WHEN symbol='BTCUSDC' THEN 0 ELSE 1 END, id
            FOR UPDATE SKIP LOCKED
            LIMIT $2
            """,
            job_id,
            BACKTEST_BATCH_SYMBOLS,
        )
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        await connection.execute(
            """
            UPDATE day_trade_backtest_symbols
            SET status='RUNNING', started_at=NOW(), last_error=NULL
            WHERE id = ANY($1::bigint[])
            """,
            ids,
        )
    return [dict(row) for row in rows]


async def insert_signals(
    connection: asyncpg.Connection,
    signals: list[dict[str, Any]],
) -> int:
    inserted = 0
    for item in signals:
        row = await connection.fetchrow(
            """
            INSERT INTO day_trade_backtest_signals (
                job_id, signal_key, strategy_version, signal_class,
                execution_assumption, included_primary, primary_exclusion_reason,
                symbol, side, opened_at, closed_at, setup_type,
                entry_price, trigger_price, stop_price, tp1, tp2, tp3,
                risk_per_unit, expected_rr, modeled_tp2_r,
                expansion_score, direction_score, side_direction_score,
                quality_score, setup_score, turnover_24h_usdc,
                modeled_spread_bps, cost_bps, cost_r, timeframe_conflict,
                btc_structure_1h, btc_structure_4h, btc_volatility_regime,
                bars_observed, mfe_r, mae_r, exit_price, exit_reason,
                gross_r, net_r, candidate_payload
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                $19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,
                $35,$36,$37,$38,$39,$40,$41,$42::jsonb
            )
            ON CONFLICT (signal_key) DO NOTHING
            RETURNING id
            """,
            item["job_id"], item["signal_key"], item["strategy_version"],
            item["signal_class"], item["execution_assumption"],
            item["included_primary"], item["primary_exclusion_reason"],
            item["symbol"], item["side"], item["opened_at"], item["closed_at"],
            item["setup_type"], item["entry_price"], item["trigger_price"],
            item["stop_price"], item["tp1"], item["tp2"], item["tp3"],
            item["risk_per_unit"], item["expected_rr"], item["modeled_tp2_r"],
            item["expansion_score"], item["direction_score"],
            item["side_direction_score"], item["quality_score"], item["setup_score"],
            item["turnover_24h_usdc"], item["modeled_spread_bps"],
            item["cost_bps"], item["cost_r"], item["timeframe_conflict"],
            item["btc_structure_1h"], item["btc_structure_4h"],
            item["btc_volatility_regime"], item["bars_observed"], item["mfe_r"],
            item["mae_r"], item["exit_price"], item["exit_reason"],
            item["gross_r"], item["net_r"],
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
        FROM day_trade_backtest_symbols WHERE job_id=$1
        """,
        job_id,
    )
    total_signals = int(await connection.fetchval(
        "SELECT COUNT(*) FROM day_trade_backtest_signals WHERE job_id=$1", job_id
    ) or 0)
    primary_signals = int(await connection.fetchval(
        "SELECT COUNT(*) FROM day_trade_backtest_signals WHERE job_id=$1 AND included_primary", job_id
    ) or 0)
    completed = int(counts["completed"] or 0)
    failed = int(counts["failed"] or 0)
    pending = int(counts["pending"] or 0)
    running = int(counts["running"] or 0)
    if pending == 0 and running == 0:
        status = "COMPLETED" if failed == 0 else "PARTIAL"
        completed_at = datetime.now(timezone.utc)
    else:
        status = "RUNNING"
        completed_at = None
    await connection.execute(
        """
        UPDATE day_trade_backtest_jobs
        SET status=$2, completed_symbols=$3, failed_symbols=$4,
            total_signals=$5, primary_signals=$6, last_run_at=NOW(),
            started_at=COALESCE(started_at,NOW()),
            completed_at=CASE WHEN $7::timestamptz IS NULL THEN completed_at ELSE COALESCE(completed_at,$7) END,
            updated_at=NOW()
        WHERE id=$1
        """,
        job_id, status, completed, failed, total_signals, primary_signals, completed_at,
    )
    return {
        "status": status,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "running": running,
        "total_signals": total_signals,
        "primary_signals": primary_signals,
    }


async def run_backtest_batch() -> dict[str, Any]:
    if not BACKTEST_ENABLED:
        return {"enabled": False, "status": "DISABLED"}
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    timeout = httpx.Timeout(45.0, connect=15.0)
    limits = httpx.Limits(max_connections=BACKTEST_HTTP_CONCURRENCY)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = HistoricalBybitAPI(client)
        connection = await asyncpg.connect(DATABASE_URL, timeout=30)
        try:
            await ensure_schema(connection)
            job = await create_job_if_needed(connection, api)
            job_id = int(job["id"])
            if str(job["status"]) in {"COMPLETED", "PARTIAL", "FAILED"}:
                counts = await update_job_counts(connection, job_id)
                return {"enabled": True, "job_id": job_id, "job_name": BACKTEST_JOB_NAME, **counts}
            await connection.execute(
                """UPDATE day_trade_backtest_jobs
                   SET status='RUNNING', started_at=COALESCE(started_at,NOW()),
                       last_run_at=NOW(), updated_at=NOW() WHERE id=$1""",
                job_id,
            )
            await reset_stale_symbols(connection, job_id)
            claimed = await claim_symbols(connection, job_id)
            if not claimed:
                counts = await update_job_counts(connection, job_id)
                return {"enabled": True, "job_id": job_id, "job_name": BACKTEST_JOB_NAME, **counts}

            warmup_start = job["warmup_start_at"]
            start_at = job["start_at"]
            end_at = job["end_at"]
            btc_bars = await api.klines_range("BTCUSDC", _ms(warmup_start), _ms(end_at))
            batch_results: list[dict[str, Any]] = []
            for row in claimed:
                symbol_id = int(row["id"])
                symbol = str(row["symbol"])
                metadata = row["metadata"]
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                try:
                    bars = btc_bars if symbol == "BTCUSDC" else await api.klines_range(
                        symbol, _ms(warmup_start), _ms(end_at)
                    )
                    replay = replay_symbol(job_id, metadata, bars, btc_bars, start_at, end_at)
                    async with connection.transaction():
                        inserted = await insert_signals(connection, replay.signals)
                        stored_count = int(await connection.fetchval(
                            """SELECT COUNT(*) FROM day_trade_backtest_signals
                               WHERE job_id=$1 AND symbol=$2""",
                            job_id, symbol,
                        ) or 0)
                        primary_count = int(await connection.fetchval(
                            """SELECT COUNT(*) FROM day_trade_backtest_signals
                               WHERE job_id=$1 AND symbol=$2 AND included_primary""",
                            job_id, symbol,
                        ) or 0)
                        await connection.execute(
                            """
                            UPDATE day_trade_backtest_symbols
                            SET status='COMPLETED', bars_fetched=$2, evaluation_bars=$3,
                                signal_count=$4, primary_signal_count=$5,
                                completed_at=NOW(), last_error=NULL
                            WHERE id=$1
                            """,
                            symbol_id, replay.bars_fetched, replay.evaluation_bars,
                            stored_count, primary_count,
                        )
                    batch_results.append({
                        "symbol": symbol,
                        "status": "COMPLETED",
                        "bars": replay.bars_fetched,
                        "evaluation_bars": replay.evaluation_bars,
                        "signals_inserted_this_run": inserted,
                        "signals_stored": stored_count,
                        "primary_signals": primary_count,
                    })
                except Exception as exc:
                    await connection.execute(
                        """
                        UPDATE day_trade_backtest_symbols
                        SET status='FAILED', completed_at=NOW(), last_error=$2
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
                "job_name": BACKTEST_JOB_NAME,
                "processed": batch_results,
                **counts,
            }
        finally:
            await connection.close()
