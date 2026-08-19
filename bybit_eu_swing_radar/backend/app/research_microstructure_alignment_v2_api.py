"""Research-only production status for the preregistered v0.7.4 microstructure cohort."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

import asyncpg
from fastapi import Depends, FastAPI

from app.microstructure_research import build_alignment_coverage
from research.microstructure.alignment_v2 import (
    COHORT_START_AT,
    PREREGISTERED_STRATEGY_VERSION,
    alignment_spec,
    load_feature_rows,
    sample_readiness,
)
from research.microstructure.collector import MicrostructureConfig
from research.microstructure.readiness import get_readiness

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("alignment boundary timestamp is missing")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("alignment boundary timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


async def _load_v2_journal_signal_counts(
    database_url: str,
    symbols: Iterable[str],
    since: datetime,
    until: datetime,
) -> dict[str, int]:
    """Count only v0.7.4 signals inside the independently preregistered v2 cohort."""
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    counts = {symbol: 0 for symbol in wanted}
    effective_since = max(since.astimezone(timezone.utc), COHORT_START_AT)
    effective_until = until.astimezone(timezone.utc)
    if effective_until <= effective_since:
        return counts

    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(
            """
            SELECT symbol, COUNT(*)::bigint AS signal_count
            FROM day_trade_signal_journal
            WHERE symbol = ANY($1::text[])
              AND opened_at >= $2
              AND opened_at < $3
              AND strategy_version = $4
            GROUP BY symbol
            ORDER BY symbol
            """,
            list(wanted),
            effective_since,
            effective_until,
            PREREGISTERED_STRATEGY_VERSION,
        )
    finally:
        await connection.close()
    for row in rows:
        symbol = str(row["symbol"]).upper()
        if symbol in counts:
            counts[symbol] = int(row["signal_count"] or 0)
    return counts


def build_alignment_v2_status(
    readiness_payload: Mapping[str, Any],
    features: Iterable[Mapping[str, Any]],
    symbols: Iterable[str],
    journal_signal_counts: Mapping[str, int],
) -> dict[str, Any]:
    """Combine frozen v2 data-quality, coverage and sample gates without outcomes."""
    wanted = list(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    rows = list(features)
    sample = sample_readiness(rows, wanted)
    coverage = build_alignment_coverage(rows, wanted, journal_signal_counts)
    data_quality_ready = readiness_payload.get("ready_for_forward_feature_analysis") is True
    alignment_coverage_ready = coverage["status"] == "ALIGNED"
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "post_signal_data_used": False,
        "outcome_visible": False,
        "promotion_allowed": False,
        "spec": alignment_spec(),
        "preregistered_strategy_version": PREREGISTERED_STRATEGY_VERSION,
        "strategy_version_isolated": True,
        "data_quality_ready": data_quality_ready,
        "alignment_coverage_ready": alignment_coverage_ready,
        "alignment_coverage": coverage,
        "sample": sample,
        "ready_for_preregistered_effect_test": (
            data_quality_ready
            and alignment_coverage_ready
            and sample["ready_for_preregistered_effect_test"]
        ),
    }


def attach_microstructure_alignment_v2_research(
    app: FastAPI,
    require_api_key: Callable[..., Any],
) -> None:
    @app.get(
        "/v1/research/microstructure/alignment-status-v2",
        dependencies=[Depends(require_api_key)],
    )
    async def alignment_status_v2() -> dict[str, Any]:
        """Report label-blind v0.7.4 prospective sample readiness only."""
        try:
            config = MicrostructureConfig.from_env()
            readiness_payload = await get_readiness(
                config.database_url,
                config.symbols,
                config.bucket_seconds,
            )
            empty_counts = {symbol: 0 for symbol in config.symbols}
            if readiness_payload.get("ready_for_forward_feature_analysis") is not True:
                return build_alignment_v2_status(
                    readiness_payload,
                    [],
                    config.symbols,
                    empty_counts,
                )

            readiness_symbols = readiness_payload.get("symbols") or []
            first_bucket_values = [
                item.get("first_bucket_at")
                for item in readiness_symbols
                if isinstance(item, Mapping) and item.get("first_bucket_at")
            ]
            if len(first_bucket_values) != len(config.symbols):
                raise ValueError("readiness first_bucket_at coverage is incomplete")
            since = max(_parse_dt(value) for value in first_bucket_values)
            until = _parse_dt(readiness_payload.get("checked_at"))
            features, journal_signal_counts = await asyncio.gather(
                load_feature_rows(
                    config.database_url,
                    config.symbols,
                    since,
                    until,
                    bucket_seconds=config.bucket_seconds,
                ),
                _load_v2_journal_signal_counts(
                    config.database_url,
                    config.symbols,
                    since,
                    until,
                ),
            )
            payload = build_alignment_v2_status(
                readiness_payload,
                features,
                config.symbols,
                journal_signal_counts,
            )
            payload["interval"] = {
                "since": max(since, COHORT_START_AT).isoformat(),
                "until": until.isoformat(),
            }
            return payload
        except Exception as exc:
            logger.exception("microstructure v2 alignment status query failed")
            return {
                "research_only": True,
                "live_strategy_mutated": False,
                "label_blind": True,
                "post_signal_data_used": False,
                "outcome_visible": False,
                "promotion_allowed": False,
                "spec": alignment_spec(),
                "preregistered_strategy_version": PREREGISTERED_STRATEGY_VERSION,
                "strategy_version_isolated": True,
                "ready_for_preregistered_effect_test": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
