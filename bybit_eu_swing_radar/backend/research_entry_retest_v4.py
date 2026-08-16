"""Research-only entry-architecture pivot for v0.7.3 liquidity-sweep opportunities.

Tests whether the confirmation-close entry is structurally too late by replaying
three preregistered pullback/retest entries after the 5m structure shift. No live
strategy/scoring/execution code is changed. Candidate selection uses the first
90d of DEVELOPMENT; the following 30d is an internal holdout. The historical
60d VALIDATION split is already inspected and is reference-only.
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
from datetime import datetime, timedelta
from typing import Any

from day_worker import DAY_MIN_RR
from diagnostics_v073 import DIAGNOSTIC_BASE_COST_BPS, DIAGNOSTIC_BASE_HORIZON_HOURS, evaluate_path
from worker import Bar

ANALYSIS_VERSION = "day-trade-entry-retest-v4"
TRAIN_DAYS = 90
INTERNAL_HOLDOUT_DAYS = 30
RETEST_BARS = 3
MIN_TRAIN_N = 150
MIN_HOLDOUT_N = 50
MIN_TRAIN_FILL_RATE_PCT = 8.0


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _stats(values: list[float]) -> dict[str, Any]:
    clean = [float(v) for v in values if math.isfinite(float(v))]
    gains = sum(v for v in clean if v > 0)
    losses = abs(sum(v for v in clean if v < 0))
    return {
        "n": len(clean),
        "average_net_r": round(statistics.fmean(clean), 6) if clean else None,
        "median_net_r": round(statistics.median(clean), 6) if clean else None,
        "positive_rate_pct": round(sum(v > 0 for v in clean) / len(clean) * 100.0, 3) if clean else None,
        "profit_factor": round(gains / losses, 6) if losses > 0 else None,
        "total_net_r": round(sum(clean), 6),
    }


def _candidate_levels(row: dict[str, Any]) -> dict[str, float]:
    payload = _payload(row.get("candidate_payload"))
    sweep = _payload(payload.get("sweep_event"))
    confirmation = _number(row.get("entry_price"))
    structure = _number(sweep.get("structure_shift_level_5m"))
    reclaim = _number(sweep.get("sweep_level"))
    levels: dict[str, float] = {}
    if confirmation is not None and structure is not None:
        levels["structure_break_retest"] = structure
        levels["half_retrace_to_break"] = (confirmation + structure) / 2.0
    if reclaim is not None:
        levels["sweep_level_retest"] = reclaim
    return levels


def _targets(side: str, entry: float, stop: float) -> tuple[float, float, float] | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    cost = entry * DIAGNOSTIC_BASE_COST_BPS / 10_000.0
    direction = 1.0 if side == "long" else -1.0
    def target(net_r: float) -> float:
        return entry + direction * (net_r * risk + cost)
    return target(1.0), target(DAY_MIN_RR), target(2.5)


def replay_entry_variant(
    row: dict[str, Any],
    bars: list[Bar],
    starts: list[int],
    *,
    variant: str,
) -> dict[str, Any] | None:
    opened = _parse_time(row.get("opened_at"))
    side = str(row.get("side") or "").lower()
    stop = _number(row.get("stop_price"))
    if opened is None or side not in {"long", "short"} or stop is None:
        return None
    level = _candidate_levels(row).get(variant)
    if level is None or level <= 0:
        return None
    if (side == "long" and stop >= level) or (side == "short" and stop <= level):
        return None

    first_start = int(opened.timestamp() * 1000)
    first = bisect.bisect_left(starts, first_start)
    if first >= len(bars):
        return None
    fill_index: int | None = None
    for index in range(first, min(len(bars), first + RETEST_BARS)):
        bar = bars[index]
        if bar.low <= level <= bar.high:
            fill_index = index
            break
    if fill_index is None:
        return {
            "filled": False,
            "variant": variant,
            "entry": level,
            "net_r": None,
            "fill_delay_bars": None,
        }

    targets = _targets(side, level, stop)
    if targets is None:
        return None
    horizon = DIAGNOSTIC_BASE_HORIZON_HOURS * 12
    future = bars[fill_index : fill_index + horizon]
    if not future:
        return None
    path = evaluate_path(side, level, stop, targets[0], targets[1], targets[2], future)
    if path is None:
        return None
    risk = abs(level - stop)
    cost_r = (level * DIAGNOSTIC_BASE_COST_BPS / 10_000.0) / risk
    net_r = float(path["gross_r"]) - cost_r
    return {
        "filled": True,
        "variant": variant,
        "entry": level,
        "net_r": round(net_r, 6),
        "gross_r": path["gross_r"],
        "exit_reason": path["exit_reason"],
        "fill_delay_bars": fill_index - first,
    }


def _variant_summary(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    attempted = [r for r in rows if variant in (r.get("entry_retests") or {})]
    results = [(r.get("entry_retests") or {}).get(variant) or {} for r in attempted]
    filled = [item for item in results if item.get("filled") and _number(item.get("net_r")) is not None]
    net = [float(item["net_r"]) for item in filled]
    return {
        "attempted_n": len(attempted),
        "filled_n": len(filled),
        "fill_rate_pct": round(len(filled) / len(attempted) * 100.0, 3) if attempted else None,
        **_stats(net),
    }


def build_entry_retest_report(
    rows: list[dict[str, Any]], *, start_at: datetime, development_end_at: datetime
) -> dict[str, Any]:
    train_end = start_at + timedelta(days=TRAIN_DAYS)
    holdout_end = min(development_end_at, train_end + timedelta(days=INTERNAL_HOLDOUT_DAYS))
    train: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    reused_validation: list[dict[str, Any]] = []
    for row in rows:
        opened = _parse_time(row.get("opened_at"))
        if opened is None:
            continue
        if start_at <= opened < train_end:
            train.append(row)
        elif train_end <= opened < holdout_end:
            holdout.append(row)
        elif row.get("dataset_split") == "VALIDATION":
            reused_validation.append(row)

    variants = ("structure_break_retest", "half_retrace_to_break", "sweep_level_retest")
    candidates: list[dict[str, Any]] = []
    for variant in variants:
        result = _variant_summary(train, variant)
        eligible = (
            int(result["n"]) >= MIN_TRAIN_N
            and result["fill_rate_pct"] is not None
            and float(result["fill_rate_pct"]) >= MIN_TRAIN_FILL_RATE_PCT
        )
        candidates.append({"name": variant, "eligible_for_train_selection": eligible, "train": result})

    eligible = [item for item in candidates if item["eligible_for_train_selection"] and item["train"]["average_net_r"] is not None]
    winner = max(eligible, key=lambda item: float(item["train"]["average_net_r"]), default=None)
    winner_name = None if winner is None else str(winner["name"])
    holdout_result = _variant_summary(holdout, winner_name) if winner_name else None
    external_result = _variant_summary(reused_validation, winner_name) if winner_name else None
    baseline_train = _stats([float(v) for row in train if (v := _number(row.get("base_net_r"))) is not None])
    baseline_holdout = _stats([float(v) for row in holdout if (v := _number(row.get("base_net_r"))) is not None])

    train_pass = False
    holdout_pass = False
    if winner is not None:
        tr = winner["train"]
        train_pass = bool(tr["average_net_r"] is not None and tr["average_net_r"] > 0 and tr["profit_factor"] is not None and tr["profit_factor"] > 1.0)
    if holdout_result is not None:
        holdout_pass = bool(
            int(holdout_result["n"]) >= MIN_HOLDOUT_N
            and holdout_result["average_net_r"] is not None and holdout_result["average_net_r"] > 0
            and holdout_result["profit_factor"] is not None and holdout_result["profit_factor"] > 1.0
        )
    edge_pass = train_pass and holdout_pass
    return {
        "analysis_version": ANALYSIS_VERSION,
        "status": "OK" if train and holdout else "INSUFFICIENT_SPLIT_DATA",
        "research_only": True,
        "promotion_allowed": False,
        "split_policy": {
            "train_days": TRAIN_DAYS,
            "internal_holdout_days": INTERNAL_HOLDOUT_DAYS,
            "historical_validation_status": "REUSED_REFERENCE_NOT_UNTOUCHED_OOS",
            "winner_selected_on": "FIRST_90D_DEVELOPMENT_ONLY",
            "retest_window_bars": RETEST_BARS,
            "validation_threshold_search": False,
        },
        "baseline_confirmation_close": {"train": baseline_train, "internal_holdout": baseline_holdout},
        "candidate_train_results": candidates,
        "selected_on_train": winner_name,
        "internal_holdout_result": holdout_result,
        "reused_external_validation_reference": external_result,
        "train_edge_pass": train_pass,
        "internal_holdout_edge_pass": holdout_pass,
        "entry_architecture_edge_pass": edge_pass,
        "next_step": (
            "Freeze the retest entry and collect genuinely fresh forward OOS before any promotion."
            if edge_pass
            else "Retest-entry architecture did not produce positive train+holdout expectancy. Reject this v0.7.3 sweep family and pivot strategy family rather than tuning thresholds."
        ),
        "warnings": [
            "Research-only entry replay; live day_worker strategy/scoring/execution is unchanged.",
            "Retest orders are assumed placed only after the 5m confirmation bar closes; the confirmation bar itself cannot fill them.",
            "Same-bar fill/stop ambiguity is conservative because path evaluation checks stop before target.",
            "Historical shorts are technical-only and do not establish Bybit EU spot-margin borrowability.",
            "The original 60d validation has already been inspected and cannot authorize promotion.",
        ],
    }
