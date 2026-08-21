"""Outcome-bearing effect test for the preregistered v0.7.5 microstructure cohort.

The pre-outcome contract lives in ``effect_analysis_v3`` and must already validate
before this module can freeze a cohort or query any journal outcome. The cohort is
the earliest chronological aligned-signal prefix satisfying the frozen 60-total /
10-per-symbol gate. This prevents optional stopping as later signals arrive.

Research-only: no live strategy/scoring/eligibility/execution mutation path and no
threshold/model search. Results are descriptive DEVELOPMENT evidence only.
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import asyncpg

from research.microstructure import alignment_v3, effect_analysis_v3

BLOCK_HOURS = 6
BOOTSTRAP_REPS = 2000
ALPHA = 0.05
MIN_OBS_PER_STABILITY_BLOCK = 3

OUTCOME_SQL = """
SELECT id AS signal_id, symbol, opened_at, net_r
FROM day_trade_signal_journal
WHERE id = ANY($1::bigint[])
  AND strategy_version = $2
  AND status = 'CLOSED'
  AND net_r IS NOT NULL
ORDER BY opened_at, id
"""


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


def _validate_preregistration() -> None:
    ok, reason = effect_analysis_v3.validate_effect_preregistration(
        effect_analysis_v3.effect_analysis_spec()
    )
    if not ok:
        raise RuntimeError(f"v3 effect preregistration invalid: {reason}")


def select_earliest_ready_cohort(
    features: Iterable[Mapping[str, Any]], symbols: Iterable[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Freeze the earliest outcome-blind v0.7.5 prefix satisfying the sample gate."""
    _validate_preregistration()
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    ordered = sorted(
        (dict(row) for row in features),
        key=lambda row: (_opened_at(row), int(row["signal_id"])),
    )
    contaminated = sorted(
        {
            str(row.get("strategy_version") or "")
            for row in ordered
            if str(row.get("strategy_version") or "")
            != alignment_v3.PREREGISTERED_STRATEGY_VERSION
        }
    )
    if contaminated:
        raise ValueError("v3 effect cohort strategy contamination: " + ",".join(contaminated))

    for index in range(1, len(ordered) + 1):
        prefix = ordered[:index]
        readiness = alignment_v3.sample_readiness(prefix, wanted)
        if readiness["ready_for_preregistered_effect_test"]:
            return prefix, {
                **readiness,
                "cohort_frozen": True,
                "cohort_size": len(prefix),
                "cohort_last_opened_at": _opened_at(prefix[-1]).isoformat(),
                "strategy_version": alignment_v3.PREREGISTERED_STRATEGY_VERSION,
            }
    return [], {
        **alignment_v3.sample_readiness(ordered, wanted),
        "cohort_frozen": False,
        "cohort_size": 0,
        "cohort_last_opened_at": None,
        "strategy_version": alignment_v3.PREREGISTERED_STRATEGY_VERSION,
    }


async def load_closed_outcomes(
    database_url: str, cohort: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Read outcomes only after the exact v3 cohort is demonstrably sample-ready."""
    _validate_preregistration()
    rows = [dict(row) for row in cohort]
    if not rows:
        raise RuntimeError("v3 effect cohort is not sample-ready; outcome query is forbidden")
    wanted_symbols = tuple(sorted({str(row.get("symbol") or "").upper() for row in rows}))
    readiness = alignment_v3.sample_readiness(rows, wanted_symbols)
    if readiness.get("ready_for_preregistered_effect_test") is not True:
        raise RuntimeError("v3 effect cohort is below preregistered minimum; outcome query is forbidden")
    if any(
        str(row.get("strategy_version") or "") != alignment_v3.PREREGISTERED_STRATEGY_VERSION
        for row in rows
    ):
        raise RuntimeError("v3 effect cohort strategy contamination; outcome query is forbidden")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    ids = [int(row["signal_id"]) for row in rows]
    connection = await asyncpg.connect(database_url)
    try:
        result = await connection.fetch(
            OUTCOME_SQL,
            ids,
            alignment_v3.PREREGISTERED_STRATEGY_VERSION,
        )
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
    dx = [value - mx for value in x]
    dy = [value - my for value in y]
    denominator = math.sqrt(sum(value * value for value in dx) * sum(value * value for value in dy))
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(dx, dy)) / denominator


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
    position = (len(ordered) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _direction_matches(value: float, expected: str) -> bool:
    return value > 0 if expected == "positive" else value < 0


def _pooled_effect(
    rows: list[dict[str, Any]], feature: str, expected: str, seed_text: str
) -> dict[str, Any]:
    blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        blocks[_block_key(_opened_at(row))].append(row)
    keys = sorted(blocks)
    point = spearman(
        [float(row[feature]) for row in rows],
        [float(row["net_r_after_costs"]) for row in rows],
    )

    bootstrap: list[float] = []
    if len(keys) >= 2:
        seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)
        for _ in range(BOOTSTRAP_REPS):
            sampled: list[dict[str, Any]] = []
            for _ in keys:
                sampled.extend(blocks[rng.choice(keys)])
            bootstrap.append(
                spearman(
                    [float(row[feature]) for row in sampled],
                    [float(row["net_r_after_costs"]) for row in sampled],
                )
            )
    else:
        bootstrap = [point]

    low = _quantile(bootstrap, ALPHA / 2.0)
    high = _quantile(bootstrap, 1.0 - ALPHA / 2.0)
    non_positive = (1 + sum(value <= 0.0 for value in bootstrap)) / (len(bootstrap) + 1)
    non_negative = (1 + sum(value >= 0.0 for value in bootstrap)) / (len(bootstrap) + 1)
    p_two_sided = min(1.0, 2.0 * min(non_positive, non_negative))

    eligible_block_rhos: list[float] = []
    for key in keys:
        block = blocks[key]
        if len(block) < MIN_OBS_PER_STABILITY_BLOCK:
            continue
        eligible_block_rhos.append(
            spearman(
                [float(row[feature]) for row in block],
                [float(row["net_r_after_costs"]) for row in block],
            )
        )
    matching = sum(_direction_matches(rho, expected) for rho in eligible_block_rhos)
    directional_share = matching / len(eligible_block_rhos) if eligible_block_rhos else 0.0
    return {
        "n": len(rows),
        "rho": point,
        "ci95": [low, high],
        "p_two_sided_descriptive": p_two_sided,
        "expected_direction": expected,
        "sign_matches_preregistration": _direction_matches(point, expected),
        "time_block_count": len(keys),
        "eligible_stability_blocks": len(eligible_block_rhos),
        "directional_block_share": directional_share,
    }


def analyze_preregistered_effects(
    cohort: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    symbols: Iterable[str],
) -> dict[str, Any]:
    """Evaluate exactly the frozen v3 features; never fit thresholds or models."""
    _validate_preregistration()
    features = [dict(row) for row in cohort]
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    outcome_map = {int(row["signal_id"]): dict(row) for row in outcomes}
    missing_ids = [
        int(row["signal_id"])
        for row in features
        if int(row["signal_id"]) not in outcome_map
    ]
    if missing_ids:
        return {
            "research_only": True,
            "live_strategy_mutated": False,
            "promotion_allowed": False,
            "threshold_search_allowed": False,
            "model_search_allowed": False,
            "outcome_visible": False,
            "effect_spec": effect_analysis_v3.effect_analysis_spec(),
            "status": "WAITING_FOR_CLOSED_OUTCOMES",
            "cohort_size": len(features),
            "closed_outcomes": len(features) - len(missing_ids),
            "missing_outcomes": len(missing_ids),
            "missing_outcome_signal_ids": missing_ids,
            "results": [],
        }

    joined: list[dict[str, Any]] = []
    for row in features:
        item = dict(row)
        item["net_r_after_costs"] = float(outcome_map[int(row["signal_id"])]["net_r"])
        joined.append(item)

    results: list[dict[str, Any]] = []
    for hypothesis in effect_analysis_v3.HYPOTHESES:
        feature = str(hypothesis["feature"])
        expected = str(hypothesis["expected_direction"])
        pooled = _pooled_effect(
            joined,
            feature,
            expected,
            f"{effect_analysis_v3.SPEC_VERSION}|{hypothesis['id']}",
        )
        stratified: dict[str, Any] = {}
        symbol_sign_matches = 0
        symbol_effects = 0
        for symbol in wanted:
            symbol_rows = [row for row in joined if str(row.get("symbol") or "").upper() == symbol]
            rho = spearman(
                [float(row[feature]) for row in symbol_rows],
                [float(row["net_r_after_costs"]) for row in symbol_rows],
            )
            sign_match = _direction_matches(rho, expected) if len(symbol_rows) >= 2 else None
            stratified[symbol] = {
                "n": len(symbol_rows),
                "rho": rho if len(symbol_rows) >= 2 else None,
                "sign_matches_preregistration": sign_match,
            }
            if sign_match is not None:
                symbol_effects += 1
                symbol_sign_matches += int(sign_match)
        results.append(
            {
                "id": hypothesis["id"],
                "feature": feature,
                "expected_direction": expected,
                "pooled": pooled,
                "symbol_stratified": stratified,
                "symbol_sign_consistency": {
                    "matching_symbols": symbol_sign_matches,
                    "evaluable_symbols": symbol_effects,
                    "share": symbol_sign_matches / symbol_effects if symbol_effects else 0.0,
                },
                "measured_effect_is_descriptive": True,
            }
        )

    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "threshold_search_allowed": False,
        "model_search_allowed": False,
        "outcome_visible": True,
        "effect_spec": effect_analysis_v3.effect_analysis_spec(),
        "status": "COMPLETE",
        "cohort_size": len(joined),
        "closed_outcomes": len(joined),
        "excluded_outcome_signal_ids": [],
        "results": results,
        "interpretation_rule": (
            "Measured associations are descriptive DEVELOPMENT evidence against the four frozen "
            "directional hypotheses. No threshold/model fitting and no live promotion are authorized."
        ),
        "promotion_decision": "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION",
    }
