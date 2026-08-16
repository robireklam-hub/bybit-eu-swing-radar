"""Preregistered interaction screening for the materialized v0.7.3 research dataset.

Research only. Candidate selection uses DEVELOPMENT rows only. VALIDATION is
reported only after the discovery-selected rule is frozen. No live strategy
logic, eligibility, scoring, execution, or shortability is changed here.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Any, Callable

ANALYSIS_VERSION = "day-trade-interactions-v1"
MIN_DISCOVERY_N = 150
MIN_VALIDATION_N = 50


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _quantile(values: list[float], probability: float) -> float:
    rows = sorted(values)
    if not rows:
        raise ValueError("quantile requires values")
    if len(rows) == 1:
        return rows[0]
    position = (len(rows) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return rows[lower]
    weight = position - lower
    return rows[lower] * (1.0 - weight) + rows[upper] * weight


def _stats(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    gains = sum(value for value in clean if value > 0)
    losses = abs(sum(value for value in clean if value < 0))
    return {
        "n": len(clean),
        "average_net_r": round(statistics.fmean(clean), 6) if clean else None,
        "median_net_r": round(statistics.median(clean), 6) if clean else None,
        "positive_rate_pct": round(sum(value > 0 for value in clean) / len(clean) * 100, 3) if clean else None,
        "profit_factor": round(gains / losses, 6) if losses > 0 else None,
        "total_net_r": round(sum(clean), 6),
    }


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _evaluate(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    selected = []
    complement = []
    for row in rows:
        net = _number(row.get("base_net_r"))
        if net is None:
            continue
        (selected if predicate(row) else complement).append(net)
    selected_stats = _stats(selected)
    complement_stats = _stats(complement)
    selected_avg = selected_stats.get("average_net_r")
    complement_avg = complement_stats.get("average_net_r")
    uplift = None
    if selected_avg is not None and complement_avg is not None:
        uplift = round(float(selected_avg) - float(complement_avg), 6)
    return {
        "selected": selected_stats,
        "complement": complement_stats,
        "average_net_r_uplift_vs_complement": uplift,
    }


def _block_stats(
    rows: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    start_at: datetime,
    block_count: int,
) -> list[dict[str, Any]]:
    output = []
    for index in range(block_count):
        block_start = start_at + timedelta(days=30 * index)
        block_end = block_start + timedelta(days=30)
        block_rows = [
            row
            for row in rows
            if (opened := _parse_time(row.get("opened_at"))) is not None
            and block_start <= opened < block_end
        ]
        result = _evaluate(block_rows, predicate)
        output.append(
            {
                "block": index + 1,
                "start_at": block_start.isoformat(),
                "end_at": block_end.isoformat(),
                **result,
            }
        )
    return output


def build_interaction_report(
    rows: list[dict[str, Any]],
    *,
    start_at: datetime,
    development_end_at: datetime,
) -> dict[str, Any]:
    """Screen four fixed mechanistic interactions without validation threshold search."""
    evaluable = [row for row in rows if _number(row.get("base_net_r")) is not None]
    discovery = [row for row in evaluable if row.get("dataset_split") == "DEVELOPMENT"]
    validation = [row for row in evaluable if row.get("dataset_split") == "VALIDATION"]

    discovery_expansion = [
        value
        for row in discovery
        if (value := _number(row.get("expansion_score"))) is not None
    ]
    if len(discovery_expansion) < 40:
        return {
            "analysis_version": ANALYSIS_VERSION,
            "status": "INSUFFICIENT_DISCOVERY_SAMPLE",
            "research_only": True,
            "promotion_allowed": False,
            "n": len(discovery_expansion),
        }

    expansion_q75 = _quantile(discovery_expansion, 0.75)

    def high_expansion(row: dict[str, Any]) -> bool:
        value = _number(row.get("expansion_score"))
        return value is not None and value > expansion_q75

    def expanding_btc(row: dict[str, Any]) -> bool:
        return str(row.get("btc_volatility_regime") or "").upper() == "EXPANDING"

    def mid_confirmation(row: dict[str, Any]) -> bool:
        value = _number(row.get("bars_from_sweep_to_confirmation"))
        return value is not None and 3 <= value <= 4

    rules: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
        (
            "high_expansion",
            "Expansion score above the discovery-only 75th percentile.",
            high_expansion,
        ),
        (
            "high_expansion_x_expanding_btc",
            "High expansion while BTC volatility regime is EXPANDING.",
            lambda row: high_expansion(row) and expanding_btc(row),
        ),
        (
            "high_expansion_x_mid_confirmation",
            "High expansion with 3-4 bars from sweep to confirmation.",
            lambda row: high_expansion(row) and mid_confirmation(row),
        ),
        (
            "high_expansion_x_expanding_btc_x_mid_confirmation",
            "High expansion, BTC EXPANDING regime, and 3-4 bar confirmation latency.",
            lambda row: high_expansion(row) and expanding_btc(row) and mid_confirmation(row),
        ),
    ]

    candidates: list[dict[str, Any]] = []
    predicates: dict[str, Callable[[dict[str, Any]], bool]] = {}
    for name, rationale, predicate in rules:
        predicates[name] = predicate
        discovery_result = _evaluate(discovery, predicate)
        selected_n = int(discovery_result["selected"]["n"])
        eligible = selected_n >= MIN_DISCOVERY_N
        candidates.append(
            {
                "name": name,
                "rationale": rationale,
                "discovery": discovery_result,
                "eligible_for_discovery_selection": eligible,
            }
        )

    eligible = [item for item in candidates if item["eligible_for_discovery_selection"]]
    selected_name = None
    if eligible:
        selected_name = max(
            eligible,
            key=lambda item: (
                item["discovery"]["average_net_r_uplift_vs_complement"]
                if item["discovery"]["average_net_r_uplift_vs_complement"] is not None
                else float("-inf")
            ),
        )["name"]

    selected_validation = None
    discovery_blocks: list[dict[str, Any]] = []
    validation_blocks: list[dict[str, Any]] = []
    validation_pass = False
    if selected_name is not None:
        selected_predicate = predicates[selected_name]
        selected_validation = _evaluate(validation, selected_predicate)
        discovery_blocks = _block_stats(discovery, selected_predicate, start_at, 4)
        validation_blocks = _block_stats(validation, selected_predicate, development_end_at, 2)
        selected_n = int(selected_validation["selected"]["n"])
        validation_pass = bool(
            selected_n >= MIN_VALIDATION_N
            and selected_validation["average_net_r_uplift_vs_complement"] is not None
            and selected_validation["average_net_r_uplift_vs_complement"] > 0
            and selected_validation["selected"]["average_net_r"] is not None
            and selected_validation["selected"]["average_net_r"] > 0
            and selected_validation["selected"]["profit_factor"] is not None
            and selected_validation["selected"]["profit_factor"] > 1.0
        )

    return {
        "analysis_version": ANALYSIS_VERSION,
        "status": "OK",
        "research_only": True,
        "promotion_allowed": False,
        "selection_policy": {
            "candidate_rules_fixed_before_interaction_validation": True,
            "winner_selected_on": "DEVELOPMENT only",
            "selection_metric": "average_net_r_uplift_vs_complement",
            "min_discovery_n": MIN_DISCOVERY_N,
            "min_validation_n": MIN_VALIDATION_N,
            "validation_threshold_search": False,
        },
        "learned_on_discovery_only": {
            "expansion_score_q75": round(expansion_q75, 8),
        },
        "candidate_discovery_results": candidates,
        "selected_on_discovery": selected_name,
        "selected_validation_result": selected_validation,
        "block_stability": {
            "discovery_30d_blocks": discovery_blocks,
            "validation_30d_blocks": validation_blocks,
        },
        "validation_edge_pass": validation_pass,
        "next_step": (
            "Candidate passed interaction screen; require fresh walk-forward/forward OOS before any live promotion."
            if validation_pass
            else "No promotable interaction edge; retain live strategy unchanged and investigate new orthogonal features/data."
        ),
        "warnings": [
            "This is a post-profile interaction screen, not final confirmation.",
            "Historical shorts remain technical-only; this report cannot establish Bybit EU borrowability.",
            "No OI/funding history is used here; derivatives remain context-only and never a hard gate.",
            "The source universe retains survivorship bias from the completed v0.7.3 backtest universe.",
        ],
    }
