"""Preregistered v0.7.4 forward microstructure cohort.

This v2 study deliberately reuses the frozen v1 feature definitions and sample
requirements while isolating observations to day strategy v0.7.4 and to a
forward-only cohort start. It never reads outcome labels and cannot mutate live
strategy/scoring/eligibility/execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import asyncpg

from research.microstructure import alignment as v1

SPEC_VERSION = "microstructure-forward-alignment-v2"
PARENT_SPEC_VERSION = v1.SPEC_VERSION
PREREGISTERED_STRATEGY_VERSION = "0.7.4"
COHORT_START_AT = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
LOOKBACK_SECONDS = v1.LOOKBACK_SECONDS
WINDOW_SECONDS = v1.WINDOW_SECONDS
MIN_SIGNAL_SAMPLE_TOTAL = v1.MIN_SIGNAL_SAMPLE_TOTAL
MIN_SIGNAL_SAMPLE_PER_SYMBOL = v1.MIN_SIGNAL_SAMPLE_PER_SYMBOL
HYPOTHESES = v1.HYPOTHESES

# Keep the exact label-blind column/query contract from v1. The strategy version
# is supplied as $4 and is different for this independently preregistered cohort.
ALIGNMENT_SQL = v1.ALIGNMENT_SQL


def alignment_spec() -> dict[str, Any]:
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "spec_version": SPEC_VERSION,
        "parent_spec_version": PARENT_SPEC_VERSION,
        "preregistered_strategy_version": PREREGISTERED_STRATEGY_VERSION,
        "strategy_version_isolated": True,
        "cohort_start_at": COHORT_START_AT.isoformat(),
        "forward_only": True,
        "label_blind": True,
        "post_signal_data_used": False,
        "outcome_visible": False,
        "promotion_allowed": False,
        "lookback_seconds": LOOKBACK_SECONDS,
        "windows_seconds": list(WINDOW_SECONDS),
        "minimum_signal_sample": {
            "total": MIN_SIGNAL_SAMPLE_TOTAL,
            "per_symbol": MIN_SIGNAL_SAMPLE_PER_SYMBOL,
        },
        "primary_future_label": "journal.net_r_after_costs",
        "feature_definitions_frozen_from_parent": True,
        "analysis_rule": (
            "No threshold search on the forward sample. Evaluate the frozen v1 "
            "continuous feature directions first; any threshold/model discovered "
            "later requires a subsequent untouched validation period."
        ),
        "hypotheses": list(HYPOTHESES),
    }


def build_feature_rows(
    rows: Iterable[Mapping[str, Any]],
    bucket_seconds: int = 5,
) -> list[dict[str, Any]]:
    source_rows = list(rows)
    contaminated = sorted({
        str(row.get("strategy_version") or "")
        for row in source_rows
        if str(row.get("strategy_version") or "") != PREREGISTERED_STRATEGY_VERSION
    })
    if contaminated:
        raise ValueError(
            "v2 alignment strategy contamination: " + ",".join(contaminated)
        )
    features = v1.build_feature_rows(source_rows, bucket_seconds=bucket_seconds)
    for feature in features:
        feature["spec_version"] = SPEC_VERSION
    return features


def sample_readiness(
    features: Iterable[Mapping[str, Any]],
    symbols: Iterable[str],
) -> dict[str, Any]:
    return v1.sample_readiness(features, symbols)


async def load_feature_rows(
    database_url: str,
    symbols: Iterable[str],
    since: datetime,
    until: datetime,
    bucket_seconds: int = 5,
) -> list[dict[str, Any]]:
    """Load only v0.7.4 signals at/after the preregistered forward cohort start."""
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("alignment boundaries must be timezone-aware")
    effective_since = max(since.astimezone(timezone.utc), COHORT_START_AT)
    effective_until = until.astimezone(timezone.utc)
    if effective_until <= effective_since:
        return []

    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(
            ALIGNMENT_SQL,
            list(wanted),
            effective_since,
            effective_until,
            PREREGISTERED_STRATEGY_VERSION,
        )
    finally:
        await connection.close()
    return build_feature_rows(rows, bucket_seconds=bucket_seconds)
