"""Core semantics for the fixed v0.7.3 structural target-path A/B/C replay.

RESEARCH ONLY. No live strategy state is mutated.
"""
from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any

from day_worker import (
    DAY_ASSUMED_ROUND_TRIP_COST_BPS,
    DAY_BARRIER_LOOKBACK_15M,
    DAY_BARRIER_MIN_PROMINENCE_ATR,
    DAY_BARRIER_PIVOT_LEFT,
    DAY_BARRIER_PIVOT_RIGHT,
    DAY_MIN_RR,
    DAY_TRIGGER_VOLUME_RATIO,
)
from structure_ab_v073 import STRUCTURE_AB_RUN_LOCK_NAME
from worker import safe_float

STRATEGY_VERSION = "0.7.3"
TARGET_PATH_AB_JOB_NAME = os.getenv(
    "V073_TARGET_PATH_AB_JOB_NAME", "v073-180d-target-path-fresh-close-ab-v1"
).strip()
DATABASE_URL = os.getenv("DATABASE_URL", "")
TARGET_PATH_AB_ENABLED = os.getenv(
    "V073_TARGET_PATH_AB_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
TARGET_PATH_AB_RUN_LOCK_NAME = STRUCTURE_AB_RUN_LOCK_NAME + ":target-path-fresh-close"

MODEL_CURRENT = "A_CURRENT"
MODEL_FRESH = "B_FRESH_15M_CLOSE"
MODEL_IGNORE = "C_IGNORE"
MODEL_NAMES = (MODEL_CURRENT, MODEL_FRESH, MODEL_IGNORE)

GO_MIN_PRIMARY = 300
GO_MIN_SIDE_PRIMARY = 100
GO_MIN_AVG_NET_R = 0.10
GO_MIN_PROFIT_FACTOR = 1.15
GO_MIN_NON_NEGATIVE_BLOCKS = 4
GO_MAX_POSITIVE_BLOCK_CONCENTRATION = 0.50

WARNINGS = [
    "Research-only target-path A/B/C replay; live v0.7.3 strategy is never changed.",
    "Exactly one promotion hypothesis is tested: ignore a confirmed 15m barrier only after a fully closed 15m candle has closed through it before the trade trigger.",
    "Wicks/touches do not consume a barrier; there is no touch-count, age, prominence, pivot, or RR parameter grid.",
    "CURRENT reproduces existing target-path semantics; IGNORE is diagnostic-only and can never auto-promote to live.",
    "All upstream/downstream v0.7.3 gates remain frozen, including sweep/reclaim/5m structure, volume 1.3x, closed non-opposing 15m structure, scores, net RR 1.8 and liquidity/execution.",
    "The fixed replay is 180 days in six chronological 30-day blocks with 20 bps costs and an 8-hour horizon.",
    "Historical short borrowability is unavailable; short results remain technical research only.",
    "Coinalyze OI/funding is excluded from replay scoring and never acts as a hard gate.",
    "Same-candle stop and TP2 is conservatively treated as stop-first.",
]

def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def fresh_nearest_structural_barrier(
    analysis: Any,
    side: str,
    entry: float,
    trigger_window_start_ms: int,
    trade_trigger_ms: int,
) -> dict[str, Any] | None:
    """Return nearest live-compatible 15m pivot not consumed by a later 15m close.

    The pivot definition intentionally mirrors day_worker.nearest_structural_barrier.
    The only added rule is freshness: after right-side confirmation and through
    the actual trade trigger, a fully closed 15m candle through the pivot consumes it.
    Pivot eligibility itself remains frozen to the live rule: fully confirmed
    before the trigger-formation window starts.
    """
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")

    bars = analysis.bars_15m[-DAY_BARRIER_LOOKBACK_15M:]
    left = DAY_BARRIER_PIVOT_LEFT
    right = DAY_BARRIER_PIVOT_RIGHT
    if len(bars) < left + right + 1:
        return None

    interval_ms = 15 * 60 * 1000
    min_prominence = max(
        float(analysis.atr_15m) * DAY_BARRIER_MIN_PROMINENCE_ATR,
        0.0,
    )
    candidates: list[dict[str, Any]] = []

    for index in range(left, len(bars) - right):
        pivot = bars[index]
        confirmation_end_ms = bars[index + right].start_ms + interval_ms
        if confirmation_end_ms > trigger_window_start_ms:
            continue

        left_rows = bars[index - left:index]
        right_rows = bars[index + 1:index + right + 1]
        if side == "long":
            left_ref = max(row.high for row in left_rows)
            right_ref = max(row.high for row in right_rows)
            is_pivot = pivot.high > left_ref and pivot.high >= right_ref
            prominence = min(pivot.high - left_ref, pivot.high - right_ref)
            price = float(pivot.high)
            swing_type = "SWING_HIGH"
            if not is_pivot or prominence < min_prominence or price <= entry:
                continue
        else:
            left_ref = min(row.low for row in left_rows)
            right_ref = min(row.low for row in right_rows)
            is_pivot = pivot.low < left_ref and pivot.low <= right_ref
            prominence = min(left_ref - pivot.low, right_ref - pivot.low)
            price = float(pivot.low)
            swing_type = "SWING_LOW"
            if not is_pivot or prominence < min_prominence or price >= entry:
                continue

        consumed_at: int | None = None
        for row in bars[index + right + 1:]:
            close_ms = row.start_ms + interval_ms
            if close_ms > trade_trigger_ms:
                break
            consumed = row.close > price if side == "long" else row.close < price
            if consumed:
                consumed_at = close_ms
                break
        if consumed_at is not None:
            continue

        candidates.append(
            {
                "price": price,
                "timeframe": "15m",
                "swing_type": swing_type,
                "pivot_start_ms": pivot.start_ms,
                "pivot_time": _iso_from_ms(pivot.start_ms),
                "confirmed_at": _iso_from_ms(confirmation_end_ms),
                "prominence": prominence,
                "prominence_atr": (
                    prominence / analysis.atr_15m
                    if analysis.atr_15m > 0
                    else None
                ),
                "search_window_start": _iso_from_ms(bars[0].start_ms),
                "search_window_end": _iso_from_ms(trigger_window_start_ms),
                "trigger_window_start": _iso_from_ms(trigger_window_start_ms),
                "freshness_window_end": _iso_from_ms(trade_trigger_ms),
                "trigger_window_excluded": True,
                "same_structure_as_trigger": False,
                "freshness_rule": "UNCONSUMED_BY_CLOSED_15M_CLOSE",
                "consumed_by_close": False,
            }
        )

    if not candidates:
        return None
    if side == "long":
        return min(candidates, key=lambda item: item["price"])
    return max(candidates, key=lambda item: item["price"])


def _apply_target_path_mode(
    candidate: dict[str, Any],
    analysis: Any,
    side: str,
    trigger_window_start_ms: int,
    trade_trigger_ms: int,
    mode: str,
) -> dict[str, Any]:
    """Change only target-path interpretation; entry/stop/targets stay frozen."""
    if mode not in MODEL_NAMES:
        raise ValueError(f"unsupported target path mode {mode!r}")

    output = copy.deepcopy(candidate)
    metrics = output.setdefault("metrics", {})
    current_barrier = metrics.get("nearest_structural_barrier")
    metrics["target_path_model"] = mode
    metrics["current_structural_barrier_control"] = current_barrier

    if mode == MODEL_CURRENT:
        return output

    entry = safe_float(output.get("entry"))
    stop = safe_float(output.get("stop"))
    targets = list(output.get("targets") or [])
    risk = abs(entry - stop)
    if entry <= 0 or risk <= 0 or len(targets) < 2:
        output["expected_rr"] = 0.0
        metrics["target_path_valid"] = False
        return output

    tp2 = safe_float(targets[1])
    assumed_cost = entry * DAY_ASSUMED_ROUND_TRIP_COST_BPS / 10_000.0
    expected_rr_without_barrier = max(
        0.0,
        (abs(tp2 - entry) - assumed_cost) / max(risk, 1e-12),
    )

    barrier_info = (
        fresh_nearest_structural_barrier(
            analysis,
            side,
            entry,
            trigger_window_start_ms,
            trade_trigger_ms,
        )
        if mode == MODEL_FRESH
        else None
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
        True
        if mode == MODEL_IGNORE
        else (
            not barrier_before_tp2
            or (
                barrier_net_rr is not None
                and barrier_net_rr + 1e-9 >= DAY_MIN_RR
            )
        )
    )

    output["expected_rr"] = float(expected_rr)
    metrics.update(
        {
            "expected_rr_without_barrier": float(expected_rr_without_barrier),
            "expected_rr_with_barrier": float(expected_rr),
            "target_path_valid": bool(target_path_valid),
            "nearest_structural_barrier": barrier_info,
            "barrier_before_tp2": bool(barrier_before_tp2),
            "barrier_net_rr": barrier_net_rr,
        }
    )
    if mode == MODEL_IGNORE:
        metrics["ignored_structural_barrier"] = current_barrier
        metrics["control_only"] = True
    return output
