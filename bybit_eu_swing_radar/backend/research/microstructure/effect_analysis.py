"""Preregistered effect test for the forward microstructure sample.

Outcome labels are intentionally inaccessible until the label-blind alignment sample
passes its frozen 60-total / 10-per-symbol gate. The analysis cohort is the earliest
chronological prefix that satisfies that gate, preventing optional stopping based on
outcomes. No result from this module authorizes live promotion.
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import asyncpg

from research.microstructure.alignment import (
    HYPOTHESES,
    MIN_SIGNAL_SAMPLE_PER_SYMBOL,
    MIN_SIGNAL_SAMPLE_TOTAL,
    SPEC_VERSION,
    sample_readiness,
)

EFFECT_SPEC_VERSION = "microstructure-effect-test-v1"
BLOCK_HOURS = 6
BOOTSTRAP_REPS = 2000
ALPHA = 0.05
MIN_TIME_BLOCKS = 6
MIN_OBS_PER_STABILITY_BLOCK = 3
MIN_DIRECTIONAL_BLOCK_SHARE = 2.0 / 3.0

OUTCOME_SQL = """
SELECT id AS signal_id, symbol, opened_at, net_r
FROM day_trade_signal_journal
WHERE id = ANY($1::bigint[])
  AND status = 'CLOSED'
  AND net_r IS NOT NULL
ORDER BY opened_at, id
"""


def effect_spec() -> dict[str, Any]:
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "alignment_spec_version": SPEC_VERSION,
        "effect_spec_version": EFFECT_SPEC_VERSION,
        "primary_label": "day_trade_signal_journal.net_r",
        "cohort_rule": (
            "Earliest chronological aligned-signal prefix satisfying >=60 total and "
            ">=10 per tracked symbol; outcomes are not queried before this prefix exists."
        ),
        "estimator": "Spearman rank correlation(feature, after-cost net_r)",
        "uncertainty": {
            "method": "6h time-block bootstrap",
            "replicates": BOOTSTRAP_REPS,
            "alpha": ALPHA,
            "minimum_time_blocks": MIN_TIME_BLOCKS,
        },
        "multiple_testing": "Holm correction across the four preregistered directional hypotheses",
        "time_block_stability": {
            "minimum_observations_per_block": MIN_OBS_PER_STABILITY_BLOCK,
            "minimum_expected_direction_share": MIN_DIRECTIONAL_BLOCK_SHARE,
        },
        "hypotheses": list(HYPOTHESES),
        "decision_rule": (
            "SUPPORTED only if point direction matches preregistration, Holm-adjusted one-sided "
            "bootstrap p<=0.05, the 95% block-bootstrap CI excludes zero in the expected direction, "
            "and >=2/3 of sufficiently populated 6h blocks have the expected sign. Otherwise INCONCLUSIVE."
        ),
        "promotion_rule": "Never promote from this forward sample alone; require a subsequent untouched validation period.",
    }


def _opened_at(row: Mapping[str, Any]) -> datetime:
    value = row.get("opened_at")
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("opened_at is missing")
    if dt.tzinfo is None:
        raise ValueError("opened_at must be timezone-aware")
    return dt.astimezone(timezone.utc)


def select_earliest_ready_cohort(
    features: Iterable[Mapping[str, Any]], symbols: Iterable[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select the earliest outcome-blind prefix that satisfies the frozen sample gate."""
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    ordered = sorted((dict(row) for row in features), key=lambda row: (_opened_at(row), int(row["signal_id"])))
    for index in range(1, len(ordered) + 1):
        prefix = ordered[:index]
        readiness = sample_readiness(prefix, wanted)
        if readiness["ready_for_preregistered_effect_test"]:
            return prefix, {
                **readiness,
                "cohort_frozen": True,
                "cohort_size": len(prefix),
                "cohort_last_opened_at": _opened_at(prefix[-1]).isoformat(),
            }
    return [], {
        **sample_readiness(ordered, wanted),
        "cohort_frozen": False,
        "cohort_size": 0,
        "cohort_last_opened_at": None,
    }


async def load_closed_outcomes(database_url: str, cohort: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Load labels only for an already-frozen sample-ready cohort."""
    rows = [dict(row) for row in cohort]
    if not rows:
        raise RuntimeError("effect cohort is not sample-ready; outcome query is forbidden")
    ids = [int(row["signal_id"]) for row in rows]
    if len(ids) < MIN_SIGNAL_SAMPLE_TOTAL:
        raise RuntimeError("effect cohort is below preregistered minimum; outcome query is forbidden")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    connection = await asyncpg.connect(database_url)
    try:
        result = await connection.fetch(OUTCOME_SQL, ids)
    finally:
        await connection.close()
    return [dict(row) for row in result]


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = rank
        i = j
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    if len(x) < 2 or len(x) != len(y):
        return 0.0
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    dx = [v - mx for v in x]
    dy = [v - my for v in y]
    denom = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    if denom == 0:
        return 0.0
    return sum(a * b for a, b in zip(dx, dy)) / denom


def spearman(x: list[float], y: list[float]) -> float:
    if len(x) < 2 or len(x) != len(y):
        return 0.0
    return _pearson(_ranks(x), _ranks(y))


def _block_key(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    hour = (dt.hour // BLOCK_HOURS) * BLOCK_HOURS
    return f"{dt.date().isoformat()}T{hour:02d}"


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _bootstrap_effect(rows: list[dict[str, Any]], feature: str, expected: str, seed_text: str) -> dict[str, Any]:
    blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        blocks[_block_key(_opened_at(row))].append(row)
    keys = sorted(blocks)
    x = [float(row[feature]) for row in rows]
    y = [float(row["net_r"]) for row in rows]
    point = spearman(x, y)

    boot: list[float] = []
    if len(keys) >= 2:
        seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)
        for _ in range(BOOTSTRAP_REPS):
            sampled: list[dict[str, Any]] = []
            for _ in keys:
                sampled.extend(blocks[rng.choice(keys)])
            boot.append(
                spearman(
                    [float(row[feature]) for row in sampled],
                    [float(row["net_r"]) for row in sampled],
                )
            )
    else:
        boot = [point]

    lo = _quantile(boot, ALPHA / 2.0)
    hi = _quantile(boot, 1.0 - ALPHA / 2.0)
    if expected == "positive":
        p_one_sided = (1 + sum(1 for value in boot if value <= 0.0)) / (len(boot) + 1)
        point_ok = point > 0
        ci_ok = lo > 0
    else:
        p_one_sided = (1 + sum(1 for value in boot if value >= 0.0)) / (len(boot) + 1)
        point_ok = point < 0
        ci_ok = hi < 0

    eligible_block_effects = []
    for key in keys:
        block = blocks[key]
        if len(block) < MIN_OBS_PER_STABILITY_BLOCK:
            continue
        rho = spearman(
            [float(row[feature]) for row in block],
            [float(row["net_r"]) for row in block],
        )
        eligible_block_effects.append(rho)
    matching = sum(
        1 for rho in eligible_block_effects
        if (rho > 0 if expected == "positive" else rho < 0)
    )
    directional_share = matching / len(eligible_block_effects) if eligible_block_effects else 0.0
    return {
        "rho": point,
        "ci95": [lo, hi],
        "p_one_sided": p_one_sided,
        "time_block_count": len(keys),
        "eligible_stability_blocks": len(eligible_block_effects),
        "directional_block_share": directional_share,
        "point_direction_ok": point_ok,
        "ci_direction_ok": ci_ok,
    }


def _holm_adjust(results: list[dict[str, Any]]) -> None:
    ordered = sorted(enumerate(results), key=lambda item: item[1]["p_one_sided"])
    running = 0.0
    m = len(results)
    for rank, (index, result) in enumerate(ordered):
        adjusted = min(1.0, (m - rank) * result["p_one_sided"])
        running = max(running, adjusted)
        results[index]["p_holm"] = running


def analyze_preregistered_effects(
    cohort: Iterable[Mapping[str, Any]], outcomes: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    features = [dict(row) for row in cohort]
    outcome_map = {int(row["signal_id"]): dict(row) for row in outcomes}
    missing = [int(row["signal_id"]) for row in features if int(row["signal_id"]) not in outcome_map]
    if missing:
        return {
            "research_only": True,
            "promotion_allowed": False,
            "effect_spec": effect_spec(),
            "status": "WAITING_FOR_CLOSED_OUTCOMES",
            "cohort_size": len(features),
            "closed_outcomes": len(features) - len(missing),
            "missing_outcomes": len(missing),
            "results": [],
        }

    joined = []
    for row in features:
        item = dict(row)
        item["net_r"] = float(outcome_map[int(row["signal_id"])]["net_r"])
        joined.append(item)

    results = []
    for hypothesis in HYPOTHESES:
        result = _bootstrap_effect(
            joined,
            str(hypothesis["feature"]),
            str(hypothesis["expected_direction"]),
            f"{EFFECT_SPEC_VERSION}|{hypothesis['id']}",
        )
        result.update({
            "id": hypothesis["id"],
            "feature": hypothesis["feature"],
            "expected_direction": hypothesis["expected_direction"],
        })
        results.append(result)
    _holm_adjust(results)

    for result in results:
        stability_ok = (
            result["time_block_count"] >= MIN_TIME_BLOCKS
            and result["directional_block_share"] >= MIN_DIRECTIONAL_BLOCK_SHARE
        )
        supported = (
            result["point_direction_ok"]
            and result["ci_direction_ok"]
            and result["p_holm"] <= ALPHA
            and stability_ok
        )
        result["time_block_stability_ok"] = stability_ok
        result["verdict"] = "SUPPORTED" if supported else "INCONCLUSIVE"

    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "effect_spec": effect_spec(),
        "status": "COMPLETE",
        "cohort_size": len(joined),
        "closed_outcomes": len(joined),
        "results": results,
        "promotion_decision": "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION",
    }
