"""Single-pass replay engine for target-path CURRENT/FRESH/IGNORE research."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from backtest import _ms
from diagnostics_v073 import (
    DIAGNOSTIC_BASE_COST_BPS,
    DIAGNOSTIC_BASE_HORIZON_HOURS,
    DIAGNOSTIC_SHORT_MODE,
    build_research_candidate,
    evaluate_path,
    gate_snapshot,
)
from diagnostics_v073_perf import fast_scan_sweep_setups
from structure_ab_v073 import (
    STRUCTURE_AB_BLOCK_DAYS,
    STRUCTURE_AB_LOOKBACK_DAYS,
    _build_analysis_cache,
    _count_event,
    _empty_counter,
    _parse_iso_ms,
)
from sweep_research import SweepResearchConfig, normalize_bars
from target_path_ab_core_v073 import (
    DAY_TRIGGER_VOLUME_RATIO,
    MODEL_CURRENT,
    MODEL_FRESH,
    MODEL_IGNORE,
    MODEL_NAMES,
    _apply_target_path_mode,
)
from worker import Bar, safe_float

def replay_symbol(
    symbol_meta: dict[str, Any],
    bars_5m: list[Bar],
    btc_bars_5m: list[Bar],
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    """Replay CURRENT, FRESH and IGNORE target-path models for one symbol."""
    symbol = str(symbol_meta["symbol"]).upper()
    if len(bars_5m) < 500 or len(btc_bars_5m) < 500:
        return {
            "symbol": symbol,
            "bars_fetched": len(bars_5m),
            "models": {
                name: {"counters": _empty_counter(), "trades": []}
                for name in MODEL_NAMES
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
        name: {"counters": _empty_counter(), "trades": []}
        for name in MODEL_NAMES
    }
    last_primary_exit = {
        (name, side): 0 for name in MODEL_NAMES for side in ("long", "short")
    }

    for event in raw_events:
        sweep_ms = _parse_iso_ms(event.get("sweep_time"))
        if sweep_ms is None or sweep_ms < start_ms or sweep_ms >= end_ms:
            continue
        for name in MODEL_NAMES:
            _count_event(models[name]["counters"], event)

        structure_ms = _parse_iso_ms(event.get("structure_shift_time_5m"))
        if structure_ms is None:
            continue
        confirm_index = bar_index.get(structure_ms)
        if confirm_index is None:
            continue
        opened_ms = structure_ms + 5 * 60 * 1000
        if opened_ms < start_ms or opened_ms >= end_ms:
            continue

        cached = analysis_for(confirm_index)
        if cached is None:
            continue
        base_analysis, _, _ = cached
        analysis = copy.copy(base_analysis)
        side = str(event["side"])
        if side == "long":
            analysis.shortable = False
        elif DIAGNOSTIC_SHORT_MODE == "technical_only":
            analysis.shortable = True
        elif DIAGNOSTIC_SHORT_MODE == "current_proxy":
            analysis.shortable = current_shortable_proxy
        else:
            analysis.shortable = False

        base_candidate = build_research_candidate(analysis, side, event)
        if base_candidate is None:
            continue
        for name in MODEL_NAMES:
            models[name]["counters"]["candidates"] += 1

        candidates = {
            name: _apply_target_path_mode(
                base_candidate,
                analysis,
                side,
                sweep_ms,
                opened_ms,
                name,
            )
            for name in MODEL_NAMES
        }
        gates_by_model = {
            name: gate_snapshot(
                candidates[name],
                side,
                event,
                current_shortable_proxy,
            )
            for name in MODEL_NAMES
        }
        for name, gates in gates_by_model.items():
            if gates.get("pass_strict_trade"):
                models[name]["counters"]["strict_trade_raw"] += 1

        if confirm_index + horizon_bars >= len(bars_5m):
            continue
        targets = list(base_candidate.get("targets") or [])
        if len(targets) < 3:
            continue
        entry = safe_float(base_candidate.get("entry"))
        stop = safe_float(base_candidate.get("stop"))
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

        for name in MODEL_NAMES:
            key = (name, side)
            included_primary = opened_ms >= last_primary_exit[key]
            if included_primary:
                last_primary_exit[key] = closed_ms
            if not (included_primary and gates_by_model[name].get("pass_strict_trade")):
                continue

            metrics = candidates[name].get("metrics") or {}
            models[name]["trades"].append(
                {
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
                    "target_path_model": name,
                    "barrier_before_tp2": bool(metrics.get("barrier_before_tp2")),
                    "barrier": metrics.get("nearest_structural_barrier"),
                }
            )
            models[name]["counters"]["primary_strict_trades"] += 1

    return {"symbol": symbol, "bars_fetched": len(bars_5m), "models": models}
