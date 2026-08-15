"""Performance-only acceleration for the v0.7.3 gate diagnostic replay.

This module is intentionally isolated from live day-trade strategy code. It patches
only names owned by diagnostics_v073:
- one symbol per Railway diagnostic batch;
- an O(log n) 15m structure lookup inside a diagnostics-only sweep scanner;
- batched PostgreSQL event writes;
- process-local stage + heartbeat metadata for operational visibility.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import diagnostics_v073 as diagnostic
import sweep_research as sweep

_INSTALLED = False
_RUNTIME_LOCK = threading.Lock()
_RUNTIME: dict[str, Any] = {
    "active": False,
    "stage": "IDLE",
    "symbol": None,
    "heartbeat_at": None,
    "detail": {},
}
_HEARTBEAT_THREAD_STARTED = False

_EVENT_COLUMNS = (
    "job_id", "event_key", "strategy_version", "symbol", "side", "opened_at",
    "dataset_split", "universe_group", "execution_assumption", "borrowability_status",
    "included_primary", "primary_exclusion_reason", "candidate_built",
    "pass_tradeable", "pass_side_execution_model", "pass_no_timeframe_conflict",
    "pass_expansion", "pass_direction", "pass_quality", "pass_setup", "pass_target_path",
    "pass_rr", "pass_volume_confirmation", "pass_score_gates", "pass_strict_eligible",
    "pass_strict_trade", "near_strict", "first_failed_gate", "setup_type",
    "entry_price", "trigger_price", "stop_price", "tp1", "tp2", "tp3", "expected_rr",
    "expansion_score", "direction_score", "side_direction_score", "quality_score",
    "setup_score", "volume_ratio_5m", "turnover_24h_usdc", "modeled_spread_bps",
    "timeframe_conflict", "btc_structure_1h", "btc_structure_4h",
    "btc_volatility_regime", "base_horizon_hours", "base_cost_bps",
    "base_exit_reason", "base_gross_r", "base_net_r", "base_mfe_r", "base_mae_r",
    "sensitivity", "candidate_payload", "pass_reclaim", "pass_structure_5m",
    "pass_structure_15m", "sweep_depth_atr", "bars_from_sweep_to_confirmation",
)
_JSON_COLUMNS = {"sensitivity", "candidate_payload"}
_BULK_INSERT_CHUNK = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_runtime(
    stage: str,
    *,
    active: bool | None = None,
    symbol: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    with _RUNTIME_LOCK:
        if active is not None:
            _RUNTIME["active"] = active
        _RUNTIME["stage"] = stage
        if symbol is not None or not _RUNTIME.get("active"):
            _RUNTIME["symbol"] = symbol
        _RUNTIME["heartbeat_at"] = _utc_now()
        if detail is not None:
            _RUNTIME["detail"] = dict(detail)


def get_runtime_progress() -> dict[str, Any]:
    with _RUNTIME_LOCK:
        return {
            "active": bool(_RUNTIME["active"]),
            "stage": str(_RUNTIME["stage"]),
            "symbol": _RUNTIME.get("symbol"),
            "heartbeat_at": _RUNTIME.get("heartbeat_at"),
            "detail": dict(_RUNTIME.get("detail") or {}),
            "diagnostic_batch_symbols": 1,
            "bulk_insert_chunk": _BULK_INSERT_CHUNK,
            "scanner": "DIAGNOSTICS_ONLY_BINARY_15M_LOOKUP",
        }


def _heartbeat_loop() -> None:
    while True:
        threading.Event().wait(5.0)
        with _RUNTIME_LOCK:
            if _RUNTIME["active"]:
                _RUNTIME["heartbeat_at"] = _utc_now()


def _start_heartbeat_thread() -> None:
    global _HEARTBEAT_THREAD_STARTED
    if _HEARTBEAT_THREAD_STARTED:
        return
    thread = threading.Thread(
        target=_heartbeat_loop,
        name="v073-diagnostic-heartbeat",
        daemon=True,
    )
    thread.start()
    _HEARTBEAT_THREAD_STARTED = True


def fast_classify_15m_structure(
    bars_15m: Sequence[sweep.ResearchBar],
    confirmation_close_ms: int,
    lookback: int = 3,
) -> str:
    """Reference-equivalent 15m classifier without rebuilding the full prefix."""
    lo = 0
    hi = len(bars_15m)
    while lo < hi:
        mid = (lo + hi) // 2
        closed_at = bars_15m[mid].start_ms + sweep.FIFTEEN_MIN_MS
        if closed_at <= confirmation_close_ms:
            lo = mid + 1
        else:
            hi = mid
    end = lo
    if end < lookback + 1:
        return "INSUFFICIENT_DATA"

    current = bars_15m[end - 1]
    previous = bars_15m[end - lookback - 1:end - 1]
    prior_high = max(row.high for row in previous)
    prior_low = min(row.low for row in previous)
    if current.close > prior_high:
        return "BULLISH_SHIFT"
    if current.close < prior_low:
        return "BEARISH_SHIFT"
    return "NEUTRAL_NON_OPPOSING"


def _fast_evaluate_sweep_normalized(
    bars: Sequence[sweep.ResearchBar],
    sweep_index: int,
    side: sweep.Side,
    *,
    bars_15m: Sequence[sweep.ResearchBar] | None,
    config: sweep.SweepResearchConfig,
) -> dict[str, Any]:
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    if sweep_index < 0 or sweep_index >= len(bars):
        return sweep._empty_result(side, sweep_index, ["SWEEP_INDEX_OUT_OF_RANGE"])

    required_history = max(
        config.liquidity_lookback,
        config.structure_lookback_5m,
        config.atr_period,
    )
    if sweep_index < required_history:
        return sweep._empty_result(side, sweep_index, ["INSUFFICIENT_5M_HISTORY"])

    atr_value = sweep.atr_at_index(bars, sweep_index, config.atr_period)
    if atr_value is None or atr_value <= 0:
        return sweep._empty_result(side, sweep_index, ["ATR_UNAVAILABLE"])

    sweep_bar = bars[sweep_index]
    liquidity_window = bars[sweep_index - config.liquidity_lookback:sweep_index]
    structure_window = bars[sweep_index - config.structure_lookback_5m:sweep_index]
    if side == "long":
        sweep_level = min(row.low for row in liquidity_window)
        sweep_price = sweep_bar.low
        sweep_depth = sweep_level - sweep_price
        structure_level = max(row.high for row in structure_window)
        raw_sweep = sweep_price < sweep_level
    else:
        sweep_level = max(row.high for row in liquidity_window)
        sweep_price = sweep_bar.high
        sweep_depth = sweep_price - sweep_level
        structure_level = min(row.low for row in structure_window)
        raw_sweep = sweep_price > sweep_level

    if not raw_sweep:
        return sweep._empty_result(side, sweep_index, ["NO_LIQUIDITY_SWEEP"])

    sweep_depth_atr = sweep_depth / atr_value
    if sweep_depth_atr < config.min_sweep_depth_atr:
        result = sweep._empty_result(side, sweep_index, ["SWEEP_TOO_SHALLOW"])
        result.update({
            "sweep_level": sweep_level,
            "sweep_price": sweep_price,
            "sweep_depth": sweep_depth,
            "sweep_depth_atr": sweep_depth_atr,
            "sweep_time": sweep.iso_from_ms(sweep_bar.start_ms),
        })
        return result
    if sweep_depth_atr > config.max_sweep_depth_atr:
        result = sweep._empty_result(side, sweep_index, ["SWEEP_TOO_DEEP"])
        result.update({
            "sweep_level": sweep_level,
            "sweep_price": sweep_price,
            "sweep_depth": sweep_depth,
            "sweep_depth_atr": sweep_depth_atr,
            "sweep_time": sweep.iso_from_ms(sweep_bar.start_ms),
        })
        return result

    result = sweep._empty_result(side, sweep_index, [])
    result.update({
        "sweep_detected": True,
        "sweep_level": sweep_level,
        "sweep_price": sweep_price,
        "sweep_depth": sweep_depth,
        "sweep_depth_atr": sweep_depth_atr,
        "sweep_time": sweep.iso_from_ms(sweep_bar.start_ms),
        "structure_shift_level_5m": structure_level,
        "candidate_invalidation": sweep_price,
    })

    reclaim_end = min(len(bars) - 1, sweep_index + config.reclaim_window_bars)
    reclaim_index: int | None = None
    for index in range(sweep_index, reclaim_end + 1):
        close = bars[index].close
        reclaimed = close > sweep_level if side == "long" else close < sweep_level
        if reclaimed:
            reclaim_index = index
            break
    if reclaim_index is None:
        result["failure_reasons"].append("NO_RECLAIM_WITHIN_WINDOW")
        return result

    reclaim_bar = bars[reclaim_index]
    result["reclaim_confirmed"] = True
    result["reclaim_close"] = reclaim_bar.close
    result["reclaim_time"] = sweep.iso_from_ms(reclaim_bar.start_ms)

    confirmation_end = min(len(bars) - 1, sweep_index + config.max_confirmation_bars)
    confirmation_index: int | None = None
    for index in range(reclaim_index, confirmation_end + 1):
        close = bars[index].close
        shifted = close > structure_level if side == "long" else close < structure_level
        if shifted:
            confirmation_index = index
            break
    if confirmation_index is None:
        result["failure_reasons"].append("NO_5M_STRUCTURE_SHIFT")
        return result

    confirmation_bar = bars[confirmation_index]
    result["structure_shift_5m"] = True
    result["structure_shift_time_5m"] = sweep.iso_from_ms(confirmation_bar.start_ms)
    result["bars_from_sweep_to_confirmation"] = confirmation_index - sweep_index
    result["candidate_entry"] = confirmation_bar.close

    volume_ratio = sweep.volume_ratio_at_index(
        bars, confirmation_index, config.volume_lookback
    )
    result["volume_ratio_5m"] = volume_ratio
    result["volume_confirmed"] = (
        volume_ratio is not None and volume_ratio >= config.volume_confirmation_ratio
    )
    if not result["volume_confirmed"]:
        result["failure_reasons"].append("VOLUME_NOT_CONFIRMED")

    if bars_15m is None:
        fifteen = sweep.aggregate_5m_to_15m(bars[:confirmation_index + 1])
    else:
        fifteen = bars_15m
    confirmation_close_ms = confirmation_bar.start_ms + sweep.FIVE_MIN_MS
    state_15m = fast_classify_15m_structure(
        fifteen, confirmation_close_ms, config.structure_lookback_15m
    )
    result["structure_15m_state"] = state_15m
    result["structure_confirmed_15m"] = sweep._15m_confirms(side, state_15m)
    if not result["structure_confirmed_15m"]:
        result["failure_reasons"].append("15M_STRUCTURE_OPPOSES_OR_UNAVAILABLE")

    result["entry_ready"] = bool(
        result["sweep_detected"]
        and result["reclaim_confirmed"]
        and result["structure_shift_5m"]
        and result["structure_confirmed_15m"]
        and result["volume_confirmed"]
    )
    return result


def fast_scan_sweep_setups(
    bars_5m: Iterable[Any],
    side: sweep.Side,
    *,
    bars_15m: Iterable[Any] | None = None,
    config: sweep.SweepResearchConfig = sweep.DEFAULT_CONFIG,
    include_incomplete: bool = True,
) -> list[dict[str, Any]]:
    """Diagnostics-only historical scanner with O(log n) 15m lookups."""
    bars = sweep.normalize_bars(bars_5m)
    fifteen = sweep.normalize_bars(bars_15m) if bars_15m is not None else None
    start = max(
        config.liquidity_lookback,
        config.structure_lookback_5m,
        config.atr_period,
    )
    output: list[dict[str, Any]] = []
    for index in range(start, len(bars)):
        event = _fast_evaluate_sweep_normalized(
            bars,
            index,
            side,
            bars_15m=fifteen,
            config=config,
        )
        if not event["sweep_detected"]:
            continue
        if include_incomplete or event["entry_ready"]:
            output.append(event)
    return output


def _event_record(item: dict[str, Any]) -> tuple[Any, ...]:
    values: list[Any] = []
    for column in _EVENT_COLUMNS:
        value = item[column]
        if column in _JSON_COLUMNS:
            value = json.dumps(value, default=str)
        values.append(value)
    return tuple(values)


async def bulk_insert_events(connection: Any, events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    first = events[0]
    job_id = int(first["job_id"])
    symbol = str(first["symbol"])
    before = int(
        await connection.fetchval(
            "SELECT COUNT(*) FROM day_trade_diagnostic_events WHERE job_id=$1 AND symbol=$2",
            job_id,
            symbol,
        )
        or 0
    )
    placeholders = []
    for index, column in enumerate(_EVENT_COLUMNS, start=1):
        suffix = "::jsonb" if column in _JSON_COLUMNS else ""
        placeholders.append(f"${index}{suffix}")
    sql = (
        "INSERT INTO day_trade_diagnostic_events ("
        + ",".join(_EVENT_COLUMNS)
        + ") VALUES ("
        + ",".join(placeholders)
        + ") ON CONFLICT (event_key) DO NOTHING"
    )
    _set_runtime(
        "INSERTING_EVENTS",
        symbol=symbol,
        detail={"event_count": len(events), "chunk_size": _BULK_INSERT_CHUNK},
    )
    records = [_event_record(item) for item in events]
    for start in range(0, len(records), _BULK_INSERT_CHUNK):
        await connection.executemany(sql, records[start:start + _BULK_INSERT_CHUNK])
        _set_runtime(
            "INSERTING_EVENTS",
            symbol=symbol,
            detail={
                "event_count": len(events),
                "inserted_through": min(start + _BULK_INSERT_CHUNK, len(records)),
            },
        )
    after = int(
        await connection.fetchval(
            "SELECT COUNT(*) FROM day_trade_diagnostic_events WHERE job_id=$1 AND symbol=$2",
            job_id,
            symbol,
        )
        or 0
    )
    _set_runtime("FINALIZING_SYMBOL", symbol=symbol, detail={"stored_events": after})
    return max(after - before, 0)


def install_performance_patch() -> None:
    """Install diagnostics-only runtime patches. Idempotent by design."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_claim = diagnostic.claim_symbols
    original_replay = diagnostic.replay_diagnostic_symbol
    original_update_counts = diagnostic.update_job_counts
    original_run = diagnostic.run_diagnostic_batch

    async def claim_one(connection: Any, job_id: int) -> list[dict[str, Any]]:
        diagnostic.DIAGNOSTIC_BATCH_SYMBOLS = 1
        rows = await original_claim(connection, job_id)
        if rows:
            symbol = str(rows[0]["symbol"])
            _set_runtime("FETCHING_HISTORY", active=True, symbol=symbol)
        return rows[:1]

    def replay_with_progress(*args: Any, **kwargs: Any) -> Any:
        symbol_meta = args[1] if len(args) > 1 else kwargs.get("symbol_meta") or {}
        symbol = str(symbol_meta.get("symbol") or get_runtime_progress().get("symbol") or "")
        _set_runtime("SWEEP_SCAN_AND_CANDIDATE_EVAL", active=True, symbol=symbol)
        result = original_replay(*args, **kwargs)
        _set_runtime(
            "INSERTING_EVENTS",
            active=True,
            symbol=symbol,
            detail={"events": len(result.events), "evaluation_bars": result.evaluation_bars},
        )
        return result

    async def update_counts_with_progress(connection: Any, job_id: int) -> dict[str, Any]:
        _set_runtime("BATCH_FINALIZING", active=True)
        result = await original_update_counts(connection, job_id)
        return result

    async def run_with_progress() -> dict[str, Any]:
        diagnostic.DIAGNOSTIC_BATCH_SYMBOLS = 1
        _set_runtime("BATCH_START", active=True, symbol=None, detail={})
        try:
            result = await original_run()
        except Exception as exc:
            _set_runtime(
                "FAILED",
                active=False,
                symbol=None,
                detail={"error": f"{type(exc).__name__}: {exc}"[:500]},
            )
            raise
        status = str(result.get("status") or "BATCH_COMPLETE")
        _set_runtime(
            status if status in {"COMPLETED", "PARTIAL", "FAILED"} else "BATCH_COMPLETE",
            active=False,
            symbol=None,
            detail={
                "completed": result.get("completed"),
                "failed": result.get("failed"),
                "pending": result.get("pending"),
                "total_events": result.get("total_events"),
            },
        )
        return result

    diagnostic.DIAGNOSTIC_BATCH_SYMBOLS = 1
    diagnostic.scan_sweep_setups = fast_scan_sweep_setups
    diagnostic.insert_events = bulk_insert_events
    diagnostic.claim_symbols = claim_one
    diagnostic.replay_diagnostic_symbol = replay_with_progress
    diagnostic.update_job_counts = update_counts_with_progress
    diagnostic.run_diagnostic_batch = run_with_progress
    _start_heartbeat_thread()
    _INSTALLED = True


__all__ = [
    "bulk_insert_events",
    "fast_classify_15m_structure",
    "fast_scan_sweep_setups",
    "get_runtime_progress",
    "install_performance_patch",
]
