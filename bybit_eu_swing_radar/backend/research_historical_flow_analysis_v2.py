"""Preregistered historical derivatives analysis for Trading Radar research v2.

Only the first 90d of DEVELOPMENT may select a candidate. The final 30d of
DEVELOPMENT is the internal holdout. The previously inspected VALIDATION split
is reference-only and cannot authorize promotion.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Any, Callable

ANALYSIS_VERSION = "day-trade-historical-flow-analysis-v2"
TRAIN_DAYS = 90
HOLDOUT_DAYS = 30
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
    position = (len(rows) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return rows[lower]
    weight = position - lower
    return rows[lower] * (1.0 - weight) + rows[upper] * weight


def _stats(values: list[float]) -> dict[str, Any]:
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    return {
        "n": len(values),
        "average_net_r": round(statistics.fmean(values), 6) if values else None,
        "median_net_r": round(statistics.median(values), 6) if values else None,
        "positive_rate_pct": round(sum(value > 0 for value in values) / len(values) * 100, 3) if values else None,
        "profit_factor": round(gains / losses, 6) if losses > 0 else None,
        "total_net_r": round(sum(values), 6),
    }


def _evaluate(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    selected: list[float] = []
    complement: list[float] = []
    for row in rows:
        net = _number(row.get("base_net_r"))
        if net is None:
            continue
        (selected if predicate(row) else complement).append(net)
    a = _stats(selected)
    b = _stats(complement)
    uplift = None
    if a["average_net_r"] is not None and b["average_net_r"] is not None:
        uplift = round(float(a["average_net_r"]) - float(b["average_net_r"]), 6)
    return {"selected": a, "complement": b, "average_net_r_uplift_vs_complement": uplift}


def build_historical_flow_report(
    rows: list[dict[str, Any]], *, start_at: datetime, development_end_at: datetime
) -> dict[str, Any]:
    train_end = start_at + timedelta(days=TRAIN_DAYS)
    holdout_end = min(development_end_at, train_end + timedelta(days=HOLDOUT_DAYS))
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

    expansion_values = [
        value
        for value in (_number(row.get("expansion_score")) for row in train)
        if value is not None
    ]
    q75 = _quantile(expansion_values, 0.75) if expansion_values else None

    def high_expansion(row: dict[str, Any]) -> bool:
        value = _number(row.get("expansion_score"))
        return q75 is not None and value is not None and value > q75

    def oi_building(row: dict[str, Any]) -> bool:
        one = _number(row.get("oi_change_1h_pct"))
        four = _number(row.get("oi_change_4h_pct"))
        return one is not None and four is not None and one > 0 and four > 0

    def oi_contracting(row: dict[str, Any]) -> bool:
        one = _number(row.get("oi_change_1h_pct"))
        four = _number(row.get("oi_change_4h_pct"))
        return one is not None and four is not None and one < 0 and four < 0

    def supportive_funding(row: dict[str, Any]) -> bool:
        funding = _number(row.get("funding_rate"))
        side = str(row.get("side") or "").lower()
        if funding is None:
            return False
        return (side == "long" and funding <= 0) or (side == "short" and funding >= 0)

    rules: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
        (
            "high_expansion_x_oi_building",
            "High expansion with OI rising over both 1h and 4h.",
            lambda row: high_expansion(row) and oi_building(row),
        ),
        (
            "high_expansion_x_oi_contracting",
            "High expansion with OI falling over both 1h and 4h.",
            lambda row: high_expansion(row) and oi_contracting(row),
        ),
        (
            "high_expansion_x_supportive_funding",
            "High expansion with non-crowded funding sign for trade direction.",
            lambda row: high_expansion(row) and supportive_funding(row),
        ),
        (
            "high_expansion_x_oi_building_x_supportive_funding",
            "High expansion with OI build and supportive funding sign.",
            lambda row: high_expansion(row) and oi_building(row) and supportive_funding(row),
        ),
    ]

    candidates = []
    predicates: dict[str, Callable[[dict[str, Any]], bool]] = {}
    for name, rationale, predicate in rules:
        predicates[name] = predicate
        result = _evaluate(train, predicate)
        candidates.append(
            {
                "name": name,
                "rationale": rationale,
                "eligible_for_train_selection": int(result["selected"]["n"]) >= MIN_TRAIN_N,
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
    winner_name = str(winner["name"]) if winner else None
    holdout_result = _evaluate(holdout, predicates[winner_name]) if winner_name else None
    external_reference = _evaluate(reused_validation, predicates[winner_name]) if winner_name else None
    internal_pass = False
    if holdout_result:
        selected = holdout_result["selected"]
        internal_pass = (
            int(selected["n"]) >= MIN_HOLDOUT_N
            and selected["average_net_r"] is not None
            and float(selected["average_net_r"]) > 0
            and selected["profit_factor"] is not None
            and float(selected["profit_factor"]) > 1
        )

    flow_rows = sum(bool(row.get("historical_flow_available")) for row in rows)
    return {
        "analysis_version": ANALYSIS_VERSION,
        "research_only": True,
        "promotion_allowed": False,
        "status": "OK" if train and holdout else "INSUFFICIENT_SPLIT_DATA",
        "coverage": {
            "rows": len(rows),
            "historical_flow_rows": flow_rows,
            "historical_flow_coverage_pct": round(flow_rows / len(rows) * 100, 3) if rows else 0.0,
        },
        "split_policy": {
            "winner_selected_on": "FIRST_90D_DEVELOPMENT_ONLY",
            "train_days": TRAIN_DAYS,
            "internal_holdout_days": HOLDOUT_DAYS,
            "historical_validation_status": "REUSED_REFERENCE_NOT_UNTOUCHED_OOS",
            "validation_threshold_search": False,
        },
        "learned_on_train_only": {"expansion_score_q75": round(q75, 8) if q75 is not None else None},
        "candidate_train_results": candidates,
        "selected_on_train": winner_name,
        "internal_holdout_result": holdout_result,
        "reused_external_validation_reference": external_reference,
        "internal_holdout_edge_pass": internal_pass,
        "next_step": (
            "Freeze the winning rule and require genuinely fresh forward holdout before any live promotion."
            if internal_pass
            else "No positive internal-holdout edge; do not tune these signs/thresholds further. Pivot or add a genuinely different microstructure feature family."
        ),
        "warnings": [
            "Derivatives data is research context only and never a hard gate.",
            "The original 60d validation has already been inspected and cannot authorize promotion.",
            "Historical shorts remain technical-only and do not establish Bybit EU borrowability.",
            "No live strategy, scoring, trigger, eligibility or execution logic is changed.",
        ],
    }
