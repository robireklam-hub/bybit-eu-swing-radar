"""Research-only runtime cycle for controlled-pullback v2 prospective collection.

This module deliberately accepts an existing asyncpg connection so callers can
serialize the cycle with recorder writes. It never touches live strategy state,
never reads outcomes, and persists immutable first-seen research records only.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from research.microstructure.controlled_pullback_activation_v2 import activation_snapshot
from research.microstructure.controlled_pullback_detector_v2 import detect_research_events
from research.microstructure.controlled_pullback_store_v2 import (
    INSERT_SQL,
    build_storage_rows,
    install_schema,
)

RUNTIME_ID = "microstructure-controlled-pullback-runtime-v2"
LOOKBACK_SECONDS = 15 * 60

LOAD_BUCKETS_SQL = """
SELECT
    symbol,bucket_start,bucket_seconds,mid,spread_bps,
    bid_depth_5_quote,ask_depth_5_quote,
    signed_quote_flow,total_quote_volume,
    bid_added_quote,bid_removed_quote,ask_added_quote,ask_removed_quote,
    book_ready
FROM microstructure_buckets
WHERE symbol = ANY($1::text[])
  AND bucket_seconds = 5
  AND bucket_start >= $2
  AND bucket_start < $3
ORDER BY symbol,bucket_start
"""


def runtime_contract() -> dict[str, Any]:
    return {
        "runtime_id": RUNTIME_ID,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "outcome_visible": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
        "lookback_seconds": LOOKBACK_SECONDS,
        "connection_policy": "CALLER_SERIALIZES_WITH_RECORDER_DB_WRITES",
    }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("runtime timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


async def run_prospective_cycle(connection: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Detect and persist one label-blind prospective cycle on an existing DB connection."""
    contract = runtime_contract()
    snapshot = activation_snapshot()
    current = _utc(now or datetime.now(timezone.utc))
    forward_start = datetime.fromisoformat(str(snapshot["forward_start_utc"]).replace("Z", "+00:00"))
    start = max(forward_start - timedelta(seconds=120), current - timedelta(seconds=LOOKBACK_SECONDS))

    rows = await connection.fetch(
        LOAD_BUCKETS_SQL,
        ["BTCUSDC", "ETHUSDC", "SOLUSDC"],
        start,
        current,
    )
    raw_rows = [dict(row) for row in rows]
    detection = detect_research_events(raw_rows, snapshot)
    storage_rows = build_storage_rows(detection)

    await install_schema(connection)
    inserted = 0
    for row in storage_rows:
        status = await connection.execute(
            INSERT_SQL,
            row["record_key"],
            row["record_class"],
            row["experiment_id"],
            row["strategy_version"],
            row["detector_id"],
            row["symbol"],
            row["direction"],
            datetime.fromisoformat(row["forward_start_utc"]),
            datetime.fromisoformat(row["momentum_start_at"]),
            datetime.fromisoformat(row["momentum_end_at"]),
            datetime.fromisoformat(row["momentum_trigger_at"]),
            None if row["pullback_at"] is None else datetime.fromisoformat(row["pullback_at"]),
            datetime.fromisoformat(row["trigger_at"]),
            json.dumps(row["feature_payload"], sort_keys=True, default=str),
        )
        if str(status).endswith("1"):
            inserted += 1

    return {
        "runtime": contract,
        "window_start_utc": start.isoformat(),
        "window_end_utc": current.isoformat(),
        "bucket_rows": len(raw_rows),
        "momentum_candidates": len(detection.get("momentum_candidates") or []),
        "controlled_pullback_events": len(detection.get("controlled_pullback_events") or []),
        "candidate_records": len(storage_rows),
        "inserted_records": inserted,
        "duplicate_records": len(storage_rows) - inserted,
        "outcome_visible": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
    }
