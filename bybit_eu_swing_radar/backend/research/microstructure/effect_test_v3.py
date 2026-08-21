"""Outcome-bearing implementation of the separately preregistered v0.7.5 study."""
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


def _dt(row: Mapping[str, Any]) -> datetime:
    value = row.get("opened_at")
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("opened_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _validate_preregistration() -> None:
    ok, reason = effect_analysis_v3.validate_effect_preregistration(effect_analysis_v3.effect_analysis_spec())
    if not ok:
        raise RuntimeError(f"v3 effect preregistration invalid: {reason}")


def select_earliest_ready_cohort(features: Iterable[Mapping[str, Any]], symbols: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Freeze the earliest chronological outcome-blind prefix satisfying 60/10."""
    _validate_preregistration()
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    ordered = sorted((dict(row) for row in features), key=lambda row: (_dt(row), int(row["signal_id"])))
    if any(str(row.get("strategy_version") or "") != alignment_v3.PREREGISTERED_STRATEGY_VERSION for row in ordered):
        raise ValueError("v3 effect cohort strategy contamination")
    for index in range(1, len(ordered) + 1):
        prefix = ordered[:index]
        gate = alignment_v3.sample_readiness(prefix, wanted)
        if gate["ready_for_preregistered_effect_test"]:
            return prefix, {**gate, "cohort_frozen": True, "cohort_size": len(prefix), "cohort_last_opened_at": _dt(prefix[-1]).isoformat()}
    return [], {**alignment_v3.sample_readiness(ordered, wanted), "cohort_frozen": False, "cohort_size": 0, "cohort_last_opened_at": None}


async def load_closed_outcomes(database_url: str, cohort: Iterable[Mapping[str, Any]], symbols: Iterable[str]) -> list[dict[str, Any]]:
    """Read labels only for a demonstrably sample-ready exact v3 cohort."""
    _validate_preregistration()
    rows = [dict(row) for row in cohort]
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    if not rows or alignment_v3.sample_readiness(rows, wanted).get("ready_for_preregistered_effect_test") is not True:
        raise RuntimeError("v3 effect cohort is below preregistered minimum; outcome query is forbidden")
    if any(str(row.get("strategy_version") or "") != alignment_v3.PREREGISTERED_STRATEGY_VERSION for row in rows):
        raise RuntimeError("v3 effect cohort strategy contamination; outcome query is forbidden")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    connection = await asyncpg.connect(database_url)
    try:
        result = await connection.fetch(OUTCOME_SQL, [int(row["signal_id"]) for row in rows], alignment_v3.PREREGISTERED_STRATEGY_VERSION)
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


def spearman(x: list[float], y: list[float]) -> float:
    if len(x) < 2 or len(x) != len(y):
        return 0.0
    x, y = _ranks(x), _ranks(y)
    mx, my = sum(x) / len(x), sum(y) / len(y)
    dx, dy = [v - mx for v in x], [v - my for v in y]
    denom = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    return 0.0 if denom == 0 else sum(a * b for a, b in zip(dx, dy)) / denom


def _block_key(dt: datetime) -> str:
    hour = (dt.hour // BLOCK_HOURS) * BLOCK_HOURS
    return f"{dt.date().isoformat()}T{hour:02d}"


def _quantile(values: list[float], q: float) -> float:
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return values[lo] if lo == hi else values[lo] * (hi - pos) + values[hi] * (pos - lo)


def _matches(value: float, expected: str) -> bool:
    return value > 0 if expected == "positive" else value < 0


def _pooled(rows: list[dict[str, Any]], feature: str, expected: str, seed_text: str) -> dict[str, Any]:
    blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        blocks[_block_key(_dt(row))].append(row)
    keys = sorted(blocks)
    point = spearman([float(row[feature]) for row in rows], [float(row["net_r_after_costs"]) for row in rows])
    boot = [point]
    if len(keys) >= 2:
        rng = random.Random(int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16))
        boot = []
        for _ in range(BOOTSTRAP_REPS):
            sample: list[dict[str, Any]] = []
            for _ in keys:
                sample.extend(blocks[rng.choice(keys)])
            boot.append(spearman([float(row[feature]) for row in sample], [float(row["net_r_after_costs"]) for row in sample]))
    low, high = _quantile(boot, ALPHA / 2), _quantile(boot, 1 - ALPHA / 2)
    pneg = (1 + sum(value <= 0 for value in boot)) / (len(boot) + 1)
    ppos = (1 + sum(value >= 0 for value in boot)) / (len(boot) + 1)
    stable = []
    for key in keys:
        block = blocks[key]
        if len(block) >= MIN_OBS_PER_STABILITY_BLOCK:
            stable.append(spearman([float(row[feature]) for row in block], [float(row["net_r_after_costs"]) for row in block]))
    return {"n": len(rows), "rho": point, "ci95": [low, high], "p_two_sided_descriptive": min(1.0, 2 * min(pneg, ppos)), "expected_direction": expected, "sign_matches_preregistration": _matches(point, expected), "time_block_count": len(keys), "eligible_stability_blocks": len(stable), "directional_block_share": (sum(_matches(v, expected) for v in stable) / len(stable)) if stable else 0.0}


def analyze_preregistered_effects(cohort: Iterable[Mapping[str, Any]], outcomes: Iterable[Mapping[str, Any]], symbols: Iterable[str]) -> dict[str, Any]:
    """Run exactly the frozen descriptive methods; never fit thresholds/models."""
    _validate_preregistration()
    features = [dict(row) for row in cohort]
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    outcome_map = {int(row["signal_id"]): dict(row) for row in outcomes}
    missing = [int(row["signal_id"]) for row in features if int(row["signal_id"]) not in outcome_map]
    base = {"research_only": True, "live_strategy_mutated": False, "promotion_allowed": False, "threshold_search_allowed": False, "model_search_allowed": False, "effect_spec": effect_analysis_v3.effect_analysis_spec()}
    if missing:
        return {**base, "outcome_visible": False, "status": "WAITING_FOR_CLOSED_OUTCOMES", "cohort_size": len(features), "closed_outcomes": len(features) - len(missing), "missing_outcomes": len(missing), "missing_outcome_signal_ids": missing, "results": []}
    joined = []
    for row in features:
        item = dict(row)
        item["net_r_after_costs"] = float(outcome_map[int(row["signal_id"])]["net_r"])
        joined.append(item)
    results = []
    for hypothesis in effect_analysis_v3.HYPOTHESES:
        feature, expected = str(hypothesis["feature"]), str(hypothesis["expected_direction"])
        stratified = {}
        matching = evaluable = 0
        for symbol in wanted:
            rows = [row for row in joined if str(row.get("symbol") or "").upper() == symbol]
            rho = spearman([float(row[feature]) for row in rows], [float(row["net_r_after_costs"]) for row in rows]) if len(rows) >= 2 else None
            sign = _matches(rho, expected) if rho is not None else None
            stratified[symbol] = {"n": len(rows), "rho": rho, "sign_matches_preregistration": sign}
            if sign is not None:
                evaluable += 1
                matching += int(sign)
        results.append({"id": hypothesis["id"], "feature": feature, "expected_direction": expected, "pooled": _pooled(joined, feature, expected, f"{effect_analysis_v3.SPEC_VERSION}|{hypothesis['id']}"), "symbol_stratified": stratified, "symbol_sign_consistency": {"matching_symbols": matching, "evaluable_symbols": evaluable, "share": matching / evaluable if evaluable else 0.0}, "measured_effect_is_descriptive": True})
    return {**base, "outcome_visible": True, "status": "COMPLETE", "cohort_size": len(joined), "closed_outcomes": len(joined), "excluded_outcome_signal_ids": [], "results": results, "promotion_decision": "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION"}
