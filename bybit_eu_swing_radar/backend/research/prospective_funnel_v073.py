"""Prospective, label-free v0.7.3 gate-funnel observability.

Research-only sidecar. This module records point-in-time gate snapshots for
liquidity-sweep opportunities seen by the live day worker. It never changes
candidate ranking, scoring, eligibility, trigger logic, execution or journal
semantics.

Key invariants:
- strategy version is pinned to v0.7.3;
- first successful recorder run establishes the prospective boundary;
- sweep events older than that boundary are never backfilled;
- outcome labels / realized PnL are not stored;
- current Bybit EU USDC spot-margin shortability is required for the forward
  SIDE_EXECUTION_MODEL gate;
- historical diagnostics keep their own default technical-only short model.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sweep_research import SweepResearchConfig, scan_sweep_setups

SPEC_VERSION = "v073-prospective-funnel-v1"
STRATEGY_VERSION = "0.7.3"
MAX_EVENT_AGE_MINUTES = 90

PROSPECTIVE_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS day_trade_v073_prospective_funnel_meta (
    spec_version TEXT PRIMARY KEY,
    strategy_version TEXT NOT NULL,
    prospective_start_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS day_trade_v073_prospective_funnel (
    id BIGSERIAL PRIMARY KEY,
    spec_version TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    run_id TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    event_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    sweep_time TIMESTAMPTZ NOT NULL,
    sweep_index INTEGER,
    sweep_depth_atr DOUBLE PRECISION,
    reclaim_confirmed BOOLEAN NOT NULL,
    structure_shift_5m BOOLEAN NOT NULL,
    volume_confirmed BOOLEAN NOT NULL,
    structure_confirmed_15m BOOLEAN NOT NULL,
    candidate_built BOOLEAN NOT NULL,
    pass_tradeable BOOLEAN NOT NULL,
    pass_side_execution_model BOOLEAN NOT NULL,
    pass_expansion BOOLEAN NOT NULL,
    pass_direction BOOLEAN NOT NULL,
    pass_quality BOOLEAN NOT NULL,
    pass_setup BOOLEAN NOT NULL,
    pass_target_path BOOLEAN NOT NULL,
    pass_rr BOOLEAN NOT NULL,
    pass_strict_trade BOOLEAN NOT NULL,
    live_strict_trigger_observed BOOLEAN NOT NULL DEFAULT FALSE,
    first_failed_gate TEXT NOT NULL,
    borrowability_status TEXT NOT NULL,
    expansion_score DOUBLE PRECISION,
    side_direction_score DOUBLE PRECISION,
    quality_score DOUBLE PRECISION,
    setup_score DOUBLE PRECISION,
    expected_rr DOUBLE PRECISION,
    volume_ratio_5m DOUBLE PRECISION,
    target_path_valid BOOLEAN,
    failure_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    snapshot_payload JSONB NOT NULL,
    UNIQUE (spec_version, run_id, event_key)
);

CREATE INDEX IF NOT EXISTS idx_day_trade_v073_prospective_funnel_captured
    ON day_trade_v073_prospective_funnel (captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_day_trade_v073_prospective_funnel_event
    ON day_trade_v073_prospective_funnel (event_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_day_trade_v073_prospective_funnel_symbol_side
    ON day_trade_v073_prospective_funnel (symbol, side, captured_at DESC);
"""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def _event_key(symbol: str, side: str, sweep_time: str) -> str:
    raw = "|".join((SPEC_VERSION, STRATEGY_VERSION, symbol.upper(), side, sweep_time))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _recompute_first_failed(snapshot: dict[str, Any]) -> str:
    ordered = [
        ("RECLAIM", bool(snapshot.get("pass_reclaim"))),
        ("STRUCTURE_SHIFT_5M", bool(snapshot.get("pass_structure_5m"))),
        ("VOLUME_1_3X", bool(snapshot.get("pass_volume_confirmation"))),
        ("STRUCTURE_15M", bool(snapshot.get("pass_structure_15m"))),
        ("CANDIDATE_BUILD", bool(snapshot.get("candidate_built"))),
        ("LIQUIDITY_EXECUTION", bool(snapshot.get("pass_tradeable"))),
        ("SIDE_EXECUTION_MODEL", bool(snapshot.get("pass_side_execution_model"))),
        ("EXPANSION_55", bool(snapshot.get("pass_expansion"))),
        ("DIRECTION_35", bool(snapshot.get("pass_direction"))),
        ("QUALITY_65", bool(snapshot.get("pass_quality"))),
        ("SETUP_70", bool(snapshot.get("pass_setup"))),
        ("TARGET_PATH", bool(snapshot.get("pass_target_path"))),
        ("NET_RR_1_8", bool(snapshot.get("pass_rr"))),
    ]
    for name, passed in ordered:
        if not passed:
            return name
    return "PASSED_STRICT_TRADE"


def _apply_current_execution_semantics(
    snapshot: dict[str, Any],
    *,
    side: str,
    current_shortable: bool,
) -> dict[str, Any]:
    """Adapt historical gate flags to forward Bybit EU execution semantics.

    `diagnostics_v073.gate_snapshot` remains the source of the score/target-path
    gate definitions. Historical replay defaults to technical-only short
    execution because historical borrowability is unavailable. Forward capture
    has current borrowability, so only the execution-dependent fields are
    replaced here.
    """
    result = dict(snapshot)
    if side == "long":
        return result

    tradeable = bool(result.get("pass_tradeable"))
    execution_model = tradeable and bool(current_shortable)
    result["pass_side_execution_model"] = execution_model
    result["borrowability_status"] = (
        "CURRENT_USDC_MARGIN_CONFIRMED"
        if current_shortable
        else "CURRENT_USDC_MARGIN_BLOCKED"
    )

    pass_score_gates = bool(
        result.get("pass_expansion")
        and result.get("pass_direction")
        and result.get("pass_quality")
        and result.get("pass_setup")
    )
    strict_eligible = bool(
        tradeable
        and execution_model
        and pass_score_gates
        and result.get("pass_target_path")
        and result.get("pass_rr")
    )
    strict_trade = bool(
        strict_eligible
        and result.get("pass_reclaim")
        and result.get("pass_structure_5m")
        and result.get("pass_volume_confirmation")
        and result.get("pass_structure_15m")
    )
    result["pass_score_gates"] = pass_score_gates
    result["pass_strict_eligible"] = strict_eligible
    result["pass_strict_trade"] = strict_trade
    result["near_strict"] = bool(result.get("near_strict") and execution_model)
    result["first_failed_gate"] = _recompute_first_failed(result)
    return result


def _live_strict_event_keys(setups: Iterable[dict[str, Any]]) -> set[str]:
    """Return exact sweep keys that the live v0.7.3 worker triggered as STRICT."""
    keys: set[str] = set()
    for setup in setups:
        if setup.get("category") != "STRICT":
            continue
        trigger = setup.get("trigger") or {}
        if not trigger.get("triggered"):
            continue
        sweep = trigger.get("sweep_confirmation") or {}
        sweep_time = str(sweep.get("sweep_time") or "")
        symbol = str(setup.get("symbol") or "").upper()
        side = str(setup.get("side") or "")
        if symbol and side in {"long", "short"} and sweep_time:
            keys.add(_event_key(symbol, side, sweep_time))
    return keys


def _analysis_snapshot_rows(
    analyses: Iterable[Any],
    *,
    captured_at: datetime,
    prospective_start_at: datetime,
    source_commit_sha: str | None,
    volume_confirmation_ratio: float = 1.3,
    live_strict_event_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build label-free point-in-time rows for recent prospective sweeps."""
    # Local import avoids an import cycle: diagnostics_v073 imports day_worker,
    # while day_worker calls this module only after its module initialization.
    from diagnostics_v073 import build_research_candidate, gate_snapshot

    captured_at = _as_utc(captured_at)
    prospective_start_at = _as_utc(prospective_start_at)
    lower_age_bound = captured_at - timedelta(minutes=MAX_EVENT_AGE_MINUTES)
    lower_bound = max(prospective_start_at, lower_age_bound)
    run_id = captured_at.isoformat()
    sweep_config = SweepResearchConfig(
        volume_confirmation_ratio=volume_confirmation_ratio
    )

    output: list[dict[str, Any]] = []
    for analysis in analyses:
        symbol = str(analysis.instrument.symbol).upper()
        for side in ("long", "short"):
            events = scan_sweep_setups(
                analysis.bars_5m,
                side,
                bars_15m=analysis.bars_15m,
                config=sweep_config,
                include_incomplete=True,
            )
            for event in events:
                sweep_time_text = str(event.get("sweep_time") or "")
                sweep_at = _parse_iso(sweep_time_text)
                if sweep_at is None:
                    continue
                if sweep_at < lower_bound or sweep_at > captured_at:
                    continue

                candidate = build_research_candidate(analysis, side, event)
                gates = gate_snapshot(
                    candidate,
                    side,
                    event,
                    current_shortable_proxy=bool(analysis.shortable),
                )
                gates = _apply_current_execution_semantics(
                    gates,
                    side=side,
                    current_shortable=bool(analysis.shortable),
                )
                metrics = (candidate or {}).get("metrics") or {}
                payload = {
                    "spec_version": SPEC_VERSION,
                    "strategy_version": STRATEGY_VERSION,
                    "label_free": True,
                    "capture_mode": "RUN_SNAPSHOT",
                    "source_commit_sha": source_commit_sha,
                    "symbol": symbol,
                    "side": side,
                    "sweep": event,
                    "gates": gates,
                    "scores": {
                        "expansion_score": None if candidate is None else candidate.get("expansion_score"),
                        "side_direction_score": None if candidate is None else candidate.get("side_direction_score"),
                        "quality_score": None if candidate is None else candidate.get("quality_score"),
                        "setup_score": None if candidate is None else candidate.get("setup_score"),
                        "expected_rr": None if candidate is None else candidate.get("expected_rr"),
                    },
                }
                event_key = _event_key(symbol, side, sweep_time_text)
                output.append(
                    {
                        "spec_version": SPEC_VERSION,
                        "strategy_version": STRATEGY_VERSION,
                        "run_id": run_id,
                        "captured_at": captured_at,
                        "source_commit_sha": source_commit_sha,
                        "event_key": event_key,
                        "symbol": symbol,
                        "side": side,
                        "sweep_time": sweep_at,
                        "sweep_index": event.get("sweep_index"),
                        "sweep_depth_atr": event.get("sweep_depth_atr"),
                        "reclaim_confirmed": bool(gates.get("pass_reclaim")),
                        "structure_shift_5m": bool(gates.get("pass_structure_5m")),
                        "volume_confirmed": bool(gates.get("pass_volume_confirmation")),
                        "structure_confirmed_15m": bool(gates.get("pass_structure_15m")),
                        "candidate_built": bool(gates.get("candidate_built")),
                        "pass_tradeable": bool(gates.get("pass_tradeable")),
                        "pass_side_execution_model": bool(gates.get("pass_side_execution_model")),
                        "pass_expansion": bool(gates.get("pass_expansion")),
                        "pass_direction": bool(gates.get("pass_direction")),
                        "pass_quality": bool(gates.get("pass_quality")),
                        "pass_setup": bool(gates.get("pass_setup")),
                        "pass_target_path": bool(gates.get("pass_target_path")),
                        "pass_rr": bool(gates.get("pass_rr")),
                        "pass_strict_trade": bool(gates.get("pass_strict_trade")),
                        "live_strict_trigger_observed": event_key in (live_strict_event_keys or set()),
                        "first_failed_gate": str(gates.get("first_failed_gate") or "UNKNOWN"),
                        "borrowability_status": str(gates.get("borrowability_status") or "UNKNOWN"),
                        "expansion_score": None if candidate is None else candidate.get("expansion_score"),
                        "side_direction_score": None if candidate is None else candidate.get("side_direction_score"),
                        "quality_score": None if candidate is None else candidate.get("quality_score"),
                        "setup_score": None if candidate is None else candidate.get("setup_score"),
                        "expected_rr": None if candidate is None else candidate.get("expected_rr"),
                        "volume_ratio_5m": event.get("volume_ratio_5m"),
                        "target_path_valid": None if candidate is None else bool(metrics.get("target_path_valid")),
                        "failure_reasons": list(event.get("failure_reasons") or []),
                        "snapshot_payload": payload,
                    }
                )
    return output


async def _ensure_prospective_boundary(connection: Any, captured_at: datetime) -> datetime:
    captured_at = _as_utc(captured_at)
    await connection.execute(PROSPECTIVE_SCHEMA_SQL)
    await connection.execute(
        """
        INSERT INTO day_trade_v073_prospective_funnel_meta (
            spec_version, strategy_version, prospective_start_at
        ) VALUES ($1, $2, $3)
        ON CONFLICT (spec_version) DO NOTHING
        """,
        SPEC_VERSION,
        STRATEGY_VERSION,
        captured_at,
    )
    value = await connection.fetchval(
        """
        SELECT prospective_start_at
        FROM day_trade_v073_prospective_funnel_meta
        WHERE spec_version = $1 AND strategy_version = $2
        """,
        SPEC_VERSION,
        STRATEGY_VERSION,
    )
    if value is None:
        raise RuntimeError("prospective funnel boundary was not persisted")
    return _as_utc(value)


async def _insert_snapshot(connection: Any, row: dict[str, Any]) -> bool:
    inserted = await connection.fetchval(
        """
        INSERT INTO day_trade_v073_prospective_funnel (
            spec_version,strategy_version,run_id,captured_at,source_commit_sha,
            event_key,symbol,side,sweep_time,sweep_index,sweep_depth_atr,
            reclaim_confirmed,structure_shift_5m,volume_confirmed,
            structure_confirmed_15m,candidate_built,pass_tradeable,
            pass_side_execution_model,pass_expansion,pass_direction,pass_quality,
            pass_setup,pass_target_path,pass_rr,pass_strict_trade,
            live_strict_trigger_observed,first_failed_gate,borrowability_status,expansion_score,
            side_direction_score,quality_score,setup_score,expected_rr,
            volume_ratio_5m,target_path_valid,failure_reasons,snapshot_payload
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
            $19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,
            $35,$36::jsonb,$37::jsonb
        )
        ON CONFLICT (spec_version, run_id, event_key) DO NOTHING
        RETURNING 1
        """,
        row["spec_version"], row["strategy_version"], row["run_id"],
        row["captured_at"], row["source_commit_sha"], row["event_key"],
        row["symbol"], row["side"], row["sweep_time"], row["sweep_index"],
        row["sweep_depth_atr"], row["reclaim_confirmed"],
        row["structure_shift_5m"], row["volume_confirmed"],
        row["structure_confirmed_15m"], row["candidate_built"],
        row["pass_tradeable"], row["pass_side_execution_model"],
        row["pass_expansion"], row["pass_direction"], row["pass_quality"],
        row["pass_setup"], row["pass_target_path"], row["pass_rr"],
        row["pass_strict_trade"], row["live_strict_trigger_observed"],
        row["first_failed_gate"], row["borrowability_status"], row["expansion_score"],
        row["side_direction_score"], row["quality_score"], row["setup_score"],
        row["expected_rr"], row["volume_ratio_5m"], row["target_path_valid"],
        json.dumps(row["failure_reasons"], ensure_ascii=False),
        json.dumps(row["snapshot_payload"], ensure_ascii=False),
    )
    return bool(inserted)


async def _cumulative_status(
    connection: Any,
    *,
    prospective_start_at: datetime,
    captured_at: datetime,
    source_commit_sha: str | None,
    current_rows: list[dict[str, Any]],
    inserted_snapshots: int,
) -> dict[str, Any]:
    latest = await connection.fetch(
        """
        SELECT DISTINCT ON (event_key)
            event_key,symbol,side,captured_at,first_failed_gate,
            reclaim_confirmed,structure_shift_5m,volume_confirmed,
            structure_confirmed_15m,candidate_built,pass_tradeable,
            pass_side_execution_model,pass_expansion,pass_direction,pass_quality,
            pass_setup,pass_target_path,pass_rr,pass_strict_trade
        FROM day_trade_v073_prospective_funnel
        WHERE spec_version = $1 AND strategy_version = $2
        ORDER BY event_key, captured_at DESC
        """,
        SPEC_VERSION,
        STRATEGY_VERSION,
    )
    total_snapshots = int(
        await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM day_trade_v073_prospective_funnel
            WHERE spec_version = $1 AND strategy_version = $2
            """,
            SPEC_VERSION,
            STRATEGY_VERSION,
        )
        or 0
    )

    fields = [
        "reclaim_confirmed",
        "structure_shift_5m",
        "volume_confirmed",
        "structure_confirmed_15m",
        "candidate_built",
        "pass_tradeable",
        "pass_side_execution_model",
        "pass_expansion",
        "pass_direction",
        "pass_quality",
        "pass_setup",
        "pass_target_path",
        "pass_rr",
        "pass_strict_trade",
    ]
    gate_counts = {
        field: sum(1 for row in latest if bool(row[field]))
        for field in fields
    }
    first_failed_counts = Counter(str(row["first_failed_gate"]) for row in latest)
    side_counts = Counter(str(row["side"]) for row in latest)
    symbols = sorted({str(row["symbol"]) for row in latest})
    current_side_counts = Counter(str(row["side"]) for row in current_rows)

    return {
        "status": "COMPLETE",
        "research_only": True,
        "label_free": True,
        "outcome_labels_stored": False,
        "spec_version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "prospective_start_at": _as_utc(prospective_start_at).isoformat(),
        "captured_at": _as_utc(captured_at).isoformat(),
        "source_commit_sha": source_commit_sha,
        "max_event_age_minutes": MAX_EVENT_AGE_MINUTES,
        "current_run": {
            "observed_snapshots": len(current_rows),
            "inserted_snapshots": inserted_snapshots,
            "long_snapshots": current_side_counts.get("long", 0),
            "short_snapshots": current_side_counts.get("short", 0),
        },
        "cumulative": {
            "distinct_sweep_events": len(latest),
            "total_snapshots": total_snapshots,
            "symbols_observed": len(symbols),
            "symbol_list": symbols,
            "side_event_counts": dict(sorted(side_counts.items())),
            "latest_gate_pass_counts": gate_counts,
            "latest_first_failed_gate_counts": dict(sorted(first_failed_counts.items())),
        },
        "notes": [
            "No sweep event before prospective_start_at is persisted.",
            "Rows are run snapshots, so one sweep event may have multiple point-in-time snapshots as it matures.",
            "The forward short execution gate requires current Bybit EU USDC spot-margin borrowability.",
            "pass_strict_trade is the comparable gate-chain state; live_strict_trigger_observed separately records an exact live STRICT trigger on that run.",
            "No realized outcome, PnL, MFE/MAE or win/loss label is stored by this recorder.",
        ],
    }


async def persist_v073_prospective_funnel(
    connection: Any,
    analyses: Iterable[Any],
    *,
    captured_at: datetime,
    source_commit_sha: str | None,
    volume_confirmation_ratio: float,
    live_setups: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Persist one label-free forward funnel snapshot batch."""
    captured_at = _as_utc(captured_at)
    prospective_start_at = await _ensure_prospective_boundary(connection, captured_at)
    rows = _analysis_snapshot_rows(
        analyses,
        captured_at=captured_at,
        prospective_start_at=prospective_start_at,
        source_commit_sha=source_commit_sha,
        volume_confirmation_ratio=volume_confirmation_ratio,
        live_strict_event_keys=_live_strict_event_keys(live_setups),
    )
    inserted = 0
    for row in rows:
        if await _insert_snapshot(connection, row):
            inserted += 1
    return await _cumulative_status(
        connection,
        prospective_start_at=prospective_start_at,
        captured_at=captured_at,
        source_commit_sha=source_commit_sha,
        current_rows=rows,
        inserted_snapshots=inserted,
    )


__all__ = [
    "MAX_EVENT_AGE_MINUTES",
    "PROSPECTIVE_SCHEMA_SQL",
    "SPEC_VERSION",
    "STRATEGY_VERSION",
    "_analysis_snapshot_rows",
    "_apply_current_execution_semantics",
    "_event_key",
    "_live_strict_event_keys",
    "persist_v073_prospective_funnel",
]
