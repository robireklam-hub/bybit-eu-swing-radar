"""Pre-registered reporting and GO/NO-GO contract for target-path research."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from structure_ab_v073 import (
    STRUCTURE_AB_BLOCK_DAYS,
    STRUCTURE_AB_LOOKBACK_DAYS,
    _empty_counter,
    aggregate_trades,
)
from target_path_ab_core_v073 import (
    GO_MAX_POSITIVE_BLOCK_CONCENTRATION,
    GO_MIN_AVG_NET_R,
    GO_MIN_NON_NEGATIVE_BLOCKS,
    GO_MIN_PRIMARY,
    GO_MIN_PROFIT_FACTOR,
    GO_MIN_SIDE_PRIMARY,
    MODEL_CURRENT,
    MODEL_FRESH,
    MODEL_IGNORE,
    MODEL_NAMES,
    STRATEGY_VERSION,
)

def _model_report(
    symbol_results: list[dict[str, Any]],
    model_name: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    counters = _empty_counter()
    for item in symbol_results:
        model = ((item.get("models") or {}).get(model_name) or {})
        trades.extend(list(model.get("trades") or []))
        for key, value in (model.get("counters") or {}).items():
            if key in counters:
                counters[key] += int(value or 0)

    by_side = {
        side: aggregate_trades(row for row in trades if row.get("side") == side)
        for side in ("long", "short")
    }
    blocks: list[dict[str, Any]] = []
    block_count = STRUCTURE_AB_LOOKBACK_DAYS // STRUCTURE_AB_BLOCK_DAYS
    for index in range(block_count):
        block_start = start_at + timedelta(days=index * STRUCTURE_AB_BLOCK_DAYS)
        block_end = min(end_at, block_start + timedelta(days=STRUCTURE_AB_BLOCK_DAYS))
        blocks.append(
            {
                "index": index + 1,
                "start_at": block_start.isoformat(),
                "end_at": block_end.isoformat(),
                **aggregate_trades(
                    row for row in trades if int(row.get("block_index", -1)) == index
                ),
            }
        )
    positive_totals = [
        float(block["total_net_r"])
        for block in blocks
        if float(block["total_net_r"]) > 0
    ]
    positive_total = sum(positive_totals)
    concentration = max(positive_totals) / positive_total if positive_total > 0 else None
    non_negative_blocks = sum(
        1
        for block in blocks
        if int(block["sample_size"]) > 0 and float(block["total_net_r"]) >= 0
    )
    return {
        "counters": counters,
        "overall": aggregate_trades(trades),
        "by_side": by_side,
        "blocks_30d": blocks,
        "non_negative_blocks": non_negative_blocks,
        "positive_block_concentration": (
            round(concentration, 6) if concentration is not None else None
        ),
    }


def _pf_numeric(stats: dict[str, Any]) -> float | None:
    if stats.get("profit_factor_unbounded"):
        return float("inf")
    value = stats.get("profit_factor")
    return None if value is None else float(value)


def _delta(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    c = candidate["overall"]
    b = baseline["overall"]
    c_pf, b_pf = _pf_numeric(c), _pf_numeric(b)
    return {
        "primary_sample": int(c["sample_size"]) - int(b["sample_size"]),
        "average_net_r": (
            None
            if c.get("average_net_r") is None or b.get("average_net_r") is None
            else round(float(c["average_net_r"]) - float(b["average_net_r"]), 6)
        ),
        "profit_factor": (
            None
            if c_pf is None or b_pf is None or c_pf == float("inf") or b_pf == float("inf")
            else round(c_pf - b_pf, 6)
        ),
    }


def build_report_from_symbol_results(
    symbol_results: list[dict[str, Any]],
    start_at: datetime,
    end_at: datetime,
    *,
    expected_symbols: int | None = None,
) -> dict[str, Any]:
    models = {
        name: _model_report(symbol_results, name, start_at, end_at)
        for name in MODEL_NAMES
    }
    current = models[MODEL_CURRENT]
    fresh = models[MODEL_FRESH]
    ignore = models[MODEL_IGNORE]
    overall = fresh["overall"]
    long_metrics = fresh["by_side"]["long"]
    short_metrics = fresh["by_side"]["short"]
    pf = _pf_numeric(overall)
    current_pf = _pf_numeric(current["overall"])
    concentration = fresh.get("positive_block_concentration")
    current_avg = current["overall"].get("average_net_r")
    fresh_avg = overall.get("average_net_r")

    checks = {
        "all_symbols_completed": (
            True if expected_symbols is None else len(symbol_results) == expected_symbols
        ),
        "primary_sample_gte_300": int(overall["sample_size"]) >= GO_MIN_PRIMARY,
        "long_sample_gte_100": int(long_metrics["sample_size"]) >= GO_MIN_SIDE_PRIMARY,
        "short_sample_gte_100": int(short_metrics["sample_size"]) >= GO_MIN_SIDE_PRIMARY,
        "average_net_r_gt_0_10": fresh_avg is not None and float(fresh_avg) > GO_MIN_AVG_NET_R,
        "profit_factor_gte_1_15": pf is not None and pf >= GO_MIN_PROFIT_FACTOR,
        "non_negative_blocks_gte_4_of_6": int(fresh["non_negative_blocks"]) >= GO_MIN_NON_NEGATIVE_BLOCKS,
        "positive_block_concentration_lte_0_50": concentration is not None and float(concentration) <= GO_MAX_POSITIVE_BLOCK_CONCENTRATION,
        "fresh_average_net_r_gt_current": (
            fresh_avg is not None
            and current_avg is not None
            and float(fresh_avg) > float(current_avg)
        ),
        "fresh_profit_factor_gte_current": (
            pf is not None and current_pf is not None and pf >= current_pf
        ),
    }
    decision = "GO" if all(checks.values()) else "NO_GO"

    return {
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "live_strategy_mutated": False,
        "hypothesis": (
            "A confirmed 15m structural barrier should remain a hard target-path "
            "constraint only while no later fully closed 15m candle has closed "
            "through that level before the actual 5m trade trigger."
        ),
        "window": {
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "lookback_days": STRUCTURE_AB_LOOKBACK_DAYS,
            "block_days": STRUCTURE_AB_BLOCK_DAYS,
            "blocks": STRUCTURE_AB_LOOKBACK_DAYS // STRUCTURE_AB_BLOCK_DAYS,
        },
        "models": models,
        "deltas": {
            "fresh_vs_current": _delta(fresh, current),
            "ignore_vs_current": _delta(ignore, current),
            "ignore_vs_fresh": _delta(ignore, fresh),
        },
        "go_criteria": {
            "fixed_before_run": True,
            "promotion_candidate": MODEL_FRESH,
            "diagnostic_control": MODEL_IGNORE,
            "checks": checks,
            "decision": decision,
            "thresholds": {
                "primary_sample": GO_MIN_PRIMARY,
                "min_each_side": GO_MIN_SIDE_PRIMARY,
                "average_net_r_strictly_greater_than": GO_MIN_AVG_NET_R,
                "profit_factor_min": GO_MIN_PROFIT_FACTOR,
                "non_negative_blocks_min": GO_MIN_NON_NEGATIVE_BLOCKS,
                "positive_block_concentration_max": GO_MAX_POSITIVE_BLOCK_CONCENTRATION,
                "must_beat_current_average_net_r": True,
                "must_not_reduce_current_profit_factor": True,
            },
        },
        "next_action": (
            "If GO: review one isolated v0.7.4 shadow/live proposal for the fresh-close rule. "
            "C_IGNORE is diagnostic-only and never auto-promotes. If NO_GO: leave live v0.7.3 unchanged and interpret CURRENT/FRESH/IGNORE before selecting any further target-path hypothesis."
        ),
    }
