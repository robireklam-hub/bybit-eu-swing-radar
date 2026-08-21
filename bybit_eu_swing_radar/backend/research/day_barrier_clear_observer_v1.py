"""Prospective closed-5m barrier-clear observer for day-barrier-clear-rearm-v1.

Consumes only already-frozen v0.7.5 parent rows. A parent can terminate when its
original breakout/reclaim boundary is lost, when a later fully closed 15m
structure shifts against the side, or when the first later fully closed 5m bar
clears the frozen barrier. Clear-time entry/stop/targets/target-path/net-R are
reconstructed from candles available at that clear bar; stale parent geometry
is never read or inherited.

No forward outcome, PnL, MFE/MAE, win/loss or trade authorization is stored.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import day_worker as live
from research.day_barrier_clear_context_v1 import CONTEXT_VERSION, build_clear_context_snapshot
from research.day_barrier_clear_rearm_v1 import STUDY_ID
from research.research_governance import trial_fingerprint
from sweep_research import classify_15m_structure

OBSERVER_VERSION = "day-barrier-clear-observer-v1"
FIVE_MIN_MS = 5 * 60 * 1000
FIFTEEN_MIN_MS = 15 * 60 * 1000
OUTCOME_VISIBILITY = "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE"
TERMINAL = {"CLEARED", "INVALIDATED_BOUNDARY", "INVALIDATED_STRUCTURE"}
FORBIDDEN_OUTCOME_KEYS = {
    "outcome", "label", "forward_return", "forward_return_pct", "mfe", "mae",
    "pnl", "profit", "win", "loss", "tp_hit", "stop_hit", "realized_r",
    "realized_pnl",
}

OBSERVER_SCHEMA_SQL = r"""
ALTER TABLE day_barrier_clear_rearm_v1_parent
    ADD COLUMN IF NOT EXISTS trigger_boundary DOUBLE PRECISION;
ALTER TABLE day_barrier_clear_rearm_v1_parent
    ADD COLUMN IF NOT EXISTS boundary_kind TEXT;
ALTER TABLE day_barrier_clear_rearm_v1_parent
    ADD COLUMN IF NOT EXISTS resolution_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE day_barrier_clear_rearm_v1_parent
    ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;
ALTER TABLE day_barrier_clear_rearm_v1_parent
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE day_barrier_clear_rearm_v1_parent
    ADD COLUMN IF NOT EXISTS resolution_reason TEXT;

UPDATE day_barrier_clear_rearm_v1_parent
SET trigger_boundary = trigger_price,
    boundary_kind = 'RANGE_BREAKOUT_BOUNDARY'
WHERE trigger_boundary IS NULL
  AND trigger_route = 'CLOSED_5M_RANGE_BREAKOUT'
  AND trigger_price IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_day_barrier_clear_rearm_parent_pending
    ON day_barrier_clear_rearm_v1_parent (resolution_status, symbol, side, captured_at);

CREATE TABLE IF NOT EXISTS day_barrier_clear_rearm_v1_clear (
    event_key TEXT PRIMARY KEY REFERENCES day_barrier_clear_rearm_v1_parent(event_key),
    study TEXT NOT NULL,
    observer_version TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    clear_bar_time TIMESTAMPTZ NOT NULL,
    clear_close DOUBLE PRECISION NOT NULL,
    bars_to_clear INTEGER NOT NULL,
    clearance_distance DOUBLE PRECISION NOT NULL,
    clearance_atr_5m DOUBLE PRECISION,
    structure_state_15m TEXT NOT NULL,
    fresh_geometry JSONB NOT NULL,
    context_payload JSONB NOT NULL,
    research_only BOOLEAN NOT NULL DEFAULT TRUE,
    execution_authorized BOOLEAN NOT NULL DEFAULT FALSE,
    outcome_visibility TEXT NOT NULL,
    trial_fingerprint TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_day_barrier_clear_rearm_clear_time
    ON day_barrier_clear_rearm_v1_clear (clear_bar_time DESC);
"""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in FORBIDDEN_OUTCOME_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    return value


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


async def ensure_observer_schema(connection: Any) -> None:
    await connection.execute(OBSERVER_SCHEMA_SQL)


async def pending_parent_symbols(connection: Any) -> list[str]:
    await ensure_observer_schema(connection)
    rows = await connection.fetch(
        """
        SELECT DISTINCT symbol
        FROM day_barrier_clear_rearm_v1_parent
        WHERE study=$1 AND resolution_status='PENDING' AND trigger_boundary IS NOT NULL
        ORDER BY symbol
        """,
        STUDY_ID,
    )
    return [str(row["symbol"]) for row in rows]


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
        candidates.append({
            "price": price,
            "timeframe": "15m",
            "swing_type": swing_type,
            "pivot_start_ms": int(pivot.start_ms),
            "prominence_atr": prominence / atr_15m if atr_15m > 0 else None,
            "point_in_time_cutoff_ms": cutoff_start_ms,
        })
    if not candidates:
        return None
    if side == "long":
        return min(candidates, key=lambda item: item["price"])
    return max(candidates, key=lambda item: item["price"])


def fresh_geometry_as_of_clear(analysis: Any, side: str, clear_index: int) -> dict[str, Any]:
    """Recompute clear-time geometry solely from point-in-time candle prefixes."""
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
    base = {
        "model": "RESEARCH_FRESH_CLEAR_GEOMETRY_V1",
        "as_of": _bar_close_at(clear_bar).isoformat(),
        "side": side,
        "reference_entry": live.round_to_tick(entry, tick),
        "inherited_parent_geometry": None,
        "research_only": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
    }
    if entry <= 0 or atr_5m <= 0 or atr_15m <= 0:
        return {**base, "status": "UNAVAILABLE", "reason": "POINT_IN_TIME_ATR_UNAVAILABLE"}

    recent = bars_5m[-9:]
    if side == "long":
        stop = min(min(bar.low for bar in recent), entry - 1.2 * atr_5m)
        direction = 1.0
    else:
        stop = max(max(bar.high for bar in recent), entry + 1.2 * atr_5m)
        direction = -1.0
    risk = abs(entry - stop)
    if risk <= max(tick * 3.0, entry * 0.0002):
        return {**base, "status": "UNAVAILABLE", "reason": "POINT_IN_TIME_RISK_TOO_SMALL"}

    entry_low, entry_high = live.fresh_entry_zone(current_price=entry, atr_5m=atr_5m, side=side)
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
    fresh_net_rr = max(0.0, (abs(reward_reference - entry) - cost) / max(risk, 1e-12))
    barrier_net_rr = (
        None if barrier is None
        else max(0.0, (abs(barrier - entry) - cost) / max(risk, 1e-12))
    )
    target_path_valid = bool(
        not barrier_before_tp2
        or (barrier_net_rr is not None and barrier_net_rr + 1e-9 >= live.DAY_MIN_RR)
    )
    structure_state = classify_15m_structure(bars_15m, clear_close_ms, 3)
    return _sanitize({
        **base,
        "status": "COMPLETE",
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
        "fresh_net_rr": fresh_net_rr,
        "target_path_valid": target_path_valid,
        "structure_state_15m": structure_state,
    })


def resolve_parent_against_analysis(parent: Mapping[str, Any], analysis: Any) -> dict[str, Any] | None:
    """Return the first terminal prospective state from bars after parent capture."""
    captured_at = parent.get("captured_at")
    if not isinstance(captured_at, datetime):
        return None
    captured_at = _utc(captured_at)
    side = str(parent.get("side") or "")
    try:
        boundary = float(parent["trigger_boundary"])
        barrier = float(parent["frozen_barrier_price"])
    except (TypeError, ValueError, KeyError):
        return None

    future = [
        (index, bar)
        for index, bar in enumerate(analysis.bars_5m)
        if _bar_close_at(bar) > captured_at
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
            atr_5m = live.atr(list(analysis.bars_5m[: index + 1]), 14)
            clearance = abs(close - barrier)
            return {
                "status": "CLEARED",
                "resolved_at": resolved_at,
                "resolution_reason": "FIRST_LATER_CLOSED_5M_BARRIER_CLEAR",
                "bars_to_resolution": bars_after_parent,
                "clear_bar_time": datetime.fromtimestamp(int(bar.start_ms) / 1000.0, tz=timezone.utc),
                "clear_close": close,
                "clearance_distance": clearance,
                "clearance_atr_5m": clearance / atr_5m if atr_5m > 0 else None,
                "structure_state_15m": structure_state,
                "fresh_geometry": fresh_geometry_as_of_clear(analysis, side, index),
                "clear_time_context": build_clear_context_snapshot(analysis, index),
            }
    return None


async def _pending_parents(connection: Any) -> list[dict[str, Any]]:
    rows = await connection.fetch(
        """
        SELECT event_key,captured_at,symbol,side,trigger_route,trigger_boundary,
               boundary_kind,frozen_barrier_price,resolution_status
        FROM day_barrier_clear_rearm_v1_parent
        WHERE study=$1 AND resolution_status='PENDING' AND trigger_boundary IS NOT NULL
        ORDER BY captured_at,event_key
        """,
        STUDY_ID,
    )
    return [dict(row) for row in rows]


async def _persist_resolution(
    connection: Any,
    parent: Mapping[str, Any],
    resolution: Mapping[str, Any],
    *,
    observed_at: datetime,
    source_commit_sha: str | None,
    analysis: Any,
) -> bool:
    status = str(resolution.get("status") or "")
    if status not in TERMINAL:
        return False
    updated = await connection.fetchval(
        """
        UPDATE day_barrier_clear_rearm_v1_parent
        SET resolution_status=$2,last_checked_at=$3,resolved_at=$4,resolution_reason=$5
        WHERE event_key=$1 AND resolution_status='PENDING'
        RETURNING 1
        """,
        parent["event_key"],
        status,
        observed_at,
        resolution.get("resolved_at"),
        resolution.get("resolution_reason"),
    )
    if not updated:
        return False
    if status != "CLEARED":
        return True
    context = _sanitize({
        "research_only": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "derivatives_context_only": True,
        "outcome_visibility": OUTCOME_VISIBILITY,
        "resolution_reason": resolution.get("resolution_reason"),
        "clear_time_context": resolution.get("clear_time_context") or {},
        "shortable_at_observer_run": bool(getattr(analysis, "shortable", False)),
        "tradeable_at_observer_run": bool(getattr(analysis.instrument, "tradeable", False)),
        "observer_run_at": _utc(observed_at).isoformat(),
        "derivatives_snapshot_timing": "OBSERVER_RUN_CONTEXT_ONLY_NOT_CLEAR_TIME",
        "derivatives_at_observer_run": getattr(analysis, "derivatives", {}) or {},
        "execution_state_is_not_reconstructed_at_clear_time": True,
    })
    geometry = _sanitize(resolution.get("fresh_geometry") or {})
    await connection.execute(
        """
        INSERT INTO day_barrier_clear_rearm_v1_clear (
            event_key,study,observer_version,observed_at,source_commit_sha,clear_bar_time,
            clear_close,bars_to_clear,clearance_distance,clearance_atr_5m,structure_state_15m,
            fresh_geometry,context_payload,research_only,execution_authorized,outcome_visibility,
            trial_fingerprint
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14,$15,$16,$17)
        ON CONFLICT (event_key) DO NOTHING
        """,
        parent["event_key"], STUDY_ID, OBSERVER_VERSION, observed_at, source_commit_sha,
        resolution["clear_bar_time"], resolution["clear_close"],
        resolution["bars_to_resolution"], resolution["clearance_distance"],
        resolution.get("clearance_atr_5m"), str(resolution.get("structure_state_15m") or "UNKNOWN"),
        json.dumps(geometry, ensure_ascii=True), json.dumps(context, ensure_ascii=True),
        True, False, OUTCOME_VISIBILITY, trial_fingerprint(STUDY_ID),
    )
    return True


async def persist_pending_resolutions(
    connection: Any,
    analyses: Iterable[Any],
    *,
    observed_at: datetime,
    source_commit_sha: str | None,
) -> dict[str, Any]:
    observed_at = _utc(observed_at)
    await ensure_observer_schema(connection)
    by_symbol = {str(item.instrument.symbol).upper(): item for item in analyses}
    resolved: Counter[str] = Counter()
    missing_analysis = 0
    for parent in await _pending_parents(connection):
        analysis = by_symbol.get(str(parent["symbol"]).upper())
        if analysis is None:
            missing_analysis += 1
            continue
        resolution = resolve_parent_against_analysis(parent, analysis)
        if resolution is None:
            await connection.execute(
                "UPDATE day_barrier_clear_rearm_v1_parent SET last_checked_at=$2 WHERE event_key=$1 AND resolution_status='PENDING'",
                parent["event_key"], observed_at,
            )
            continue
        if await _persist_resolution(
            connection,
            parent,
            resolution,
            observed_at=observed_at,
            source_commit_sha=source_commit_sha,
            analysis=analysis,
        ):
            resolved[str(resolution["status"])] += 1

    counts = await connection.fetch(
        """
        SELECT resolution_status,COUNT(*)::int AS count
        FROM day_barrier_clear_rearm_v1_parent
        WHERE study=$1
        GROUP BY resolution_status
        ORDER BY resolution_status
        """,
        STUDY_ID,
    )
    cumulative = {str(row["resolution_status"]): int(row["count"]) for row in counts}
    clear_rows = int(await connection.fetchval(
        "SELECT COUNT(*)::int FROM day_barrier_clear_rearm_v1_clear WHERE study=$1", STUDY_ID
    ) or 0)
    return {
        "status": "COMPLETE",
        "study": STUDY_ID,
        "observer_version": OBSERVER_VERSION,
        "context_version": CONTEXT_VERSION,
        "research_only": True,
        "label_free": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
        "parent_strategy_version": "0.7.5",
        "source_commit_sha": source_commit_sha,
        "captured_at": observed_at.isoformat(),
        "resolved_this_run": dict(sorted(resolved.items())),
        "pending_without_analysis_this_run": missing_analysis,
        "cumulative": {
            "pending": cumulative.get("PENDING", 0),
            "cleared": cumulative.get("CLEARED", 0),
            "invalidated_boundary": cumulative.get("INVALIDATED_BOUNDARY", 0),
            "invalidated_structure": cumulative.get("INVALIDATED_STRUCTURE", 0),
            "clear_rows": clear_rows,
        },
        "trial_fingerprint": trial_fingerprint(STUDY_ID),
        "outcome_visibility": OUTCOME_VISIBILITY,
        "notes": [
            "Only fully closed 5m bars after parent capture can resolve a parent.",
            "Original breakout/reclaim boundary loss or an opposing closed 15m shift terminates the parent before barrier clear.",
            "Fresh geometry is rebuilt as-of the clear bar; parent entry/stop/targets are never inherited.",
            "Clear-time candle context uses closed prefixes only; bid/ask spread remains an explicitly separate observer-run snapshot.",
            "No execution state is retrospectively reconstructed at the clear bar.",
            "No forward return, PnL, MFE/MAE, win/loss or target/stop outcome is stored.",
        ],
    }


__all__ = [
    "OBSERVER_SCHEMA_SQL", "OBSERVER_VERSION", "OUTCOME_VISIBILITY",
    "ensure_observer_schema", "fresh_geometry_as_of_clear", "pending_parent_symbols",
    "persist_pending_resolutions", "resolve_parent_against_analysis",
]
