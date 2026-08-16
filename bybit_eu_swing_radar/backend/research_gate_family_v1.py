"""Nested gate-family research for the materialized v0.7.3 dataset.

Research only. Because the original 60d validation period has already been
inspected by prior analyses, this module does not treat it as untouched OOS.
Rule selection uses only the first 90d of DEVELOPMENT; the final 30d of
DEVELOPMENT is an internal holdout. The historical VALIDATION split is shown
only as a reused external reference and can never authorize promotion.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Any, Callable

ANALYSIS_VERSION = "day-trade-gate-family-v1"
TRAIN_DAYS = 90
INTERNAL_HOLDOUT_DAYS = 30
MIN_TRAIN_N = 150
MIN_HOLDOUT_N = 50


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


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
    clean = [float(v) for v in values if math.isfinite(float(v))]
    gains = sum(v for v in clean if v > 0)
    losses = abs(sum(v for v in clean if v < 0))
    return {
        "n": len(clean),
        "average_net_r": round(statistics.fmean(clean), 6) if clean else None,
        "median_net_r": round(statistics.median(clean), 6) if clean else None,
        "positive_rate_pct": round(sum(v > 0 for v in clean) / len(clean) * 100, 3) if clean else None,
        "profit_factor": round(gains / losses, 6) if losses > 0 else None,
        "total_net_r": round(sum(clean), 6),
    }


def _evaluate(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    selected: list[float] = []
    complement: list[float] = []
    for row in rows:
        net = _number(row.get("base_net_r"))
        if net is None:
            continue
        (selected if predicate(row) else complement).append(net)
    selected_stats = _stats(selected)
    complement_stats = _stats(complement)
    uplift = None
    if selected_stats["average_net_r"] is not None and complement_stats["average_net_r"] is not None:
        uplift = round(selected_stats["average_net_r"] - complement_stats["average_net_r"], 6)
    return {
        "selected": selected_stats,
        "complement": complement_stats,
        "average_net_r_uplift_vs_complement": uplift,
    }


def build_gate_family_report(
    rows: list[dict[str, Any]], *, start_at: datetime, development_end_at: datetime
) -> dict[str, Any]:
    train_end = start_at + timedelta(days=TRAIN_DAYS)
    holdout_end = min(development_end_at, train_end + timedelta(days=INTERNAL_HOLDOUT_DAYS))

    train_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    reused_validation_rows: list[dict[str, Any]] = []
    for row in rows:
        opened = _parse_time(row.get("opened_at"))
        if opened is None:
            continue
        if start_at <= opened < train_end:
            train_rows.append(row)
        elif train_end <= opened < holdout_end:
            holdout_rows.append(row)
        elif row.get("dataset_split") == "VALIDATION":
            reused_validation_rows.append(row)

    expansion_values = [
        value
        for value in (_number(row.get("expansion_score")) for row in train_rows)
        if value is not None
    ]
    q75 = _quantile(expansion_values, 0.75) if expansion_values else None

    def confirmations_complete(row: dict[str, Any]) -> bool:
        return bool(row.get("pass_volume_confirmation")) and bool(row.get("pass_structure_15m"))

    def reward_path_complete(row: dict[str, Any]) -> bool:
        return bool(row.get("pass_target_path")) and bool(row.get("pass_rr"))

    def full_pretrade_chain(row: dict[str, Any]) -> bool:
        return confirmations_complete(row) and reward_path_complete(row)

    rules: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
        ("confirmations_complete", "5m volume confirmation plus non-opposing closed 15m structure.", confirmations_complete),
        ("reward_path_complete", "Structural target path plus modeled net RR gate.", reward_path_complete),
        ("full_pretrade_chain", "Both confirmation and reward-path gate families pass.", full_pretrade_chain),
    ]
    if q75 is not None:
        rules.append(
            (
                "high_expansion_x_full_pretrade_chain",
                "Train-only top-quartile expansion plus the full pretrade gate family.",
                lambda row: full_pretrade_chain(row)
                and (_number(row.get("expansion_score")) or -math.inf) > q75,
            )
        )

    candidates: list[dict[str, Any]] = []
    predicates: dict[str, Callable[[dict[str, Any]], bool]] = {}
    for name, rationale, predicate in rules:
        predicates[name] = predicate
        result = _evaluate(train_rows, predicate)
        eligible = int(result["selected"]["n"]) >= MIN_TRAIN_N
        candidates.append(
            {
                "name": name,
                "rationale": rationale,
                "eligible_for_train_selection": eligible,
                "train": result,
            }
        )

    eligible = [
        item for item in candidates
        if item["eligible_for_train_selection"]
        and item["train"]["average_net_r_uplift_vs_complement"] is not None
    ]
    winner = max(
        eligible,
        key=lambda item: float(item["train"]["average_net_r_uplift_vs_complement"]),
        default=None,
    )

    holdout_result = None
    external_reference = None
    internal_pass = False
    winner_name = None
    if winner is not None:
        winner_name = str(winner["name"])
        predicate = predicates[winner_name]
        holdout_result = _evaluate(holdout_rows, predicate)
        external_reference = _evaluate(reused_validation_rows, predicate)
        selected = holdout_result["selected"]
        internal_pass = (
            int(selected["n"]) >= MIN_HOLDOUT_N
            and selected["average_net_r"] is not None
            and float(selected["average_net_r"]) > 0.0
            and selected["profit_factor"] is not None
            and float(selected["profit_factor"]) > 1.0
        )

    return {
        "analysis_version": ANALYSIS_VERSION,
        "research_only": True,
        "promotion_allowed": False,
        "status": "OK" if train_rows and holdout_rows else "INSUFFICIENT_SPLIT_DATA",
        "split_policy": {
            "train_days": TRAIN_DAYS,
            "internal_holdout_days": INTERNAL_HOLDOUT_DAYS,
            "train_end_at": train_end.isoformat(),
            "internal_holdout_end_at": holdout_end.isoformat(),
            "historical_validation_status": "REUSED_REFERENCE_NOT_UNTOUCHED_OOS",
            "winner_selected_on": "FIRST_90D_DEVELOPMENT_ONLY",
            "validation_threshold_search": False,
        },
        "learned_on_train_only": {"expansion_score_q75": round(q75, 8) if q75 is not None else None},
        "candidate_train_results": candidates,
        "selected_on_train": winner_name,
        "internal_holdout_result": holdout_result,
        "reused_external_validation_reference": external_reference,
        "internal_holdout_edge_pass": internal_pass,
        "next_step": (
            "Preregister the frozen rule for a genuinely fresh forward holdout; live promotion remains forbidden."
            if internal_pass
            else "No positive internal-holdout edge; do not promote. Move to genuinely new orthogonal data such as aligned order-flow/derivatives history."
        ),
        "warnings": [
            "The original 60d validation has already been inspected by prior research and is not untouched OOS for this new hypothesis.",
            "Historical shorts remain technical-only and cannot establish Bybit EU borrowability.",
            "OI/funding is not used by this analysis and remains context-only, never a hard gate.",
            "No live strategy, scoring, trigger, eligibility or execution logic is changed.",
        ],
    }
