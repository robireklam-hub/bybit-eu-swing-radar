"""Prospective point-in-time recorder for the frozen v0.7.5 barrier-clear cohort.

Research only. The recorder is designed to run inside the already externalized
``prospective-funnel-worker`` sidecar. It never writes live day-trade cache,
strategy state, rankings, scores, journals or execution instructions.

The first successful recorder initialization establishes a hard prospective
boundary. Parent events whose confirmed trigger closed before that boundary are
never backfilled. Outcome fields (PnL, forward return, MFE/MAE, win/loss, etc.)
are recursively stripped from every persisted JSON payload.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

import day_worker as live
from research.day_barrier_clear_rearm_v1 import (
    PARENT_STRATEGY_VERSION,
    STUDY_ID,
    parent_event_eligibility,
)
from sweep_research import classify_15m_structure

SPEC_VERSION = "day-barrier-clear-recorder-v1"
FIVE_MIN_MS = 5 * 60 * 1000
FIFTEEN_MIN_MS = 15 * 60 * 1000
OUTCOME_VISIBILITY = "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE"
TERMINAL_STATUSES = {
    "CLEARED",
    "INVALIDATED_BOUNDARY",
    "INVALIDATED_STRUCTURE",
}
FORBIDDEN_OUTCOME_KEYS = {
    "outcome",
    "label",
    "forward_return",
    "forward_return_pct",
    "mfe",
    "mae",
    "pnl",
    "profit",
    "win",
    "loss",
    "tp_hit",
    "stop_hit",
    "realized_r",
    "realized_pnl",
}

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS research_day_barrier_clear_meta (
    spec_version TEXT PRIMARY KEY,
    study_id TEXT NOT NULL,
    parent_strategy_version TEXT NOT NULL,
    prospective_start_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS research_day_barrier_clear_parents (
    parent_id TEXT PRIMARY KEY,
    spec_version TEXT NOT NULL,
    study_id TEXT NOT NULL,
    parent_strategy_version TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long','short')),
    trigger_route TEXT NOT NULL,
    trigger_event_bar_time TIMESTAMPTZ NOT NULL,
    trigger_boundary DOUBLE PRECISION NOT NULL,
    boundary_kind TEXT NOT NULL,
    barrier_price DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    last_checked_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    resolution_reason TEXT,
    eligibility_checks JSONB NOT NULL,
    parent_payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_day_barrier_clear_pending
    ON research_day_barrier_clear_parents (status, symbol, side, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_research_day_barrier_clear_seen
    ON research_day_barrier_clear_parents (first_seen_at DESC);

CREATE TABLE IF NOT EXISTS research_day_barrier_clear_clears (
    parent_id TEXT PRIMARY KEY REFERENCES research_day_barrier_clear_parents(parent_id),
    spec_version TEXT NOT NULL,
    study_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    clear_bar_time TIMESTAMPTZ NOT NULL,
    clear_close DOUBLE PRECISION NOT NULL,
    bars_to_clear INTEGER NOT NULL,
    clearance_distance DOUBLE PRECISION NOT NULL,
    clearance_atr_5m DOUBLE PRECISION,
    boundary_held BOOLEAN NOT NULL,
    structure_state_15m TEXT NOT NULL,
    fresh_geometry JSONB NOT NULL,
    context_payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_day_barrier_clear_cleared
    ON research_day_barrier_clear_clears (observed_at DESC);
"""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _as_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _sanitize(value: Any) -> Any:
    """Recursively remove any forward/outcome-bearing keys before persistence."""
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in FORBIDDEN_OUTCOME_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return value


def _trigger_event_time(candidate: Mapping[str, Any]) -> datetime | None:
    trigger = candidate.get("trigger") or {}
    route = str(trigger.get("route") or "")
    if route == "LIQUIDITY_SWEEP_RECLAIM":
        sweep = trigger.get("sweep_confirmation") or {}
        value = sweep.get("structure_shift_time_5m") or sweep.get("reclaim_time")
    else:
        value = trigger.get("event_bar_time")
    return _parse_dt(value)


def _trigger_boundary(candidate: Mapping[str, Any]) -> tuple[float | None, str]:
    trigger = candidate.get("trigger") or {}
    route = str(trigger.get("route") or "")
    if route == "LIQUIDITY_SWEEP_RECLAIM":
        sweep = trigger.get("sweep_confirmation") or {}
        try:
            return float(sweep.get("sweep_level")), "SWEEP_RECLAIM_LEVEL"
        except (TypeError, ValueError):
            return None, "SWEEP_RECLAIM_LEVEL"
    try:
        return float(trigger.get("price")), "RANGE_BREAKOUT_BOUNDARY"
    except (TypeError, ValueError):
        return None, "RANGE_BREAKOUT_BOUNDARY"


def _parent_id(candidate: Mapping[str, Any], event_time: datetime) -> str:
    trigger = candidate.get("trigger") or {}
    raw = "|".join(
        (
            SPEC_VERSION,
            STUDY_ID,
            PARENT_STRATEGY_VERSION,
            str(candidate.get("symbol") or "").upper(),
            str(candidate.get("side") or "").lower(),
            str(trigger.get("route") or "NONE"),
            _as_utc(event_time).isoformat(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parent_row_from_candidate(
    candidate: Mapping[str, Any],
    *,
    captured_at: datetime,
    prospective_start_at: datetime,
    source_commit_sha: str | None,
) -> dict[str, Any] | None:
    """Freeze one eligible parent without retrospectively backfilling old triggers."""
    captured_at = _as_utc(captured_at)
    prospective_start_at = _as_utc(prospective_start_at)
    eligibility = parent_event_eligibility(candidate)
    if not eligibility["eligible"]:
        return None
    event_time = _trigger_event_time(candidate)
    if event_time is None:
        return None
    event_close_at = event_time + timedelta(minutes=5)
    if event_close_at < prospective_start_at or event_close_at > captured_at:
        return None
    boundary, boundary_kind = _trigger_boundary(candidate)
    metrics = candidate.get("metrics") or {}
    try:
        barrier = float(metrics.get("nearest_structural_barrier"))
    except (TypeError, ValueError):
        return None
    if boundary is None or boundary <= 0 or barrier <= 0:
        return None

    safe_parent = _sanitize(dict(candidate))
    safe_parent["research_contract"] = {
        "research_only": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "derivatives_context_only": True,
        "fresh_geometry_required_after_clear": True,
        "outcome_visibility": OUTCOME_VISIBILITY,
    }
    return {
        "parent_id": _parent_id(candidate, event_time),
        "spec_version": SPEC_VERSION,
        "study_id": STUDY_ID,
        "parent_strategy_version": PARENT_STRATEGY_VERSION,
        "first_seen_at": captured_at,
        "source_commit_sha": source_commit_sha,
        "symbol": str(candidate.get("symbol") or "").upper(),
        "side": str(candidate.get("side") or "").lower(),
        "trigger_route": str((candidate.get("trigger") or {}).get("route") or "NONE"),
        "trigger_event_bar_time": event_time,
        "trigger_boundary": boundary,
        "boundary_kind": boundary_kind,
        "barrier_price": barrier,
        "eligibility_checks": _sanitize(eligibility.get("checks") or {}),
        "parent_payload": safe_parent,
    }


async def _ensure_schema(connection: Any) -> None:
    complete = bool(
        await connection.fetchval(
            """
            SELECT
                to_regclass('public.research_day_barrier_clear_meta') IS NOT NULL
                AND to_regclass('public.research_day_barrier_clear_parents') IS NOT NULL
                AND to_regclass('public.research_day_barrier_clear_clears') IS NOT NULL
                AND to_regclass('public.idx_research_day_barrier_clear_pending') IS NOT NULL
                AND to_regclass('public.idx_research_day_barrier_clear_seen') IS NOT NULL
                AND to_regclass('public.idx_research_day_barrier_clear_cleared') IS NOT NULL
            """
        )
    )
    if complete:
        return
    transaction_factory = getattr(connection, "transaction", None)
    if transaction_factory is None:
        await connection.execute(SCHEMA_SQL)
        return
    async with transaction_factory():
        await connection.execute("SET LOCAL lock_timeout = '5s'")
        await connection.execute("SET LOCAL statement_timeout = '10s'")
        await connection.execute(SCHEMA_SQL)


async def ensure_prospective_boundary(connection: Any, captured_at: datetime) -> datetime:
    captured_at = _as_utc(captured_at)
    await _ensure_schema(connection)
    await connection.execute(
        """
        INSERT INTO research_day_barrier_clear_meta (
            spec_version,study_id,parent_strategy_version,prospective_start_at
        ) VALUES ($1,$2,$3,$4)
        ON CONFLICT (spec_version) DO NOTHING
        """,
        SPEC_VERSION,
        STUDY_ID,
        PARENT_STRATEGY_VERSION,
        captured_at,
    )
    value = await connection.fetchval(
        "SELECT prospective_start_at FROM research_day_barrier_clear_meta WHERE spec_version=$1",
        SPEC_VERSION,
    )
    if value is None:
        raise RuntimeError("barrier-clear prospective boundary was not persisted")
    return _as_utc(value)


async def pending_parent_symbols(connection: Any, captured_at: datetime) -> list[str]:
    """Return symbols that must stay in the sidecar deep-universe until resolved."""
    await ensure_prospective_boundary(connection, captured_at)
    rows = await connection.fetch(
        """
        SELECT DISTINCT symbol
        FROM research_day_barrier_clear_parents
        WHERE spec_version=$1 AND status='PENDING'
        ORDER BY symbol
        """,
        SPEC_VERSION,
    )
    return [str(row["symbol"]) for row in rows]


async def _insert_parent(connection: Any, row: dict[str, Any]) -> bool:
    inserted = await connection.fetchval(
        """
        INSERT INTO research_day_barrier_clear_parents (
            parent_id,spec_version,study_id,parent_strategy_version,first_seen_at,
            source_commit_sha,symbol,side,trigger_route,trigger_event_bar_time,
            trigger_boundary,boundary_kind,barrier_price,eligibility_checks,parent_payload
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15::jsonb)
        ON CONFLICT (parent_id) DO NOTHING
        RETURNING 1
        """,
        row["parent_id"], row["spec_version"], row["study_id"],
        row["parent_strategy_version"], row["first_seen_at"], row["source_commit_sha"],
        row["symbol"], row["side"], row["trigger_route"], row["trigger_event_bar_time"],
        row["trigger_boundary"], row["boundary_kind"], row["barrier_price"],
        json.dumps(row["eligibility_checks"], ensure_ascii=False),
        json.dumps(row["parent_payload"], ensure_ascii=False),
    )
    return bool(inserted)


def _bar_close_at(bar: Any) -> datetime:
    return datetime.fromtimestamp((int(bar.start_ms) + FIVE_MIN_MS) / 1000.0, tz=timezone.utc)


def _boundary_held(side: str, close: float, boundary: float) -> bool:
    return close > boundary if side == "long" else close < boundary


def _barrier_cleared(side: str, close: float, barrier: float) -> bool:
    return close > barrier if side == "long" else close < barrier


def _opposing_structure(side: str, state: str) -> bool:
    return (side == "long" and state == "BEARISH_SHIFT") or (
        side == "short" and state == "BULLISH_SHIFT"
    )


def _confirmed_barrier_as_of(
    bars_15m: list[Any],
    *,
    side: str,
    entry: float,
    atr_15m: float,
    cutoff_start_ms: int,
) -> dict[str, Any] | None:
    bars = [
        bar for bar in bars_15m[-live.DAY_BARRIER_LOOKBACK_15M:]
        if int(bar.start_ms) + FIFTEEN_MIN_MS <= cutoff_start_ms
    ]
    left = live.DAY_BARRIER_PIVOT_LEFT
    right = live.DAY_BARRIER_PIVOT_RIGHT
    if len(bars) < left + right + 1:
        return None
    minimum_prominence = max(atr_15m * live.DAY_BARRIER_MIN_PROMINENCE_ATR, 0.0)
    candidates: list[dict[str, Any]] = []
    for index in range(left, len(bars) - right):
        pivot = bars[index]
        left_rows = bars[index - left:index]
        right_rows = bars[index + 1:index + right + 1]
        if side == "long":
            left_ref = max(row.high for row in left_rows)
            right_ref = max(row.high for row in right_rows)
            prominence = min(pivot.high - left_ref, pivot.high - right_ref)
            if not (pivot.high > left_ref and pivot.high >= right_ref):
                continue
            price = float(pivot.high)
            if prominence < minimum_prominence or price <= entry:
                continue
            swing_type = "SWING_HIGH"
        else:
            left_ref = min(row.low for row in left_rows)
            right_ref = min(row.low for row in right_rows)
            prominence = min(left_ref - pivot.low, right_ref - pivot.low)
            if not (pivot.low < left_ref and pivot.low <= right_ref):
                continue
            price = float(pivot.low)
            if prominence < minimum_prominence or price >= entry:
                continue
            swing_type = "SWING_LOW"
        candidates.append(
            {
                "price": price,
                "timeframe": "15m",
                "swing_type": swing_type,
                "pivot_start_ms": int(pivot.start_ms),
                "prominence_atr": prominence / atr_15m if atr_15m > 0 else None,
                "point_in_time_cutoff_ms": cutoff_start_ms,
            }
        )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item["price"]) if side == "long" else max(
        candidates, key=lambda item: item["price"]
    )


def fresh_geometry_as_of_clear(analysis: Any, side: str, clear_index: int) -> dict[str, Any]:
    """Recalculate geometry from the clear bar only; no parent entry/SL/TP is read."""
    bars_5m = list(analysis.bars_5m[: clear_index + 1])
    if not bars_5m:
        raise ValueError("clear_index has no 5m prefix")
    clear_bar = bars_5m[-1]
    clear_close_ms = int(clear_bar.start_ms) + FIVE_MIN_MS
    bars_15m = [
        bar for bar in analysis.bars_15m
        if int(bar.start_ms) + FIFTEEN_MIN_MS <= clear_close_ms
    ]
    atr_5m = live.atr(bars_5m, 14)
    atr_15m = live.atr(bars_15m, 14)
    entry = float(clear_bar.close)
    tick = float(analysis.instrument.tick_size)
    if entry <= 0 or atr_5m <= 0 or atr_15m <= 0:
        return {
            "status": "UNAVAILABLE",
            "reason": "POINT_IN_TIME_ATR_UNAVAILABLE",
            "as_of": _bar_close_at(clear_bar).isoformat(),
            "reference_entry": entry,
            "inherited_parent_geometry": None,
            "execution_authorized": False,
        }

    recent = bars_5m[-9:]
    if side == "long":
        stop = min(min(bar.low for bar in recent), entry - 1.2 * atr_5m)
        direction = 1.0
    else:
        stop = max(max(bar.high for bar in recent), entry + 1.2 * atr_5m)
        direction = -1.0
    risk = abs(entry - stop)
    if risk <= max(tick * 3.0, entry * 0.0002):
        return {
            "status": "UNAVAILABLE",
            "reason": "POINT_IN_TIME_RISK_TOO_SMALL",
            "as_of": _bar_close_at(clear_bar).isoformat(),
            "reference_entry": live.round_to_tick(entry, tick),
            "inherited_parent_geometry": None,
            "execution_authorized": False,
        }

    entry_low, entry_high = live.fresh_entry_zone(
        current_price=entry, atr_5m=atr_5m, side=side
    )
    cost = entry * live.DAY_ASSUMED_ROUND_TRIP_COST_BPS / 10_000.0

    def target(net_r: float) -> float:
        return entry + direction * (net_r * risk + cost)

    tp1 = target(1.0)
    tp2 = target(live.DAY_MIN_RR)
    tp3 = target(2.5)
    barrier_info = _confirmed_barrier_as_of(
        bars_15m,
        side=side,
        entry=entry,
        atr_15m=atr_15m,
        cutoff_start_ms=int(clear_bar.start_ms),
    )
    barrier = None if barrier_info is None else float(barrier_info["price"])
    barrier_before_tp2 = bool(
        barrier is not None
        and (entry < barrier < tp2 if side == "long" else tp2 < barrier < entry)
    )
    reward_reference = barrier if barrier_before_tp2 else tp2
    net_rr = max(0.0, (abs(reward_reference - entry) - cost) / max(risk, 1e-12))
    barrier_net_rr = (
        None
        if barrier is None
        else max(0.0, (abs(barrier - entry) - cost) / max(risk, 1e-12))
    )
    target_path_valid = bool(
        not barrier_before_tp2
        or (barrier_net_rr is not None and barrier_net_rr + 1e-9 >= live.DAY_MIN_RR)
    )
    execution_valid = bool(
        analysis.instrument.tradeable and (side == "long" or analysis.shortable)
    )
    structure_state = classify_15m_structure(
        bars_15m,
        clear_close_ms,
        3,
    )
    return _sanitize(
        {
            "status": "COMPLETE",
            "model": "RESEARCH_FRESH_CURRENT_GEOMETRY_V1",
            "as_of": _bar_close_at(clear_bar).isoformat(),
            "side": side,
            "reference_entry": live.round_to_tick(entry, tick),
            "entry_zone": {
                "low": live.round_to_tick(min(entry_low, entry_high), tick),
                "high": live.round_to_tick(max(entry_low, entry_high), tick),
            },
            "stop": live.round_to_tick(stop, tick),
            "targets": [
                live.round_to_tick(tp1, tick),
                live.round_to_tick(tp2, tick),
                live.round_to_tick(tp3, tick),
            ],
            "atr_5m": atr_5m,
            "atr_15m": atr_15m,
            "risk_distance": risk,
            "assumed_round_trip_cost_bps": live.DAY_ASSUMED_ROUND_TRIP_COST_BPS,
            "expected_rr_without_barrier": live.DAY_MIN_RR,
            "fresh_nearest_structural_barrier": barrier,
            "fresh_barrier_source": barrier_info,
            "barrier_before_tp2": barrier_before_tp2,
            "fresh_net_rr": net_rr,
            "target_path_valid": target_path_valid,
            "execution_valid": execution_valid,
            "structure_state_15m": structure_state,
            "inherited_parent_geometry": None,
            "research_only": True,
            "execution_authorized": False,
            "live_strategy_mutation": False,
        }
    )


def resolve_parent_against_analysis(parent: Mapping[str, Any], analysis: Any) -> dict[str, Any] | None:
    """Resolve a pending parent strictly from later CLOSED 5m bars."""
    first_seen = _parse_dt(parent.get("first_seen_at"))
    if first_seen is None:
        return None
    side = str(parent.get("side") or "")
    boundary = float(parent["trigger_boundary"])
    barrier = float(parent["barrier_price"])
    future: list[tuple[int, Any]] = [
        (index, bar)
        for index, bar in enumerate(analysis.bars_5m)
        if _bar_close_at(bar) > first_seen
    ]
    for bars_after_parent, (index, bar) in enumerate(future, start=1):
        close = float(bar.close)
        resolved_at = _bar_close_at(bar)
        if not _boundary_held(side, close, boundary):
            return {
                "status": "INVALIDATED_BOUNDARY",
                "resolved_at": resolved_at,
                "resolution_reason": "ORIGINAL_TRIGGER_OR_RECLAIM_BOUNDARY_LOST",
                "bars_to_resolution": bars_after_parent,
            }
        structure_state = classify_15m_structure(
            analysis.bars_15m,
            int(bar.start_ms) + FIVE_MIN_MS,
            3,
        )
        if _opposing_structure(side, structure_state):
            return {
                "status": "INVALIDATED_STRUCTURE",
                "resolved_at": resolved_at,
                "resolution_reason": "OPPOSING_CLOSED_15M_STRUCTURE_SHIFT",
                "bars_to_resolution": bars_after_parent,
                "structure_state_15m": structure_state,
            }
        if _barrier_cleared(side, close, barrier):
            clearance = abs(close - barrier)
            atr_5m = live.atr(list(analysis.bars_5m[: index + 1]), 14)
            return {
                "status": "CLEARED",
                "resolved_at": resolved_at,
                "resolution_reason": "FIRST_LATER_CLOSED_5M_BARRIER_CLEAR",
                "bars_to_resolution": bars_after_parent,
                "clear_bar_time": datetime.fromtimestamp(
                    int(bar.start_ms) / 1000.0, tz=timezone.utc
                ),
                "clear_close": close,
                "clearance_distance": clearance,
                "clearance_atr_5m": clearance / atr_5m if atr_5m > 0 else None,
                "structure_state_15m": structure_state,
                "fresh_geometry": fresh_geometry_as_of_clear(analysis, side, index),
            }
    return None


async def _load_pending_parents(connection: Any) -> list[dict[str, Any]]:
    rows = await connection.fetch(
        """
        SELECT parent_id,first_seen_at,symbol,side,trigger_boundary,barrier_price,status
        FROM research_day_barrier_clear_parents
        WHERE spec_version=$1 AND status='PENDING'
        ORDER BY first_seen_at,parent_id
        """,
        SPEC_VERSION,
    )
    return [dict(row) for row in rows]


async def _persist_resolution(
    connection: Any,
    parent: Mapping[str, Any],
    resolution: Mapping[str, Any],
    *,
    captured_at: datetime,
    source_commit_sha: str | None,
) -> bool:
    parent_id = str(parent["parent_id"])
    status = str(resolution["status"])
    if status not in TERMINAL_STATUSES:
        return False
    updated = await connection.fetchval(
        """
        UPDATE research_day_barrier_clear_parents
        SET status=$2,last_checked_at=$3,resolved_at=$4,resolution_reason=$5
        WHERE parent_id=$1 AND status='PENDING'
        RETURNING 1
        """,
        parent_id,
        status,
        captured_at,
        resolution.get("resolved_at"),
        resolution.get("resolution_reason"),
    )
    if not updated:
        return False
    if status != "CLEARED":
        return True
    geometry = _sanitize(resolution.get("fresh_geometry") or {})
    context = _sanitize(
        {
            "research_only": True,
            "execution_authorized": False,
            "live_strategy_mutation": False,
            "derivatives_context_only": True,
            "outcome_visibility": OUTCOME_VISIBILITY,
            "resolution_reason": resolution.get("resolution_reason"),
        }
    )
    await connection.execute(
        """
        INSERT INTO research_day_barrier_clear_clears (
            parent_id,spec_version,study_id,observed_at,source_commit_sha,
            clear_bar_time,clear_close,bars_to_clear,clearance_distance,
            clearance_atr_5m,boundary_held,structure_state_15m,fresh_geometry,context_payload
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb)
        ON CONFLICT (parent_id) DO NOTHING
        """,
        parent_id,
        SPEC_VERSION,
        STUDY_ID,
        captured_at,
        source_commit_sha,
        resolution["clear_bar_time"],
        resolution["clear_close"],
        resolution["bars_to_resolution"],
        resolution["clearance_distance"],
        resolution.get("clearance_atr_5m"),
        True,
        str(resolution.get("structure_state_15m") or "UNKNOWN"),
        json.dumps(geometry, ensure_ascii=False),
        json.dumps(context, ensure_ascii=False),
    )
    return True


async def _status(
    connection: Any,
    *,
    prospective_start_at: datetime,
    captured_at: datetime,
    source_commit_sha: str | None,
    discovered: int,
    inserted: int,
    resolved: Counter[str],
    required_symbols: Iterable[str],
) -> dict[str, Any]:
    rows = await connection.fetch(
        """
        SELECT status,side,symbol,COUNT(*)::int AS count
        FROM research_day_barrier_clear_parents
        WHERE spec_version=$1
        GROUP BY status,side,symbol
        ORDER BY status,side,symbol
        """,
        SPEC_VERSION,
    )
    status_counts: Counter[str] = Counter()
    side_counts: Counter[str] = Counter()
    symbols: set[str] = set()
    total = 0
    for row in rows:
        count = int(row["count"])
        total += count
        status_counts[str(row["status"])] += count
        side_counts[str(row["side"])] += count
        symbols.add(str(row["symbol"]))
    clear_count = int(
        await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_day_barrier_clear_clears WHERE spec_version=$1",
            SPEC_VERSION,
        )
        or 0
    )
    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "outcome_labels_stored": False,
        "outcome_visibility": OUTCOME_VISIBILITY,
        "spec_version": SPEC_VERSION,
        "study_id": STUDY_ID,
        "parent_strategy_version": PARENT_STRATEGY_VERSION,
        "prospective_start_at": _as_utc(prospective_start_at).isoformat(),
        "captured_at": _as_utc(captured_at).isoformat(),
        "source_commit_sha": source_commit_sha,
        "execution_mode": "SHARED_STANDALONE_RESEARCH_SIDECAR",
        "live_worker_mutation": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "derivatives_context_only": True,
        "current_run": {
            "eligible_parent_candidates": discovered,
            "inserted_new_parents": inserted,
            "resolved_cleared": resolved.get("CLEARED", 0),
            "resolved_boundary_invalidations": resolved.get("INVALIDATED_BOUNDARY", 0),
            "resolved_structure_invalidations": resolved.get("INVALIDATED_STRUCTURE", 0),
            "forced_tracking_symbols": sorted(set(required_symbols)),
        },
        "cumulative": {
            "parent_events": total,
            "pending_parents": status_counts.get("PENDING", 0),
            "cleared_parents": status_counts.get("CLEARED", 0),
            "boundary_invalidated_parents": status_counts.get("INVALIDATED_BOUNDARY", 0),
            "structure_invalidated_parents": status_counts.get("INVALIDATED_STRUCTURE", 0),
            "clear_rows": clear_count,
            "side_parent_counts": dict(sorted(side_counts.items())),
            "symbols_observed": len(symbols),
            "symbol_list": sorted(symbols),
        },
        "notes": [
            "The parent cohort is pinned to day strategy v0.7.5 even though the live strategy is newer.",
            "Only parent trigger bars closing on/after the prospective boundary can enter the cohort.",
            "Only later fully closed 5m bars can clear the original structural barrier.",
            "A lost original breakout/reclaim boundary or opposing closed 15m shift terminates the parent before clear.",
            "Fresh geometry is reconstructed at the clear bar from point-in-time candles; parent entry/stop/targets are never inherited.",
            "No forward return, PnL, MFE/MAE, win/loss or target/stop outcome is stored.",
        ],
    }


async def persist_day_barrier_clear_rearm(
    connection: Any,
    analyses: Iterable[Any],
    *,
    captured_at: datetime,
    source_commit_sha: str | None,
    required_symbols: Iterable[str] = (),
) -> dict[str, Any]:
    """Persist one prospective parent-discovery + pending-resolution cycle."""
    captured_at = _as_utc(captured_at)
    prospective_start_at = await ensure_prospective_boundary(connection, captured_at)
    analysis_rows = list(analyses)
    discovered_rows: list[dict[str, Any]] = []
    for analysis in analysis_rows:
        for side in ("long", "short"):
            candidate = live.build_day_candidate(
                analysis,
                side,
                captured_at,
                strategy_version=PARENT_STRATEGY_VERSION,
            )
            if candidate is None:
                continue
            row = parent_row_from_candidate(
                candidate,
                captured_at=captured_at,
                prospective_start_at=prospective_start_at,
                source_commit_sha=source_commit_sha,
            )
            if row is not None:
                discovered_rows.append(row)

    inserted = 0
    for row in discovered_rows:
        if await _insert_parent(connection, row):
            inserted += 1

    by_symbol = {str(item.instrument.symbol).upper(): item for item in analysis_rows}
    resolved: Counter[str] = Counter()
    for parent in await _load_pending_parents(connection):
        analysis = by_symbol.get(str(parent["symbol"]).upper())
        if analysis is None:
            continue
        resolution = resolve_parent_against_analysis(parent, analysis)
        if resolution is None:
            await connection.execute(
                "UPDATE research_day_barrier_clear_parents SET last_checked_at=$2 WHERE parent_id=$1 AND status='PENDING'",
                parent["parent_id"],
                captured_at,
            )
            continue
        if await _persist_resolution(
            connection,
            parent,
            resolution,
            captured_at=captured_at,
            source_commit_sha=source_commit_sha,
        ):
            resolved[str(resolution["status"])] += 1

    return await _status(
        connection,
        prospective_start_at=prospective_start_at,
        captured_at=captured_at,
        source_commit_sha=source_commit_sha,
        discovered=len(discovered_rows),
        inserted=inserted,
        resolved=resolved,
        required_symbols=required_symbols,
    )


__all__ = [
    "FORBIDDEN_OUTCOME_KEYS",
    "OUTCOME_VISIBILITY",
    "SCHEMA_SQL",
    "SPEC_VERSION",
    "ensure_prospective_boundary",
    "fresh_geometry_as_of_clear",
    "parent_row_from_candidate",
    "pending_parent_symbols",
    "persist_day_barrier_clear_rearm",
    "resolve_parent_against_analysis",
]
