"""Research-only outcome-bearing status for the preregistered v0.7.5 cohort."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from fastapi import Depends, FastAPI

from app.research_microstructure_alignment_v3_api import (
    _load_v3_journal_signal_counts,
    build_alignment_v3_status,
)
from research.microstructure.alignment_v3 import COHORT_START_AT, load_feature_rows
from research.microstructure.collector import MicrostructureConfig
from research.microstructure.effect_analysis_v3 import effect_analysis_spec
from research.microstructure.effect_test_v3 import (
    analyze_preregistered_effects,
    load_closed_outcomes,
    select_earliest_ready_cohort,
)
from research.microstructure.readiness import get_readiness

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("effect boundary timestamp is missing")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("effect boundary timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


async def build_effect_v3_status(
    database_url: str,
    symbols: tuple[str, ...] | list[str],
    bucket_seconds: int,
) -> dict[str, Any]:
    """Open v3 outcomes only after every frozen pre-outcome gate is satisfied."""
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    readiness_payload = await get_readiness(database_url, wanted, bucket_seconds)
    base = {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "threshold_search_allowed": False,
        "model_search_allowed": False,
        "effect_spec": effect_analysis_spec(),
    }
    if readiness_payload.get("ready_for_forward_feature_analysis") is not True:
        return {
            **base,
            "status": "WAITING_FOR_DATA_QUALITY",
            "outcome_visible": False,
            "ready_for_preregistered_effect_test": False,
        }

    readiness_symbols = readiness_payload.get("symbols") or []
    first_bucket_values = [
        item.get("first_bucket_at")
        for item in readiness_symbols
        if isinstance(item, Mapping) and item.get("first_bucket_at")
    ]
    if len(first_bucket_values) != len(wanted):
        raise ValueError("readiness first_bucket_at coverage is incomplete")
    since = max(max(_parse_dt(value) for value in first_bucket_values), COHORT_START_AT)
    until = _parse_dt(readiness_payload.get("checked_at"))

    features, journal_signal_counts = await asyncio.gather(
        load_feature_rows(database_url, wanted, since, until, bucket_seconds=bucket_seconds),
        _load_v3_journal_signal_counts(database_url, wanted, since, until),
    )
    alignment_payload = build_alignment_v3_status(
        readiness_payload,
        features,
        wanted,
        journal_signal_counts,
    )
    interval = {"since": since.isoformat(), "until": until.isoformat()}
    if alignment_payload.get("ready_for_preregistered_effect_test") is not True:
        return {
            **base,
            "status": "WAITING_FOR_SAMPLE",
            "outcome_visible": False,
            "ready_for_preregistered_effect_test": False,
            "alignment_coverage": alignment_payload["alignment_coverage"],
            "sample": alignment_payload["sample"],
            "interval": interval,
        }

    if alignment_payload["alignment_coverage"].get("unaligned_signal_count") != 0:
        raise RuntimeError("v3 alignment coverage is not exact; outcome query is forbidden")

    cohort, cohort_gate = select_earliest_ready_cohort(features, wanted)
    if not cohort or cohort_gate.get("cohort_frozen") is not True:
        return {
            **base,
            "status": "WAITING_FOR_SAMPLE",
            "outcome_visible": False,
            "ready_for_preregistered_effect_test": False,
            "alignment_coverage": alignment_payload["alignment_coverage"],
            "sample": alignment_payload["sample"],
            "cohort_gate": cohort_gate,
            "interval": interval,
        }

    outcomes = await load_closed_outcomes(database_url, cohort)
    result = analyze_preregistered_effects(cohort, outcomes, wanted)
    result["cohort_gate"] = cohort_gate
    result["alignment_coverage"] = alignment_payload["alignment_coverage"]
    result["sample"] = alignment_payload["sample"]
    result["interval"] = interval
    result["ready_for_preregistered_effect_test"] = True
    return result


def attach_microstructure_effect_v3_research(
    app: FastAPI,
    require_api_key: Callable[..., Any],
) -> None:
    @app.get(
        "/v1/research/microstructure/effect-status-v3",
        dependencies=[Depends(require_api_key)],
    )
    async def effect_status_v3() -> dict[str, Any]:
        try:
            config = MicrostructureConfig.from_env()
            return await build_effect_v3_status(
                config.database_url,
                list(config.symbols),
                config.bucket_seconds,
            )
        except Exception as exc:
            logger.exception("microstructure v3 effect status query failed")
            return {
                "research_only": True,
                "live_strategy_mutated": False,
                "promotion_allowed": False,
                "threshold_search_allowed": False,
                "model_search_allowed": False,
                "outcome_visible": False,
                "effect_spec": effect_analysis_spec(),
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
