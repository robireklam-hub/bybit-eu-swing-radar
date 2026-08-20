"""Immutable prospective storage for controlled-pullback research v2.

Only label-blind detector/comparator records are persisted. The schema contains
no outcome/PnL/return columns and first-seen records are immutable (`ON CONFLICT
DO NOTHING`). PostgreSQL mutation guards also reject UPDATE, DELETE and TRUNCATE
so the prospective sample cannot be rewritten by another database role after
capture. This module is research-only and has no live strategy path.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import asyncpg

from research.microstructure.controlled_pullback_detector_v2 import DETECTOR_ID
from research.microstructure.controlled_pullback_v2 import EXPERIMENT_ID, STRATEGY_VERSION

TABLE_NAME = "research_controlled_pullback_v2_records"
STORE_SPEC_VERSION = "controlled-pullback-prospective-store-v2"

_FORBIDDEN_OUTCOME_FIELDS = {
    "net_r",
    "gross_r",
    "pnl",
    "profit",
    "loss",
    "exit_reason",
    "closed_at",
    "future_return",
    "forward_return",
    "direction_normalized_return_5m",
    "direction_normalized_return_15m",
    "mae_15m",
    "mfe_15m",
    "target_hit",
    "stop_hit",
}

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    record_key TEXT PRIMARY KEY,
    record_class TEXT NOT NULL CHECK (record_class IN ('MOMENTUM_ONLY','CONTROLLED_PULLBACK')),
    experiment_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    detector_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long','short')),
    forward_start_utc TIMESTAMPTZ NOT NULL,
    momentum_start_at TIMESTAMPTZ NOT NULL,
    momentum_end_at TIMESTAMPTZ NOT NULL,
    momentum_trigger_at TIMESTAMPTZ NOT NULL,
    pullback_at TIMESTAMPTZ,
    trigger_at TIMESTAMPTZ NOT NULL,
    feature_payload JSONB NOT NULL,
    research_only BOOLEAN NOT NULL DEFAULT TRUE,
    label_blind BOOLEAN NOT NULL DEFAULT TRUE,
    outcome_visible BOOLEAN NOT NULL DEFAULT FALSE,
    promotion_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (experiment_id = '{EXPERIMENT_ID}'),
    CHECK (strategy_version = '{STRATEGY_VERSION}'),
    CHECK (detector_id = '{DETECTOR_ID}'),
    CHECK (research_only = TRUE),
    CHECK (label_blind = TRUE),
    CHECK (outcome_visible = FALSE),
    CHECK (promotion_allowed = FALSE)
)
"""

CREATE_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_controlled_pullback_v2_trigger
ON {TABLE_NAME}(trigger_at DESC, symbol, record_class)
"""

IMMUTABILITY_GUARD_SQL = f"""
DO $immutability$
BEGIN
    IF to_regprocedure(current_schema() || '.reject_controlled_pullback_v2_mutation()') IS NULL THEN
        EXECUTE $create_function$
            CREATE FUNCTION reject_controlled_pullback_v2_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                RAISE EXCEPTION '{TABLE_NAME} is append-only';
            END;
            $function$
        $create_function$;
    END IF;
END
$immutability$;

DO $immutability$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = '{TABLE_NAME}'::regclass
          AND tgname = 'trg_controlled_pullback_v2_no_row_mutation'
          AND NOT tgisinternal
    ) THEN
        EXECUTE 'CREATE TRIGGER trg_controlled_pullback_v2_no_row_mutation '
                'BEFORE UPDATE OR DELETE ON {TABLE_NAME} '
                'FOR EACH ROW EXECUTE FUNCTION reject_controlled_pullback_v2_mutation()';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = '{TABLE_NAME}'::regclass
          AND tgname = 'trg_controlled_pullback_v2_no_truncate'
          AND NOT tgisinternal
    ) THEN
        EXECUTE 'CREATE TRIGGER trg_controlled_pullback_v2_no_truncate '
                'BEFORE TRUNCATE ON {TABLE_NAME} '
                'FOR EACH STATEMENT EXECUTE FUNCTION reject_controlled_pullback_v2_mutation()';
    END IF;
END
$immutability$;
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    record_key, record_class, experiment_id, strategy_version, detector_id,
    symbol, direction, forward_start_utc, momentum_start_at, momentum_end_at,
    momentum_trigger_at, pullback_at, trigger_at, feature_payload,
    research_only, label_blind, outcome_visible, promotion_allowed
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,TRUE,TRUE,FALSE,FALSE
)
ON CONFLICT (record_key) DO NOTHING
"""


def store_contract() -> dict[str, Any]:
    return {
        "store_spec_version": STORE_SPEC_VERSION,
        "table_name": TABLE_NAME,
        "experiment_id": EXPERIMENT_ID,
        "strategy_version": STRATEGY_VERSION,
        "detector_id": DETECTOR_ID,
        "research_only": True,
        "label_blind": True,
        "outcome_columns_present": False,
        "outcome_visible": False,
        "promotion_allowed": False,
        "conflict_policy": "DO_NOTHING_IMMUTABLE_FIRST_SEEN",
        "database_mutation_guard": "REJECT_UPDATE_DELETE_TRUNCATE",
        "live_strategy_mutation": False,
    }


def _utc(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field} must be an ISO-8601 string or datetime")
    if dt.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _forbidden_keys(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            name = str(key).lower()
            nested_path = f"{path}.{key}" if path else str(key)
            if name in _FORBIDDEN_OUTCOME_FIELDS:
                found.append(nested_path)
            found.extend(_forbidden_keys(nested, nested_path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found.extend(_forbidden_keys(nested, f"{path}[{index}]"))
    return found


def _iso(value: Any, field: str) -> str:
    return _utc(value, field).isoformat()


def build_storage_rows(detection_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize a detector result into immutable label-blind storage records."""
    forbidden = _forbidden_keys(detection_result)
    if forbidden:
        raise ValueError(
            "prospective store received forbidden outcome fields: "
            + ", ".join(sorted(forbidden))
        )
    detector = detection_result.get("detector")
    if not isinstance(detector, Mapping):
        raise ValueError("detector contract is missing")
    invariants = {
        "detector_id": DETECTOR_ID,
        "experiment_id": EXPERIMENT_ID,
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
    }
    for field, expected in invariants.items():
        if detector.get(field) != expected:
            raise ValueError(f"detector invariant failed: {field}")
    if detection_result.get("outcome_visible") is not False:
        raise ValueError("detection result opened outcome visibility")
    if detection_result.get("promotion_allowed") is not False:
        raise ValueError("detection result opened promotion gate")
    if detection_result.get("live_strategy_mutation") is not False:
        raise ValueError("detection result opened live mutation path")

    forward_start = _iso(detection_result.get("forward_start_utc"), "forward_start_utc")
    rows: list[dict[str, Any]] = []

    for candidate in detection_result.get("momentum_candidates") or []:
        if not isinstance(candidate, Mapping):
            raise ValueError("momentum candidate is not an object")
        symbol = str(candidate.get("symbol") or "").upper()
        direction = str(candidate.get("direction") or "")
        momentum_trigger = _iso(candidate.get("momentum_trigger_at"), "momentum_trigger_at")
        record_key = (
            f"{EXPERIMENT_ID}:MOMENTUM_ONLY:{symbol}:{direction}:"
            f"{int(_utc(momentum_trigger, 'momentum_trigger_at').timestamp())}"
        )
        feature_payload = {
            key: value
            for key, value in dict(candidate).items()
            if key not in {"outcome_visible", "promotion_allowed"}
        }
        rows.append(
            {
                "record_key": record_key,
                "record_class": "MOMENTUM_ONLY",
                "experiment_id": EXPERIMENT_ID,
                "strategy_version": STRATEGY_VERSION,
                "detector_id": DETECTOR_ID,
                "symbol": symbol,
                "direction": direction,
                "forward_start_utc": forward_start,
                "momentum_start_at": _iso(candidate.get("momentum_start_at"), "momentum_start_at"),
                "momentum_end_at": _iso(candidate.get("momentum_end_at"), "momentum_end_at"),
                "momentum_trigger_at": momentum_trigger,
                "pullback_at": None,
                "trigger_at": momentum_trigger,
                "feature_payload": feature_payload,
            }
        )

    for event in detection_result.get("controlled_pullback_events") or []:
        if not isinstance(event, Mapping):
            raise ValueError("controlled-pullback event is not an object")
        if event.get("experiment_id") != EXPERIMENT_ID:
            raise ValueError("controlled-pullback event experiment identity mismatch")
        if event.get("strategy_version") != STRATEGY_VERSION:
            raise ValueError("controlled-pullback event strategy identity mismatch")
        if event.get("detector_id") != DETECTOR_ID:
            raise ValueError("controlled-pullback event detector identity mismatch")
        if event.get("research_only") is not True or event.get("label_blind") is not True:
            raise ValueError("controlled-pullback event is not label-blind research")
        if event.get("outcome_visible") is not False or event.get("promotion_allowed") is not False:
            raise ValueError("controlled-pullback event opened outcome/promotion gate")
        momentum_end = _utc(event.get("momentum_end_at"), "momentum_end_at")
        feature_payload = {
            key: value
            for key, value in dict(event).items()
            if key not in {"outcome_visible", "promotion_allowed"}
        }
        rows.append(
            {
                "record_key": str(event.get("event_key") or ""),
                "record_class": "CONTROLLED_PULLBACK",
                "experiment_id": EXPERIMENT_ID,
                "strategy_version": STRATEGY_VERSION,
                "detector_id": DETECTOR_ID,
                "symbol": str(event.get("symbol") or "").upper(),
                "direction": str(event.get("direction") or ""),
                "forward_start_utc": forward_start,
                "momentum_start_at": _iso(event.get("momentum_start_at"), "momentum_start_at"),
                "momentum_end_at": momentum_end.isoformat(),
                "momentum_trigger_at": (momentum_end + timedelta(seconds=5)).isoformat(),
                "pullback_at": _iso(event.get("pullback_at"), "pullback_at"),
                "trigger_at": _iso(event.get("trigger_at"), "trigger_at"),
                "feature_payload": feature_payload,
            }
        )

    for row in rows:
        if not row["record_key"]:
            raise ValueError("prospective record_key is missing")
        if row["direction"] not in {"long", "short"}:
            raise ValueError("prospective record direction is invalid")
        if not row["symbol"].endswith("USDC"):
            raise ValueError("prospective record is not USDC")
        if _utc(row["trigger_at"], "trigger_at") < _utc(forward_start, "forward_start_utc"):
            raise ValueError("pre-forward-start record is ineligible")
        forbidden_payload = _forbidden_keys(row["feature_payload"])
        if forbidden_payload:
            raise ValueError("feature payload contains forbidden outcome fields")
    return rows


async def install_schema(connection: asyncpg.Connection) -> None:
    await connection.execute(CREATE_TABLE_SQL)
    await connection.execute(CREATE_INDEX_SQL)
    await connection.execute(IMMUTABILITY_GUARD_SQL)


async def persist_detection_result(
    database_url: str,
    detection_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist first-seen prospective records without exposing or computing outcomes."""
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    rows = build_storage_rows(detection_result)
    connection = await asyncpg.connect(database_url)
    inserted = 0
    try:
        await install_schema(connection)
        async with connection.transaction():
            for row in rows:
                status = await connection.execute(
                    INSERT_SQL,
                    row["record_key"],
                    row["record_class"],
                    row["experiment_id"],
                    row["strategy_version"],
                    row["detector_id"],
                    row["symbol"],
                    row["direction"],
                    _utc(row["forward_start_utc"], "forward_start_utc"),
                    _utc(row["momentum_start_at"], "momentum_start_at"),
                    _utc(row["momentum_end_at"], "momentum_end_at"),
                    _utc(row["momentum_trigger_at"], "momentum_trigger_at"),
                    None if row["pullback_at"] is None else _utc(row["pullback_at"], "pullback_at"),
                    _utc(row["trigger_at"], "trigger_at"),
                    json.dumps(row["feature_payload"], sort_keys=True, default=str),
                )
                if status.endswith("1"):
                    inserted += 1
    finally:
        await connection.close()
    return {
        "store": store_contract(),
        "candidate_records": len(rows),
        "inserted_records": inserted,
        "duplicate_records": len(rows) - inserted,
        "outcome_visible": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
    }
