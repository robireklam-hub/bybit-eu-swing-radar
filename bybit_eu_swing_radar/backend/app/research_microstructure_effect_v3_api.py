"""Research-only v0.7.5 microstructure effect status; labels stay closed until 60/10."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from fastapi import Depends, FastAPI

from app.research_microstructure_alignment_v3_api import _load_v3_journal_signal_counts, build_alignment_v3_status
from research.microstructure.alignment_v3 import COHORT_START_AT, load_feature_rows
from research.microstructure.collector import MicrostructureConfig
from research.microstructure.effect_analysis_v3 import effect_analysis_spec
from research.microstructure.effect_test_v3 import analyze_preregistered_effects, load_closed_outcomes, select_earliest_ready_cohort
from research.microstructure.readiness import get_readiness

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("effect boundary timestamp is missing")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("effect boundary timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


async def build_effect_v3_status(database_url: str, symbols: tuple[str, ...] | list[str], bucket_seconds: int) -> dict[str, Any]:
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    base = {"research_only": True, "live_strategy_mutated": False, "promotion_allowed": False, "threshold_search_allowed": False, "model_search_allowed": False, "effect_spec": effect_analysis_spec()}
    readiness = await get_readiness(database_url, wanted, bucket_seconds)
    if readiness.get("ready_for_forward_feature_analysis") is not True:
        return {**base, "status": "WAITING_FOR_DATA_QUALITY", "outcome_visible": False, "ready_for_preregistered_effect_test": False}

    first_buckets = [item.get("first_bucket_at") for item in readiness.get("symbols") or [] if isinstance(item, Mapping) and item.get("first_bucket_at")]
    if len(first_buckets) != len(wanted):
        raise ValueError("readiness first_bucket_at coverage is incomplete")
    since = max(max(_parse_dt(value) for value in first_buckets), COHORT_START_AT)
    until = _parse_dt(readiness.get("checked_at"))
    features, journal_counts = await asyncio.gather(
        load_feature_rows(database_url, wanted, since, until, bucket_seconds=bucket_seconds),
        _load_v3_journal_signal_counts(database_url, wanted, since, until),
    )
    alignment = build_alignment_v3_status(readiness, features, wanted, journal_counts)
    common = {"alignment_coverage": alignment["alignment_coverage"], "sample": alignment["sample"], "interval": {"since": since.isoformat(), "until": until.isoformat()}}
    if alignment.get("ready_for_preregistered_effect_test") is not True:
        return {**base, **common, "status": "WAITING_FOR_SAMPLE", "outcome_visible": False, "ready_for_preregistered_effect_test": False}
    if alignment["alignment_coverage"].get("unaligned_signal_count") != 0:
        raise RuntimeError("v3 alignment coverage is not exact; outcome query is forbidden")

    cohort, cohort_gate = select_earliest_ready_cohort(features, wanted)
    if not cohort or cohort_gate.get("cohort_frozen") is not True:
        return {**base, **common, "cohort_gate": cohort_gate, "status": "WAITING_FOR_SAMPLE", "outcome_visible": False, "ready_for_preregistered_effect_test": False}

    outcomes = await load_closed_outcomes(database_url, cohort, wanted)
    result = analyze_preregistered_effects(cohort, outcomes, wanted)
    return {**result, **common, "cohort_gate": cohort_gate, "ready_for_preregistered_effect_test": True}


def attach_microstructure_effect_v3_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get("/v1/research/microstructure/effect-status-v3", dependencies=[Depends(require_api_key)])
    async def effect_status_v3() -> dict[str, Any]:
        try:
            config = MicrostructureConfig.from_env()
            return await build_effect_v3_status(config.database_url, list(config.symbols), config.bucket_seconds)
        except Exception as exc:
            logger.exception("microstructure v3 effect status query failed")
            return {"research_only": True, "live_strategy_mutated": False, "promotion_allowed": False, "threshold_search_allowed": False, "model_search_allowed": False, "outcome_visible": False, "effect_spec": effect_analysis_spec(), "status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)[:1000]}
