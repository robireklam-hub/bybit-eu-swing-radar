"""Preregistered v0.7.5 forward microstructure cohort.

V1 and v2 remain immutable historical research cohorts. This v3 study reuses
the frozen label-blind feature definitions, hypotheses, windows and sample gates
while isolating all observations to the live day strategy v0.7.5 and to a
conservative forward-only start after exact production verification.

Research-only: no live strategy/scoring/eligibility/execution mutation path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import asyncpg

from research.microstructure import alignment as v1
from research.microstructure import alignment_v2 as v2

SPEC_VERSION = "microstructure-forward-alignment-v3"
PARENT_SPEC_VERSION = v2.SPEC_VERSION
FEATURE_DEFINITION_SOURCE = v1.SPEC_VERSION
PREREGISTERED_STRATEGY_VERSION = "0.7.5"

# v0.7.5 merge SHA 6230162e... was exact-production verified by the temporary
# verifier PR #275 before it closed at 2026-08-20T06:21:40Z. Start twenty
# seconds later so no pre-verification observation can enter the v3 cohort.
PRODUCTION_VERIFIED_BY = datetime(2026, 8, 20, 6, 21, 40, tzinfo=timezone.utc)
COHORT_START_AT = datetime(2026, 8, 20, 6, 22, 0, tzinfo=timezone.utc)
PRODUCTION_ACTIVATION_EVIDENCE = {
    "strategy_merge_sha": "6230162eaa2bc5b78a630cd2bb23a0929a65dd4f",
    "exact_production_verifier_pr": 275,
    "verified_by_utc": PRODUCTION_VERIFIED_BY.isoformat(),
    "cohort_start_rule": "strictly_after_exact_production_verification",
}

LOOKBACK_SECONDS = v1.LOOKBACK_SECONDS
WINDOW_SECONDS = v1.WINDOW_SECONDS
MIN_SIGNAL_SAMPLE_TOTAL = v1.MIN_SIGNAL_SAMPLE_TOTAL
MIN_SIGNAL_SAMPLE_PER_SYMBOL = v1.MIN_SIGNAL_SAMPLE_PER_SYMBOL
HYPOTHESES = v1.HYPOTHESES
ALIGNMENT_SQL = v1.ALIGNMENT_SQL


def alignment_spec() -> dict[str, Any]:
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "spec_version": SPEC_VERSION,
        "parent_spec_version": PARENT_SPEC_VERSION,
        "feature_definition_source": FEATURE_DEFINITION_SOURCE,
        "preregistered_strategy_version": PREREGISTERED_STRATEGY_VERSION,
        "strategy_version_isolated": True,
        "cohort_start_at": COHORT_START_AT.isoformat(),
        "production_activation_evidence": dict(PRODUCTION_ACTIVATION_EVIDENCE),
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
        "threshold_search_allowed": False,
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
            "v3 alignment strategy contamination: " + ",".join(contaminated)
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
    """Load only v0.7.5 signals at/after the preregistered v3 forward start."""
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
