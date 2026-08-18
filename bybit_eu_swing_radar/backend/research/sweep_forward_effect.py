"""Preregistered prospective effect analysis for the v0.7.3 sweep trigger.

Research only. This module never changes signal generation, scoring, execution,
or journal outcomes. It operates on already-closed prospective journal rows and
keeps the outcome layer behind a label-blind sample gate.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

SPEC_VERSION = "sweep-forward-effect-v1"
TRIGGER_MODEL = "LIQUIDITY_SWEEP_RECLAIM_5M_STRUCTURE_15M_CONFIRMATION"
MIN_CLOSED_SIGNALS = 60
MIN_PER_SIDE = 10
MIN_DISTINCT_UTC_DAYS = 10
MIN_ATTRIBUTE_COVERAGE_PCT = 95.0
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_SEED = 7331
CONFIDENCE_LEVEL = 0.95
MIN_HYPOTHESIS_SAMPLE = 40
MIN_GROUP_SAMPLE = 10


def spec() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": "0.7.3",
        "trigger_model": TRIGGER_MODEL,
        "research_only": True,
        "live_strategy_mutated": False,
        "prospective_journal_only": True,
        "label_gate_before_outcomes": True,
        "promotion_allowed": False,
        "sample_gate": {
            "minimum_closed_signals": MIN_CLOSED_SIGNALS,
            "minimum_per_side": MIN_PER_SIDE,
            "minimum_distinct_utc_days": MIN_DISTINCT_UTC_DAYS,
            "minimum_attribute_coverage_pct": MIN_ATTRIBUTE_COVERAGE_PCT,
        },
        "statistics": {
            "bootstrap": "UTC-day block bootstrap",
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "confidence_level": CONFIDENCE_LEVEL,
            "minimum_hypothesis_sample": MIN_HYPOTHESIS_SAMPLE,
            "minimum_group_sample": MIN_GROUP_SAMPLE,
            "threshold_search": False,
        },
        "hypotheses": [
            {
                "id": "H1_SWEEP_DEPTH",
                "feature": "sweep_depth_atr",
                "measure": "Spearman rho vs net_r",
                "expected_direction": "positive",
            },
            {
                "id": "H2_CONFIRMATION_SPEED",
                "feature": "bars_from_sweep_to_confirmation",
                "measure": "Spearman rho vs net_r",
                "expected_direction": "negative",
            },
            {
                "id": "H3_CONFIRMATION_VOLUME",
                "feature": "volume_ratio_5m",
                "measure": "Spearman rho vs net_r",
                "expected_direction": "positive",
            },
            {
                "id": "H4_15M_ALIGNMENT",
                "feature": "structure_15m_state",
                "measure": "mean net_r aligned shift minus neutral non-opposing",
                "expected_direction": "positive",
            },
        ],
        "warnings": [
            "A supported forward result is not sufficient for live promotion.",
            "No thresholds may be tuned on this forward sample.",
            "Short journal rows remain valid only where live Bybit EU USDC spot-margin execution eligibility was satisfied at signal time.",
        ],
    }


def sample_gate(counts: Mapping[str, Any]) -> dict[str, Any]:
    total = int(counts.get("closed_signal_count") or 0)
    long_count = int(counts.get("long_count") or 0)
    short_count = int(counts.get("short_count") or 0)
    days = int(counts.get("distinct_utc_days") or 0)
    complete = int(counts.get("attribute_complete_count") or 0)
    coverage = (complete / total * 100.0) if total else 0.0
    reasons: list[str] = []
    if total < MIN_CLOSED_SIGNALS:
        reasons.append(f"closed_signals {total}/{MIN_CLOSED_SIGNALS}")
    if long_count < MIN_PER_SIDE:
        reasons.append(f"long_signals {long_count}/{MIN_PER_SIDE}")
    if short_count < MIN_PER_SIDE:
        reasons.append(f"short_signals {short_count}/{MIN_PER_SIDE}")
    if days < MIN_DISTINCT_UTC_DAYS:
        reasons.append(f"distinct_utc_days {days}/{MIN_DISTINCT_UTC_DAYS}")
    if coverage + 1e-12 < MIN_ATTRIBUTE_COVERAGE_PCT:
        reasons.append(
            f"attribute_coverage_pct {coverage:.2f}/{MIN_ATTRIBUTE_COVERAGE_PCT:.2f}"
        )
    return {
        "ready": not reasons,
        "closed_signal_count": total,
        "long_count": long_count,
        "short_count": short_count,
        "distinct_utc_days": days,
        "attribute_complete_count": complete,
        "attribute_coverage_pct": round(coverage, 2),
        "reasons": reasons,
    }


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda idx: values[idx])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for pos in range(cursor, end):
            ranks[order[pos]] = average_rank
        cursor = end
    return ranks


def spearman(xs: Iterable[float], ys: Iterable[float]) -> float | None:
    x = list(xs)
    y = list(ys)
    if len(x) != len(y) or len(x) < 3:
        return None
    rx = _rank(x)
    ry = _rank(y)
    mean_x = statistics.fmean(rx)
    mean_y = statistics.fmean(ry)
    covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    denom_x = math.sqrt(sum((a - mean_x) ** 2 for a in rx))
    denom_y = math.sqrt(sum((b - mean_y) ** 2 for b in ry))
    if denom_x <= 0 or denom_y <= 0:
        return None
    return covariance / (denom_x * denom_y)


def _day_key(row: Mapping[str, Any]) -> str:
    value = row.get("opened_at")
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value or "")
    return text[:10] if len(text) >= 10 else "UNKNOWN"


def _block_bootstrap_ci(
    rows: list[dict[str, Any]],
    statistic: Callable[[list[dict[str, Any]]], float | None],
) -> tuple[float | None, float | None]:
    blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        blocks[_day_key(row)].append(row)
    keys = sorted(blocks)
    if len(keys) < 2:
        return None, None
    rng = random.Random(BOOTSTRAP_SEED)
    estimates: list[float] = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample: list[dict[str, Any]] = []
        for _index in range(len(keys)):
            sample.extend(blocks[rng.choice(keys)])
        estimate = statistic(sample)
        if estimate is not None and math.isfinite(estimate):
            estimates.append(float(estimate))
    if len(estimates) < max(100, BOOTSTRAP_ITERATIONS // 2):
        return None, None
    estimates.sort()
    alpha = (1.0 - CONFIDENCE_LEVEL) / 2.0
    lo_index = max(0, min(len(estimates) - 1, int(alpha * (len(estimates) - 1))))
    hi_index = max(
        0,
        min(len(estimates) - 1, int((1.0 - alpha) * (len(estimates) - 1))),
    )
    return estimates[lo_index], estimates[hi_index]


def _continuous_result(
    rows: list[dict[str, Any]],
    *,
    hypothesis_id: str,
    feature: str,
    expected_direction: str,
) -> dict[str, Any]:
    usable = [
        row
        for row in rows
        if _finite(row.get(feature)) is not None and _finite(row.get("net_r")) is not None
    ]

    def statistic(sample: list[dict[str, Any]]) -> float | None:
        return spearman(
            [float(row[feature]) for row in sample],
            [float(row["net_r"]) for row in sample],
        )

    observed = statistic(usable)
    lo, hi = _block_bootstrap_ci(usable, statistic) if len(usable) >= MIN_HYPOTHESIS_SAMPLE else (None, None)
    supported = False
    if lo is not None and hi is not None:
        supported = lo > 0 if expected_direction == "positive" else hi < 0
    return {
        "id": hypothesis_id,
        "feature": feature,
        "measure": "spearman_rho_vs_net_r",
        "expected_direction": expected_direction,
        "sample_size": len(usable),
        "estimate": None if observed is None else round(observed, 6),
        "ci_95": [
            None if lo is None else round(lo, 6),
            None if hi is None else round(hi, 6),
        ],
        "status": (
            "INSUFFICIENT_HYPOTHESIS_SAMPLE"
            if len(usable) < MIN_HYPOTHESIS_SAMPLE
            else ("DIRECTIONALLY_SUPPORTED" if supported else "NOT_DIRECTIONALLY_SUPPORTED")
        ),
    }


def _aligned_15m(row: Mapping[str, Any]) -> bool | None:
    state = str(row.get("structure_15m_state") or "")
    side = str(row.get("side") or "")
    if state == "NEUTRAL_NON_OPPOSING":
        return False
    if side == "long" and state == "BULLISH_SHIFT":
        return True
    if side == "short" and state == "BEARISH_SHIFT":
        return True
    return None


def _alignment_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [
        row
        for row in rows
        if _finite(row.get("net_r")) is not None and _aligned_15m(row) is not None
    ]
    aligned = [row for row in usable if _aligned_15m(row) is True]
    neutral = [row for row in usable if _aligned_15m(row) is False]

    def statistic(sample: list[dict[str, Any]]) -> float | None:
        a = [float(row["net_r"]) for row in sample if _aligned_15m(row) is True]
        n = [float(row["net_r"]) for row in sample if _aligned_15m(row) is False]
        if not a or not n:
            return None
        return statistics.fmean(a) - statistics.fmean(n)

    observed = statistic(usable)
    group_ready = len(aligned) >= MIN_GROUP_SAMPLE and len(neutral) >= MIN_GROUP_SAMPLE
    lo, hi = _block_bootstrap_ci(usable, statistic) if group_ready else (None, None)
    supported = lo is not None and lo > 0
    return {
        "id": "H4_15M_ALIGNMENT",
        "feature": "structure_15m_state",
        "measure": "mean_net_r_aligned_minus_neutral",
        "expected_direction": "positive",
        "sample_size": len(usable),
        "aligned_count": len(aligned),
        "neutral_count": len(neutral),
        "estimate": None if observed is None else round(observed, 6),
        "ci_95": [
            None if lo is None else round(lo, 6),
            None if hi is None else round(hi, 6),
        ],
        "status": (
            "INSUFFICIENT_GROUP_SAMPLE"
            if not group_ready
            else ("DIRECTIONALLY_SUPPORTED" if supported else "NOT_DIRECTIONALLY_SUPPORTED")
        ),
    }


def evaluate_effects(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hypotheses = [
        _continuous_result(
            rows,
            hypothesis_id="H1_SWEEP_DEPTH",
            feature="sweep_depth_atr",
            expected_direction="positive",
        ),
        _continuous_result(
            rows,
            hypothesis_id="H2_CONFIRMATION_SPEED",
            feature="bars_from_sweep_to_confirmation",
            expected_direction="negative",
        ),
        _continuous_result(
            rows,
            hypothesis_id="H3_CONFIRMATION_VOLUME",
            feature="volume_ratio_5m",
            expected_direction="positive",
        ),
        _alignment_result(rows),
    ]
    net = [float(row["net_r"]) for row in rows if _finite(row.get("net_r")) is not None]
    mfe = [float(row["mfe_r"]) for row in rows if _finite(row.get("mfe_r")) is not None]
    mae = [float(row["mae_r"]) for row in rows if _finite(row.get("mae_r")) is not None]
    return {
        "outcome_sample_size": len(net),
        "overall": {
            "average_net_r": round(statistics.fmean(net), 6) if net else None,
            "median_net_r": round(statistics.median(net), 6) if net else None,
            "positive_net_rate_pct": round(sum(v > 0 for v in net) / len(net) * 100.0, 2) if net else None,
            "average_mfe_r": round(statistics.fmean(mfe), 6) if mfe else None,
            "average_mae_r": round(statistics.fmean(mae), 6) if mae else None,
        },
        "hypotheses": hypotheses,
        "all_hypotheses_evaluable": all(
            item["status"] not in {"INSUFFICIENT_HYPOTHESIS_SAMPLE", "INSUFFICIENT_GROUP_SAMPLE"}
            for item in hypotheses
        ),
        "promotion_allowed": False,
    }
