"""Trading Radar — liquidity-sweep historical A/B replay v0.1.

RESEARCH ONLY. No live strategy changes.

This script reuses the completed v0.7.2 backtest job window/universe and
re-fetches historical Bybit EU USDC spot 5m bars. It evaluates sweep entries
from sweep_research.py and compares:

A) SWEEP_4H_VETO
   Sweep entry + current v0.7.2 score/liquidity gates + current 4H hard veto.

B) SWEEP_4H_CONTEXT
   Identical conditions, except 4H conflict is recorded as context and does
   NOT exclude the research event.

Important semantics:
- LONG historical research uses Bybit EU USDC spot klines.
- SHORT historical research is TECHNICAL ONLY because historical Bybit EU
  spot-margin borrowability is unavailable. It is never execution proof.
- OI/funding is excluded from this replay. It is not a hard gate.
- Validation is not an optimization set. Default split is final 30 days
  validation, preceding period development.
- Results are printed as JSON only; no database tables are modified.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import math
import os
import statistics
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import asyncpg
import httpx

from backtest import (
    BACKTEST_COST_BPS,
    BACKTEST_HORIZON_HOURS,
    BACKTEST_MAX_MODELED_SPREAD_BPS,
    BACKTEST_MIN_TURNOVER_USDC,
    HistoricalBybitAPI,
    aggregate_bars,
    evaluate_outcome,
    modeled_spread_bps,
)
from day_worker import (
    DAY_MIN_DIRECTION_SCORE,
    DAY_MIN_EXPANSION_SCORE,
    DAY_MIN_QUALITY_SCORE,
    DAY_MIN_RR,
    DAY_MIN_SETUP_SCORE,
    analyze_day_market,
    calculate_fast_result,
)
from sweep_research import (
    DEFAULT_CONFIG,
    FIVE_MIN_MS,
    RESEARCH_VERSION,
    _evaluate_sweep_normalized,
    normalize_bars,
)
from worker import Bar, Instrument, safe_float

SCRIPT_VERSION = "sweep-replay-0.1"

DATABASE_URL = os.getenv("DATABASE_URL", "")
BASELINE_JOB_ID = int(os.getenv("SWEEP_BASELINE_JOB_ID", "3"))
SWEEP_SYMBOL_LIMIT = max(1, min(int(os.getenv("SWEEP_RESEARCH_SYMBOL_LIMIT", "3")), 60))
SWEEP_SYMBOLS = [
    item.strip().upper()
    for item in os.getenv("SWEEP_RESEARCH_SYMBOLS", "").split(",")
    if item.strip()
]
SWEEP_VALIDATION_DAYS = max(1, min(int(os.getenv("SWEEP_VALIDATION_DAYS", "30")), 60))
SWEEP_HTTP_CONCURRENCY = max(1, min(int(os.getenv("SWEEP_HTTP_CONCURRENCY", "2")), 4))
SWEEP_PRIMARY_NO_OVERLAP = os.getenv("SWEEP_PRIMARY_NO_OVERLAP", "true").strip().lower() in {
    "1", "true", "yes", "on"
}

COHORT_4H_VETO = "SWEEP_4H_VETO"
COHORT_4H_CONTEXT = "SWEEP_4H_CONTEXT"
RAW_COHORT = "SWEEP_ENTRY_READY_RAW"


def _dt_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


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


def _closed_prefix(
    bars: list[Bar],
    close_times: list[int],
    evaluation_time_ms: int,
    limit: int,
) -> list[Bar]:
    end = bisect.bisect_right(close_times, evaluation_time_ms)
    return bars[max(0, end - limit):end]


def _return_pct(bars: list[Bar], periods: int) -> float:
    if len(bars) <= periods:
        return 0.0
    previous = bars[-periods - 1].close
    if previous <= 0:
        return 0.0
    return (bars[-1].close / previous - 1.0) * 100.0


def _side_direction_score(analysis: Any, side: str) -> float:
    return (
        float(analysis.direction_score)
        if side == "long"
        else -float(analysis.direction_score)
    )


def _setup_score(analysis: Any, side: str) -> float:
    side_direction = _side_direction_score(analysis, side)
    return max(
        0.0,
        min(
            100.0,
            0.35 * float(analysis.expansion_score)
            + 0.35 * max(side_direction, 0.0)
            + 0.30 * float(analysis.quality_score),
        ),
    )


def _conflict_4h(analysis: Any, side: str) -> bool:
    structure = str(analysis.structure_4h).lower()
    return (
        "bearish" in structure
        if side == "long"
        else "bullish" in structure
    )


def _score_gates_pass(analysis: Any, side: str) -> bool:
    score = _setup_score(analysis, side)
    side_direction = _side_direction_score(analysis, side)
    return bool(
        float(analysis.expansion_score) >= DAY_MIN_EXPANSION_SCORE
        and side_direction >= DAY_MIN_DIRECTION_SCORE
        and float(analysis.quality_score) >= DAY_MIN_QUALITY_SCORE
        and score >= DAY_MIN_SETUP_SCORE
    )


def _targets_for_net_r(
    side: str,
    entry: float,
    stop: float,
) -> tuple[float, float, float] | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    direction = 1.0 if side == "long" else -1.0
    assumed_cost = entry * BACKTEST_COST_BPS / 10_000.0

    def target(net_r: float) -> float:
        return entry + direction * (net_r * risk + assumed_cost)

    return target(1.0), target(DAY_MIN_RR), target(2.5)


def _period(opened_at: datetime, validation_start: datetime) -> str:
    return "VALIDATION" if opened_at >= validation_start else "DEVELOPMENT"


def _execution_assumption(side: str) -> str:
    if side == "long":
        return "BYBIT_EU_USDC_SPOT_HISTORICAL_KLINE"
    return "SHORT_TECHNICAL_BORROW_UNVERIFIED"


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "sample_size": 0,
            "tp2_count": 0,
            "stop_count": 0,
            "time_exit_count": 0,
            "target_hit_rate_pct": None,
            "positive_net_rate_pct": None,
            "average_net_r": None,
            "median_net_r": None,
            "profit_factor": None,
            "average_mfe_r": None,
            "average_mae_r": None,
        }

    net = [float(row["net_r"]) for row in rows]
    mfe = [float(row["mfe_r"]) for row in rows]
    mae = [float(row["mae_r"]) for row in rows]
    tp2 = sum(row["exit_reason"] == "TP2" for row in rows)
    stops = sum(
        row["exit_reason"] in {"STOP", "AMBIGUOUS_STOP_FIRST"} for row in rows
    )
    time_exits = sum(row["exit_reason"] == "TIME_EXIT" for row in rows)
    positive = sum(value > 0 for value in net)
    gains = sum(value for value in net if value > 0)
    losses = -sum(value for value in net if value < 0)
    pf = gains / losses if losses > 0 else (math.inf if gains > 0 else None)

    return {
        "sample_size": len(rows),
        "tp2_count": tp2,
        "stop_count": stops,
        "time_exit_count": time_exits,
        "target_hit_rate_pct": round(tp2 / len(rows) * 100.0, 3),
        "positive_net_rate_pct": round(positive / len(rows) * 100.0, 3),
        "average_net_r": round(statistics.fmean(net), 6),
        "median_net_r": round(statistics.median(net), 6),
        "profit_factor": None if pf is None else (
            "INF" if math.isinf(pf) else round(pf, 6)
        ),
        "average_mfe_r": round(statistics.fmean(mfe), 6),
        "average_mae_r": round(statistics.fmean(mae), 6),
    }


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def selected(**filters: str) -> list[dict[str, Any]]:
        return [
            row for row in rows
            if all(str(row.get(key)) == value for key, value in filters.items())
        ]

    output: dict[str, Any] = {
        "overall": _aggregate(rows),
        "by_period": {},
        "by_side": {},
        "by_cohort": {},
        "cohort_period_side": {},
    }

    for period in ("DEVELOPMENT", "VALIDATION"):
        output["by_period"][period] = _aggregate(selected(period=period))
    for side in ("long", "short"):
        output["by_side"][side] = _aggregate(selected(side=side))
    for cohort in (RAW_COHORT, COHORT_4H_VETO, COHORT_4H_CONTEXT):
        output["by_cohort"][cohort] = _aggregate(selected(cohort=cohort))
        output["cohort_period_side"][cohort] = {}
        for period in ("DEVELOPMENT", "VALIDATION"):
            output["cohort_period_side"][cohort][period] = {}
            for side in ("long", "short"):
                output["cohort_period_side"][cohort][period][side] = _aggregate(
                    selected(cohort=cohort, period=period, side=side)
                )
    return output


async def _load_baseline_job(connection: asyncpg.Connection) -> dict[str, Any]:
    row = await connection.fetchrow(
        """
        SELECT id,status,start_at,end_at,warmup_start_at,parameters,universe,warnings,
               completed_symbols,failed_symbols,total_signals,primary_signals
        FROM day_trade_backtest_jobs
        WHERE id=$1
        """,
        BASELINE_JOB_ID,
    )
    if row is None:
        raise RuntimeError(f"Baseline backtest job id={BASELINE_JOB_ID} not found")
    job = dict(row)
    if job["status"] != "COMPLETED":
        raise RuntimeError(
            f"Baseline job id={BASELINE_JOB_ID} must be COMPLETED, got {job['status']}"
        )
    for key in ("parameters", "universe", "warnings"):
        if isinstance(job.get(key), str):
            job[key] = json.loads(job[key])
    return job


def _selected_universe(job: dict[str, Any]) -> list[dict[str, Any]]:
    universe = list(job.get("universe") or [])
    by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in universe
        if row.get("symbol")
    }

    if SWEEP_SYMBOLS:
        selected = [by_symbol[s] for s in SWEEP_SYMBOLS if s in by_symbol]
    else:
        selected = universe[:SWEEP_SYMBOL_LIMIT]

    # BTC is needed as context even if it is not part of output selection.
    return selected


async def _fetch_bars(
    api: HistoricalBybitAPI,
    symbol: str,
    start_ms: int,
    end_ms: int,
    semaphore: asyncio.Semaphore,
) -> list[Bar]:
    async with semaphore:
        return await api.klines_range(symbol, start_ms, end_ms)


def _build_analysis(
    *,
    symbol_meta: dict[str, Any],
    bars_5m: list[Bar],
    index: int,
    bars15: list[Bar],
    bars1h: list[Bar],
    bars4h: list[Bar],
    closes15: list[int],
    closes1h: list[int],
    closes4h: list[int],
    turnover_prefix: list[float],
    volume_prefix: list[float],
    btc_bars_5m: list[Bar],
    btc15: list[Bar],
    btc1h: list[Bar],
    btc4h: list[Bar],
    btc_closes15: list[int],
    btc_closes1h: list[int],
    btc_closes4h: list[int],
    btc_starts: list[int],
) -> Any | None:
    current_bar = bars_5m[index]
    evaluation_time_ms = current_bar.start_ms + FIVE_MIN_MS

    symbol15 = _closed_prefix(bars15, closes15, evaluation_time_ms, 120)
    symbol1h = _closed_prefix(bars1h, closes1h, evaluation_time_ms, 140)
    symbol4h = _closed_prefix(bars4h, closes4h, evaluation_time_ms, 100)
    bars5_slice = bars_5m[max(0, index - 219):index + 1]

    if (
        len(bars5_slice) < 100
        or len(symbol15) < 55
        or len(symbol1h) < 55
        or len(symbol4h) < 55
    ):
        return None

    turnover24 = _rolling_sum(turnover_prefix, index, 288)
    spread_bps = modeled_spread_bps(turnover24)
    tradeable = (
        turnover24 >= BACKTEST_MIN_TURNOVER_USDC
        and spread_bps <= BACKTEST_MAX_MODELED_SPREAD_BPS
    )

    current = current_bar.close
    half_spread = spread_bps / 20_000.0
    tick_size = max(safe_float(symbol_meta.get("tick_size"), 0.0), 1e-12)
    instrument = Instrument(
        symbol=str(symbol_meta["symbol"]).upper(),
        base=str(symbol_meta.get("base") or str(symbol_meta["symbol"]).upper().removesuffix("USDC")),
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
        liquidity_reasons=[] if tradeable else ["HISTORICAL_LIQUIDITY_MODEL_FAILED"],
        discovery_source="sweep_research_replay",
    )

    fast = calculate_fast_result(instrument, bars5_slice, symbol15)

    btc_end = bisect.bisect_right(btc_starts, current_bar.start_ms)
    btc5_slice = btc_bars_5m[max(0, btc_end - 220):btc_end]
    btc15_slice = _closed_prefix(btc15, btc_closes15, evaluation_time_ms, 120)
    btc1h_slice = _closed_prefix(btc1h, btc_closes1h, evaluation_time_ms, 140)
    btc4h_slice = _closed_prefix(btc4h, btc_closes4h, evaluation_time_ms, 100)
    if (
        len(btc5_slice) < 100
        or len(btc15_slice) < 55
        or len(btc1h_slice) < 55
        or len(btc4h_slice) < 55
    ):
        return None

    btc_r1h = _return_pct(btc15_slice, 4)
    btc_r4h = _return_pct(btc1h_slice, 4)
    analysis = analyze_day_market(fast, symbol1h, symbol4h, btc_r1h, btc_r4h)

    # Historical shortability cannot be known. Mark technical shorts as
    # research-only, but do not pretend this is live Bybit EU executability.
    analysis.shortable = True
    return analysis


def replay_symbol_sweeps(
    *,
    symbol_meta: dict[str, Any],
    bars_5m: list[Bar],
    btc_bars_5m: list[Bar],
    start_at: datetime,
    end_at: datetime,
    validation_start: datetime,
) -> list[dict[str, Any]]:
    if len(bars_5m) < 500 or len(btc_bars_5m) < 500:
        return []

    bars_5m = normalize_bars(bars_5m)
    btc_bars_5m = normalize_bars(btc_bars_5m)

    symbol = str(symbol_meta["symbol"]).upper()
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

    bar_starts = [bar.start_ms for bar in bars_5m]
    btc_starts = [bar.start_ms for bar in btc_bars_5m]
    turnover_prefix = _prefix_sums(bar.turnover for bar in bars_5m)
    volume_prefix = _prefix_sums(bar.volume for bar in bars_5m)

    start_ms = _ms(start_at)
    end_ms = _ms(end_at)
    first_index = bisect.bisect_left(bar_starts, start_ms)
    horizon_bars = BACKTEST_HORIZON_HOURS * 12

    rows: list[dict[str, Any]] = []

    # Search sweep indices, but only emit when the confirmation bar is inside
    # the requested research period.
    for sweep_index in range(max(20, first_index - DEFAULT_CONFIG.max_confirmation_bars), len(bars_5m)):
        sweep_start = bars_5m[sweep_index].start_ms
        if sweep_start >= end_ms:
            break

        for side in ("long", "short"):
            event = _evaluate_sweep_normalized(
                bars_5m,
                sweep_index,
                side,
                bars_15m=bars15,
                config=DEFAULT_CONFIG,
            )
            if not bool(event.get("entry_ready")):
                continue

            confirmation_iso = event.get("structure_shift_time_5m")
            if not confirmation_iso:
                continue
            confirmation_start = int(
                datetime.fromisoformat(str(confirmation_iso).replace("Z", "+00:00")).timestamp() * 1000
            )
            confirmation_index = bisect.bisect_left(bar_starts, confirmation_start)
            if (
                confirmation_index >= len(bars_5m)
                or bars_5m[confirmation_index].start_ms != confirmation_start
            ):
                continue

            opened_at = _dt_from_ms(confirmation_start + FIVE_MIN_MS)
            if opened_at < start_at or opened_at >= end_at:
                continue

            analysis = _build_analysis(
                symbol_meta=symbol_meta,
                bars_5m=bars_5m,
                index=confirmation_index,
                bars15=bars15,
                bars1h=bars1h,
                bars4h=bars4h,
                closes15=closes15,
                closes1h=closes1h,
                closes4h=closes4h,
                turnover_prefix=turnover_prefix,
                volume_prefix=volume_prefix,
                btc_bars_5m=btc_bars_5m,
                btc15=btc15,
                btc1h=btc1h,
                btc4h=btc4h,
                btc_closes15=btc_closes15,
                btc_closes1h=btc_closes1h,
                btc_closes4h=btc_closes4h,
                btc_starts=btc_starts,
            )
            if analysis is None:
                continue

            entry = float(event["candidate_entry"])
            stop = float(event["candidate_invalidation"])
            tick_size = max(float(analysis.instrument.tick_size), 1e-12)
            risk = abs(entry - stop)
            if risk <= max(tick_size * 3.0, entry * 0.0002):
                continue

            targets = _targets_for_net_r(side, entry, stop)
            if targets is None:
                continue
            tp1, tp2, tp3 = targets

            future = bars_5m[
                confirmation_index + 1:
                confirmation_index + 1 + horizon_bars
            ]
            outcome = evaluate_outcome(
                side,
                entry,
                stop,
                tp1,
                tp2,
                tp3,
                future,
            )
            if outcome is None:
                continue

            setup_score = _setup_score(analysis, side)
            side_direction = _side_direction_score(analysis, side)
            score_pass = _score_gates_pass(analysis, side)
            conflict = _conflict_4h(analysis, side)
            liquidity_pass = bool(analysis.instrument.tradeable)

            base = {
                "symbol": symbol,
                "side": side,
                "opened_at": opened_at.isoformat(),
                "closed_at": outcome["closed_at"].isoformat(),
                "period": _period(opened_at, validation_start),
                "execution_assumption": _execution_assumption(side),
                "sweep_time": event.get("sweep_time"),
                "reclaim_time": event.get("reclaim_time"),
                "confirmation_time": event.get("structure_shift_time_5m"),
                "sweep_level": event.get("sweep_level"),
                "sweep_depth_atr": event.get("sweep_depth_atr"),
                "bars_from_sweep_to_confirmation": event.get("bars_from_sweep_to_confirmation"),
                "structure_15m_state": event.get("structure_15m_state"),
                "volume_ratio_5m": event.get("volume_ratio_5m"),
                "entry_price": entry,
                "stop_price": stop,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "risk_per_unit": outcome["risk_per_unit"],
                "cost_bps": BACKTEST_COST_BPS,
                "cost_r": outcome["cost_r"],
                "expansion_score": float(analysis.expansion_score),
                "direction_score": float(analysis.direction_score),
                "side_direction_score": side_direction,
                "quality_score": float(analysis.quality_score),
                "setup_score": setup_score,
                "structure_4h": str(analysis.structure_4h),
                "timeframe_conflict_4h": conflict,
                "liquidity_pass": liquidity_pass,
                "score_gates_pass": score_pass,
                "exit_reason": outcome["exit_reason"],
                "exit_price": outcome["exit_price"],
                "bars_observed": outcome["bars_observed"],
                "gross_r": outcome["gross_r"],
                "net_r": outcome["net_r"],
                "mfe_r": outcome["mfe_r"],
                "mae_r": outcome["mae_r"],
            }

            # RAW entry-ready cohort: detector only. Useful to see whether later
            # score/liquidity gates help or hurt.
            rows.append({**base, "cohort": RAW_COHORT})

            if not (liquidity_pass and score_pass):
                continue

            # Cohort B: 4H is context only.
            rows.append({**base, "cohort": COHORT_4H_CONTEXT})

            # Cohort A: current v0.7.2 hard 4H veto retained.
            if not conflict:
                rows.append({**base, "cohort": COHORT_4H_VETO})

    return rows


def apply_primary_no_overlap(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not SWEEP_PRIMARY_NO_OVERLAP:
        return [{**row, "included_primary": True, "primary_exclusion_reason": None} for row in rows]

    output: list[dict[str, Any]] = []
    last_exit: dict[tuple[str, str, str], datetime] = {}

    for row in sorted(
        rows,
        key=lambda item: (
            item["opened_at"],
            item["symbol"],
            item["side"],
            item["cohort"],
        ),
    ):
        key = (row["symbol"], row["side"], row["cohort"])
        opened = datetime.fromisoformat(row["opened_at"])
        closed = datetime.fromisoformat(row["closed_at"])
        previous_exit = last_exit.get(key)

        if previous_exit is not None and opened < previous_exit:
            output.append({
                **row,
                "included_primary": False,
                "primary_exclusion_reason": "OVERLAP_SAME_SYMBOL_SIDE_COHORT",
            })
            continue

        last_exit[key] = closed
        output.append({
            **row,
            "included_primary": True,
            "primary_exclusion_reason": None,
        })
    return output


async def run() -> dict[str, Any]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    connection = await asyncpg.connect(DATABASE_URL, timeout=30)
    try:
        job = await _load_baseline_job(connection)
    finally:
        await connection.close()

    start_at = job["start_at"]
    end_at = job["end_at"]
    warmup_start = job["warmup_start_at"]
    validation_start = max(start_at, end_at - timedelta(days=SWEEP_VALIDATION_DAYS))

    selected = _selected_universe(job)
    if not selected:
        raise RuntimeError("No symbols selected from baseline universe")

    # BTC context is required even if not included in selected output symbols.
    universe_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in (job.get("universe") or [])
    }
    btc_meta = universe_by_symbol.get("BTCUSDC")
    if btc_meta is None:
        raise RuntimeError("BTCUSDC missing from baseline universe")

    timeout = httpx.Timeout(45.0, connect=15.0)
    limits = httpx.Limits(max_connections=SWEEP_HTTP_CONCURRENCY)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        api = HistoricalBybitAPI(client)
        semaphore = asyncio.Semaphore(SWEEP_HTTP_CONCURRENCY)
        start_ms = _ms(warmup_start)
        end_ms = _ms(end_at)

        btc_bars = await _fetch_bars(api, "BTCUSDC", start_ms, end_ms, semaphore)
        if len(btc_bars) < 500:
            raise RuntimeError("Insufficient BTCUSDC history")

        all_rows: list[dict[str, Any]] = []
        symbol_status: list[dict[str, Any]] = []

        for meta in selected:
            symbol = str(meta.get("symbol") or "").upper()
            print(f"[sweep-replay] processing {symbol} ...", flush=True)
            try:
                bars = btc_bars if symbol == "BTCUSDC" else await _fetch_bars(
                    api, symbol, start_ms, end_ms, semaphore
                )
                rows = replay_symbol_sweeps(
                    symbol_meta=meta,
                    bars_5m=bars,
                    btc_bars_5m=btc_bars,
                    start_at=start_at,
                    end_at=end_at,
                    validation_start=validation_start,
                )
                all_rows.extend(rows)
                symbol_status.append({
                    "symbol": symbol,
                    "status": "OK",
                    "bars": len(bars),
                    "rows": len(rows),
                })
                print(f"[sweep-replay] {symbol} OK bars={len(bars)} rows={len(rows)}", flush=True)
            except Exception as exc:
                symbol_status.append({
                    "symbol": symbol,
                    "status": "ERROR",
                    "error": str(exc)[:500],
                })

    annotated = apply_primary_no_overlap(all_rows)
    primary = [row for row in annotated if row["included_primary"]]

    # Direct A/B delta on the primary sample.
    a_rows = [row for row in primary if row["cohort"] == COHORT_4H_VETO]
    b_rows = [row for row in primary if row["cohort"] == COHORT_4H_CONTEXT]
    a = _aggregate(a_rows)
    b = _aggregate(b_rows)

    def num(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    delta = {
        "sample_size_B_minus_A": b["sample_size"] - a["sample_size"],
        "average_net_r_B_minus_A": (
            round(num(b["average_net_r"]) - num(a["average_net_r"]), 6)
            if num(b["average_net_r"]) is not None and num(a["average_net_r"]) is not None
            else None
        ),
        "target_hit_rate_pct_B_minus_A": (
            round(num(b["target_hit_rate_pct"]) - num(a["target_hit_rate_pct"]), 3)
            if num(b["target_hit_rate_pct"]) is not None and num(a["target_hit_rate_pct"]) is not None
            else None
        ),
        "positive_net_rate_pct_B_minus_A": (
            round(num(b["positive_net_rate_pct"]) - num(a["positive_net_rate_pct"]), 3)
            if num(b["positive_net_rate_pct"]) is not None and num(a["positive_net_rate_pct"]) is not None
            else None
        ),
    }

    return {
        "script_version": SCRIPT_VERSION,
        "detector_version": RESEARCH_VERSION,
        "research_only": True,
        "baseline_job": {
            "id": int(job["id"]),
            "status": job["status"],
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "warmup_start_at": warmup_start.isoformat(),
            "completed_symbols": int(job.get("completed_symbols") or 0),
            "failed_symbols": int(job.get("failed_symbols") or 0),
            "total_signals": int(job.get("total_signals") or 0),
            "primary_signals": int(job.get("primary_signals") or 0),
        },
        "research_window": {
            "development_start": start_at.isoformat(),
            "validation_start": validation_start.isoformat(),
            "end_at": end_at.isoformat(),
            "validation_days": SWEEP_VALIDATION_DAYS,
        },
        "selected_symbols": [str(row.get("symbol")).upper() for row in selected],
        "config": asdict(DEFAULT_CONFIG),
        "cohort_definitions": {
            RAW_COHORT: "Sweep detector entry_ready only; no v0.7.2 score/liquidity gates.",
            COHORT_4H_CONTEXT: "Sweep entry_ready + historical liquidity model + v0.7.2 score gates; 4H conflict context-only.",
            COHORT_4H_VETO: "Same as SWEEP_4H_CONTEXT plus current v0.7.2 hard 4H conflict veto.",
        },
        "primary_no_overlap": SWEEP_PRIMARY_NO_OVERLAP,
        "summary_primary": _group_summary(primary),
        "summary_all_events": _group_summary(annotated),
        "ab_delta_primary": delta,
        "symbol_status": symbol_status,
        "warnings": [
            "Research only: this does not modify live v0.7.2 strategy logic.",
            "Short rows are technical research only; historical Bybit EU USDC spot-margin borrowability is unavailable.",
            "OI/funding is excluded from the replay and is not a gate.",
            "The baseline job universe has survivorship bias and historical spread is modeled.",
            "Sweep stop/invalidation is the sweep extreme; this is a research convention, not a live stop recommendation.",
            "No structural-barrier target-path gate is applied in v0.1; this phase isolates sweep entry and 4H-veto effects.",
            "Do not optimize parameters on the VALIDATION period.",
        ],
    }


def _print_compact_report(result: dict[str, Any]) -> None:
    """Railway-safe compact output.

    The full in-memory result is unchanged. We deliberately avoid pretty-printing
    the deeply nested summary because Railway rate-limits high line-rate logs.
    """
    primary = result.get("summary_primary") or {}
    by_cohort = primary.get("by_cohort") or {}
    cps = primary.get("cohort_period_side") or {}

    print(
        "[sweep-replay] META "
        + json.dumps(
            {
                "script_version": result.get("script_version"),
                "detector_version": result.get("detector_version"),
                "baseline_job": result.get("baseline_job"),
                "research_window": result.get("research_window"),
                "selected_symbols": result.get("selected_symbols"),
                "primary_no_overlap": result.get("primary_no_overlap"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )

    print(
        "[sweep-replay] SYMBOL_STATUS "
        + json.dumps(
            result.get("symbol_status") or [],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )

    for cohort in (RAW_COHORT, COHORT_4H_VETO, COHORT_4H_CONTEXT):
        print(
            f"[sweep-replay] PRIMARY_OVERALL {cohort} "
            + json.dumps(
                by_cohort.get(cohort) or {},
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
            flush=True,
        )

    for cohort in (COHORT_4H_VETO, COHORT_4H_CONTEXT):
        for period in ("DEVELOPMENT", "VALIDATION"):
            for side in ("long", "short"):
                metrics = (
                    ((cps.get(cohort) or {}).get(period) or {}).get(side)
                    or {}
                )
                print(
                    f"[sweep-replay] PRIMARY {cohort} {period} {side} "
                    + json.dumps(
                        metrics,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                    flush=True,
                )

    print(
        "[sweep-replay] AB_DELTA_PRIMARY "
        + json.dumps(
            result.get("ab_delta_primary") or {},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )

    print(
        "[sweep-replay] WARNINGS "
        + json.dumps(
            result.get("warnings") or [],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )
    print("[sweep-replay] COMPLETE", flush=True)


if __name__ == "__main__":
    result = asyncio.run(run())
    _print_compact_report(result)
