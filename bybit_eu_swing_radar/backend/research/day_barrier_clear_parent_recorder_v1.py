"""Prospective label-blind parent recorder for day-barrier-clear-rearm-v1.

This module freezes eligible v0.7.5 parent events at first observation. It does
not observe forward outcomes, authorize execution, or mutate the live strategy.
The first successful recorder run establishes a prospective boundary; parent
triggers older than that boundary are never backfilled.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from research.day_barrier_clear_rearm_v1 import STUDY_ID, parent_event_eligibility
from research.research_governance import trial_fingerprint, trial_manifest

PARENT_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS day_barrier_clear_rearm_v1_meta (
    study TEXT PRIMARY KEY,
    prospective_start_at TIMESTAMPTZ NOT NULL,
    trial_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS day_barrier_clear_rearm_v1_parent (
    event_key TEXT PRIMARY KEY,
    study TEXT NOT NULL,
    parent_strategy_version TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    parent_event_time TIMESTAMPTZ NOT NULL,
    trigger_route TEXT NOT NULL,
    trigger_price DOUBLE PRECISION,
    frozen_barrier_price DOUBLE PRECISION NOT NULL,
    setup_score DOUBLE PRECISION NOT NULL,
    expansion_score DOUBLE PRECISION NOT NULL,
    side_direction_score DOUBLE PRECISION NOT NULL,
    quality_score DOUBLE PRECISION NOT NULL,
    rr_without_barrier DOUBLE PRECISION NOT NULL,
    tradeable BOOLEAN NOT NULL,
    shortable BOOLEAN NOT NULL,
    spread_bps DOUBLE PRECISION,
    volume_ratio_5m DOUBLE PRECISION,
    volume_ratio_15m DOUBLE PRECISION,
    structure_15m TEXT,
    structure_1h TEXT,
    derivatives_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    snapshot_payload JSONB NOT NULL,
    research_only BOOLEAN NOT NULL DEFAULT TRUE,
    execution_authorized BOOLEAN NOT NULL DEFAULT FALSE,
    outcome_visibility TEXT NOT NULL,
    trial_fingerprint TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_day_barrier_clear_rearm_parent_time
    ON day_barrier_clear_rearm_v1_parent (parent_event_time DESC);
CREATE INDEX IF NOT EXISTS idx_day_barrier_clear_rearm_parent_symbol_side
    ON day_barrier_clear_rearm_v1_parent (symbol, side, parent_event_time DESC);
"""

FORBIDDEN_OUTCOME_KEYS = {
    "outcome", "label", "forward_return", "mfe", "mae", "pnl", "profit",
    "win", "loss", "tp_hit", "stop_hit",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, str) and value.strip():
        try:
            return _utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_time(candidate: Mapping[str, Any]) -> datetime | None:
    trigger = candidate.get("trigger") or {}
    if not isinstance(trigger, Mapping):
        return None
    direct = _parse_time(trigger.get("event_bar_time"))
    if direct is not None:
        return direct
    sweep = trigger.get("sweep_confirmation") or {}
    if isinstance(sweep, Mapping):
        return _parse_time(sweep.get("sweep_time"))
    return None


def _event_key(candidate: Mapping[str, Any], event_time: datetime, barrier: float) -> str:
    raw = "|".join((
        STUDY_ID,
        str(candidate.get("strategy_version") or ""),
        str(candidate.get("symbol") or "").upper(),
        str(candidate.get("side") or "").lower(),
        event_time.isoformat(),
        f"{barrier:.12g}",
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sanitize_context(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_context(item)
            for key, item in value.items()
            if str(key).lower() not in FORBIDDEN_OUTCOME_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_context(item) for item in value]
    return value


def build_parent_record(
    candidate: Mapping[str, Any],
    *,
    captured_at: datetime,
    prospective_start_at: datetime,
    source_commit_sha: str | None,
) -> dict[str, Any] | None:
    """Return an immutable, outcome-free parent record or None when inadmissible."""
    captured_at = _utc(captured_at)
    prospective_start_at = _utc(prospective_start_at)
    eligibility = parent_event_eligibility(candidate)
    if not eligibility["eligible"]:
        return None

    event_time = _event_time(candidate)
    if event_time is None or event_time < prospective_start_at or event_time > captured_at:
        return None

    metrics = candidate.get("metrics") or {}
    trigger = candidate.get("trigger") or {}
    if not isinstance(metrics, Mapping) or not isinstance(trigger, Mapping):
        return None
    barrier = _number(metrics.get("nearest_structural_barrier"))
    rr_without_barrier = _number(metrics.get("expected_rr_without_barrier"))
    if barrier is None or barrier <= 0 or rr_without_barrier is None:
        return None

    manifest = trial_manifest(STUDY_ID)
    fingerprint = trial_fingerprint(STUDY_ID)
    derivatives = _sanitize_context(candidate.get("derivatives") or {})
    payload = {
        "study": STUDY_ID,
        "trial_manifest": manifest,
        "trial_fingerprint": fingerprint,
        "label_free": True,
        "parent_strategy_version": "0.7.5",
        "captured_at": captured_at.isoformat(),
        "parent_event_time": event_time.isoformat(),
        "source_commit_sha": source_commit_sha,
        "symbol": str(candidate.get("symbol") or "").upper(),
        "side": str(candidate.get("side") or "").lower(),
        "trigger": {
            "route": str(trigger.get("route") or "NONE"),
            "price": _number(trigger.get("price")),
            "event_bar_time": event_time.isoformat(),
            "boundary_held_at_capture": trigger.get("boundary_held"),
        },
        "frozen_barrier_price": barrier,
        "scores": {
            "setup_score": _number(candidate.get("setup_score")),
            "expansion_score": _number(candidate.get("expansion_score")),
            "side_direction_score": _number(candidate.get("side_direction_score")),
            "quality_score": _number(candidate.get("quality_score")),
            "rr_without_barrier": rr_without_barrier,
        },
        "context": {
            "tradeable": candidate.get("tradeable") is True,
            "shortable": candidate.get("shortable") is True,
            "spread_bps": _number(metrics.get("spread_bps")),
            "volume_ratio_5m": _number(metrics.get("volume_ratio_5m")),
            "volume_ratio_15m": _number(metrics.get("volume_ratio_15m")),
            "structure_15m": candidate.get("structure_15m"),
            "structure_1h": candidate.get("structure_1h"),
            "derivatives": derivatives,
            "derivatives_context_only": True,
        },
        "fresh_geometry_required_after_clear": True,
        "execution_authorized": False,
        "research_only": True,
        "outcome_visibility": "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE",
    }
    if any(key in json.dumps(payload).lower() for key in ('"pnl"', '"mfe"', '"mae"', '"forward_return"')):
        raise ValueError("forbidden outcome field escaped parent-record sanitizer")

    return {
        "event_key": _event_key(candidate, event_time, barrier),
        "study": STUDY_ID,
        "parent_strategy_version": "0.7.5",
        "captured_at": captured_at,
        "source_commit_sha": source_commit_sha,
        "symbol": payload["symbol"],
        "side": payload["side"],
        "parent_event_time": event_time,
        "trigger_route": payload["trigger"]["route"],
        "trigger_price": payload["trigger"]["price"],
        "frozen_barrier_price": barrier,
        "setup_score": float(candidate["setup_score"]),
        "expansion_score": float(candidate["expansion_score"]),
        "side_direction_score": float(candidate["side_direction_score"]),
        "quality_score": float(candidate["quality_score"]),
        "rr_without_barrier": rr_without_barrier,
        "tradeable": candidate.get("tradeable") is True,
        "shortable": candidate.get("shortable") is True,
        "spread_bps": _number(metrics.get("spread_bps")),
        "volume_ratio_5m": _number(metrics.get("volume_ratio_5m")),
        "volume_ratio_15m": _number(metrics.get("volume_ratio_15m")),
        "structure_15m": candidate.get("structure_15m"),
        "structure_1h": candidate.get("structure_1h"),
        "derivatives_context": derivatives,
        "snapshot_payload": payload,
        "research_only": True,
        "execution_authorized": False,
        "outcome_visibility": payload["outcome_visibility"],
        "trial_fingerprint": fingerprint,
    }


async def ensure_schema_and_boundary(connection: Any, captured_at: datetime) -> datetime:
    captured_at = _utc(captured_at)
    await connection.execute(PARENT_SCHEMA_SQL)
    fingerprint = trial_fingerprint(STUDY_ID)
    await connection.execute(
        """
        INSERT INTO day_barrier_clear_rearm_v1_meta (study, prospective_start_at, trial_fingerprint)
        VALUES ($1, $2, $3)
        ON CONFLICT (study) DO NOTHING
        """,
        STUDY_ID,
        captured_at,
        fingerprint,
    )
    row = await connection.fetchrow(
        "SELECT prospective_start_at, trial_fingerprint FROM day_barrier_clear_rearm_v1_meta WHERE study = $1",
        STUDY_ID,
    )
    if row is None:
        raise RuntimeError("barrier-clear prospective boundary was not persisted")
    if str(row["trial_fingerprint"]) != fingerprint:
        raise RuntimeError("barrier-clear trial fingerprint mismatch; frozen cohort changed")
    return _utc(row["prospective_start_at"])


async def insert_parent_record(connection: Any, row: Mapping[str, Any]) -> bool:
    inserted = await connection.fetchval(
        """
        INSERT INTO day_barrier_clear_rearm_v1_parent (
            event_key,study,parent_strategy_version,captured_at,source_commit_sha,
            symbol,side,parent_event_time,trigger_route,trigger_price,frozen_barrier_price,
            setup_score,expansion_score,side_direction_score,quality_score,rr_without_barrier,
            tradeable,shortable,spread_bps,volume_ratio_5m,volume_ratio_15m,structure_15m,
            structure_1h,derivatives_context,snapshot_payload,research_only,execution_authorized,
            outcome_visibility,trial_fingerprint
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
            $20,$21,$22,$23,$24::jsonb,$25::jsonb,$26,$27,$28,$29
        )
        ON CONFLICT (event_key) DO NOTHING
        RETURNING 1
        """,
        row["event_key"], row["study"], row["parent_strategy_version"], row["captured_at"],
        row["source_commit_sha"], row["symbol"], row["side"], row["parent_event_time"],
        row["trigger_route"], row["trigger_price"], row["frozen_barrier_price"],
        row["setup_score"], row["expansion_score"], row["side_direction_score"],
        row["quality_score"], row["rr_without_barrier"], row["tradeable"], row["shortable"],
        row["spread_bps"], row["volume_ratio_5m"], row["volume_ratio_15m"],
        row["structure_15m"], row["structure_1h"],
        json.dumps(row["derivatives_context"], ensure_ascii=True),
        json.dumps(row["snapshot_payload"], ensure_ascii=True),
        row["research_only"], row["execution_authorized"], row["outcome_visibility"],
        row["trial_fingerprint"],
    )
    return bool(inserted)


async def persist_parent_batch(
    connection: Any,
    candidates: Iterable[Mapping[str, Any]],
    *,
    captured_at: datetime,
    source_commit_sha: str | None,
) -> dict[str, Any]:
    prospective_start_at = await ensure_schema_and_boundary(connection, captured_at)
    admitted = 0
    inserted = 0
    for candidate in candidates:
        row = build_parent_record(
            candidate,
            captured_at=captured_at,
            prospective_start_at=prospective_start_at,
            source_commit_sha=source_commit_sha,
        )
        if row is None:
            continue
        admitted += 1
        if await insert_parent_record(connection, row):
            inserted += 1
    total = int(await connection.fetchval(
        "SELECT COUNT(*) FROM day_barrier_clear_rearm_v1_parent WHERE study = $1", STUDY_ID
    ) or 0)
    return {
        "status": "COMPLETE",
        "study": STUDY_ID,
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "parent_strategy_version": "0.7.5",
        "prospective_start_at": prospective_start_at.isoformat(),
        "captured_at": _utc(captured_at).isoformat(),
        "admitted_this_run": admitted,
        "inserted_this_run": inserted,
        "total_frozen_parents": total,
        "trial_fingerprint": trial_fingerprint(STUDY_ID),
        "outcome_visibility": "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE",
    }
