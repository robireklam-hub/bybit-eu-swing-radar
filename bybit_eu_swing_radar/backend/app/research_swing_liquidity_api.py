from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from app.config import settings
from app.providers.bybit import BybitClient
from research.research_governance import snapshot_governance_metadata
from research.research_trial_registry import ensure_trial_registered, trial_registry_status
from research.swing_liquidity_event_builder import build_trigger_events
from research.swing_liquidity_lifecycle import record_lifecycle_on_capture_persistence

STUDY = "swing-liquidity-validation-v1"
FORBIDDEN_LABEL_KEYS = {
    "outcome",
    "gross_r",
    "net_r",
    "mfe_r",
    "mae_r",
    "closed_at",
    "exit_price",
    "exit_reason",
    "tp_hit_at",
    "stop_hit_at",
    "future_return",
    "future_returns",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS swing_liquidity_forward_captures (
    captured_at TIMESTAMPTZ PRIMARY KEY,
    study TEXT NOT NULL,
    source_commit_sha TEXT,
    scan_data_as_of TIMESTAMPTZ,
    candidate_count INTEGER NOT NULL,
    orderbook_count INTEGER NOT NULL,
    orderbook_error_count INTEGER NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE swing_liquidity_forward_captures ADD COLUMN IF NOT EXISTS feature_available_at TIMESTAMPTZ;
ALTER TABLE swing_liquidity_forward_captures ADD COLUMN IF NOT EXISTS provenance_version TEXT;
ALTER TABLE swing_liquidity_forward_captures ADD COLUMN IF NOT EXISTS trial_id TEXT;
ALTER TABLE swing_liquidity_forward_captures ADD COLUMN IF NOT EXISTS trial_fingerprint TEXT;

CREATE TABLE IF NOT EXISTS swing_liquidity_forward_observations (
    captured_at TIMESTAMPTZ NOT NULL REFERENCES swing_liquidity_forward_captures(captured_at) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    scan_data_as_of TIMESTAMPTZ,
    source_section TEXT NOT NULL,
    shortable BOOLEAN NOT NULL,
    setup_score DOUBLE PRECISION,
    expansion_score DOUBLE PRECISION,
    direction_score DOUBLE PRECISION,
    quality_score DOUBLE PRECISION,
    turnover_24h_usdc DOUBLE PRECISION,
    turnover_tier TEXT NOT NULL,
    spread_bps DOUBLE PRECISION,
    spread_tier TEXT NOT NULL,
    book_costs JSONB NOT NULL DEFAULT '[]'::jsonb,
    participation_sensitivity JSONB NOT NULL DEFAULT '[]'::jsonb,
    candidate JSONB NOT NULL,
    PRIMARY KEY (captured_at, symbol, side)
);

CREATE INDEX IF NOT EXISTS idx_swing_liq_forward_symbol_time
    ON swing_liquidity_forward_observations(symbol, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_swing_liq_forward_tiers
    ON swing_liquidity_forward_observations(turnover_tier, spread_tier, captured_at DESC);
"""


def validate_usdc_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if (
        not normalized.endswith("USDC")
        or len(normalized) < 5
        or len(normalized) > 30
        or not normalized.isalnum()
    ):
        raise HTTPException(status_code=400, detail="Research orderbook requires a Bybit EU USDC spot symbol")
    return normalized


def compact_orderbook_payload(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise RuntimeError("Bybit orderbook result is not an object")
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "execution_action": False,
        "source": "Bybit EU public spot orderbook",
        "symbol": symbol,
        "data_as_of": datetime.now(timezone.utc).isoformat(),
        "upstream_time_ms": payload.get("time"),
        "update_id": result.get("u"),
        "seq": result.get("seq"),
        "bids": result.get("b") or [],
        "asks": result.get("a") or [],
    }


def compact_closed_4h_candles(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Normalize only fully closed Bybit 4H spot candles for label-blind trigger construction."""
    result = payload.get("result") or {}
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Bybit kline result list is unavailable")
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candles: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            start_at = datetime.fromtimestamp(int(row[0]) / 1000.0, tz=timezone.utc)
            close = float(row[4])
        except (TypeError, ValueError, OSError):
            continue
        close_at = start_at + timedelta(hours=4)
        if close_at > cutoff:
            continue
        candles.append(
            {
                "start_at": start_at.isoformat(),
                "close_at": close_at.isoformat(),
                "close": close,
            }
        )
    candles.sort(key=lambda item: item["close_at"])
    return candles


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail=f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=400, detail=f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _contains_forbidden_label(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_LABEL_KEYS:
                return normalized
            nested = _contains_forbidden_label(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _contains_forbidden_label(child)
            if nested:
                return nested
    return None


def _governance_or_http_error(snapshot: dict[str, Any]) -> dict[str, Any]:
    try:
        return snapshot_governance_metadata(snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid research governance metadata: {exc}") from exc


def validate_forward_snapshot(snapshot: dict[str, Any]) -> tuple[datetime, datetime | None, list[dict[str, Any]]]:
    if snapshot.get("study") != STUDY:
        raise HTTPException(status_code=400, detail="unexpected research study")
    if snapshot.get("research_only") is not True or snapshot.get("label_blind") is not True:
        raise HTTPException(status_code=400, detail="snapshot must be research-only and label-blind")
    if snapshot.get("live_gate_unchanged") is not True:
        raise HTTPException(status_code=400, detail="live liquidity gate mutation is forbidden")
    forbidden = _contains_forbidden_label(snapshot)
    if forbidden:
        raise HTTPException(status_code=400, detail=f"forward labels are forbidden: {forbidden}")

    captured_at = _parse_timestamp(snapshot.get("captured_at"), "captured_at")
    _governance_or_http_error(snapshot)
    scan_raw = snapshot.get("scan_data_as_of")
    scan_data_as_of = _parse_timestamp(scan_raw, "scan_data_as_of") if scan_raw else None
    candidates = snapshot.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 200:
        raise HTTPException(status_code=400, detail="candidates must be a bounded list")
    try:
        declared_count = int(snapshot.get("candidate_count"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="candidate_count is invalid") from exc
    if declared_count != len(candidates):
        raise HTTPException(status_code=400, detail="candidate_count does not match candidates")

    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise HTTPException(status_code=400, detail="candidate is not an object")
        symbol = validate_usdc_symbol(str(candidate.get("symbol") or ""))
        side = str(candidate.get("side") or "").lower()
        if side not in {"long", "short"}:
            raise HTTPException(status_code=400, detail=f"invalid side for {symbol}")
        key = (symbol, side)
        if key in seen:
            raise HTTPException(status_code=400, detail=f"duplicate candidate {symbol}/{side}")
        seen.add(key)
        candidate["symbol"] = symbol
        candidate["side"] = side
    return captured_at, scan_data_as_of, candidates


async def persist_forward_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at, scan_data_as_of, candidates = validate_forward_snapshot(snapshot)
    governance = _governance_or_http_error(snapshot)
    orderbooks = snapshot.get("orderbooks") or {}
    errors = snapshot.get("orderbook_errors") or {}
    if not isinstance(orderbooks, dict) or not isinstance(errors, dict):
        raise HTTPException(status_code=400, detail="orderbook coverage fields are invalid")

    source_commit_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
    lifecycle_adoption: dict[str, Any] | None = None
    conn = await asyncpg.connect(settings.database_url)
    try:
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL)
            await ensure_trial_registered(
                conn,
                STUDY,
                source_commit_sha=source_commit_sha,
            )
            result = await conn.execute(
                """
                INSERT INTO swing_liquidity_forward_captures (
                    captured_at, study, source_commit_sha, scan_data_as_of,
                    candidate_count, orderbook_count, orderbook_error_count,
                    feature_available_at, provenance_version, trial_id, trial_fingerprint
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (captured_at) DO NOTHING
                """,
                captured_at,
                STUDY,
                source_commit_sha,
                scan_data_as_of,
                len(candidates),
                len(orderbooks),
                len(errors),
                governance["feature_available_at"] if governance["point_in_time_verified"] else None,
                governance["provenance_version"],
                governance["trial_id"],
                governance["trial_fingerprint"],
            )
            inserted_capture = result.endswith("1")
            if inserted_capture:
                for candidate in candidates:
                    await conn.execute(
                        """
                        INSERT INTO swing_liquidity_forward_observations (
                            captured_at, symbol, side, scan_data_as_of, source_section,
                            shortable, setup_score, expansion_score, direction_score,
                            quality_score, turnover_24h_usdc, turnover_tier, spread_bps,
                            spread_tier, book_costs, participation_sensitivity, candidate
                        ) VALUES (
                            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                            $15::jsonb,$16::jsonb,$17::jsonb
                        )
                        """,
                        captured_at,
                        candidate["symbol"],
                        candidate["side"],
                        scan_data_as_of,
                        str(candidate.get("source_section") or "unknown"),
                        bool(candidate.get("shortable", False)),
                        candidate.get("setup_score"),
                        candidate.get("expansion_score"),
                        candidate.get("direction_score"),
                        candidate.get("quality_score"),
                        candidate.get("turnover_24h_usdc"),
                        str(candidate.get("turnover_tier") or "UNKNOWN"),
                        candidate.get("spread_bps"),
                        str(candidate.get("spread_tier") or "UNKNOWN"),
                        json.dumps(candidate.get("book_costs") or []),
                        json.dumps(candidate.get("participation_sensitivity") or []),
                        json.dumps(candidate, ensure_ascii=False, default=str),
                    )
            lifecycle_adoption = await record_lifecycle_on_capture_persistence(
                conn,
                inserted_capture=inserted_capture,
                source_commit_sha=source_commit_sha,
            )
    finally:
        await conn.close()

    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": STUDY,
        "captured_at": captured_at.isoformat(),
        "feature_available_at": (
            governance["feature_available_at"].isoformat() if governance["point_in_time_verified"] else None
        ),
        "point_in_time_verified": governance["point_in_time_verified"],
        "provenance_version": governance["provenance_version"],
        "trial_id": governance["trial_id"],
        "trial_fingerprint": governance["trial_fingerprint"],
        "inserted": inserted_capture,
        "candidate_count": len(candidates),
        "orderbook_count": len(orderbooks),
        "orderbook_error_count": len(errors),
        "lifecycle_adoption": lifecycle_adoption,
    }


async def load_forward_snapshots() -> list[dict[str, Any]]:
    """Load only durable label-blind covariates plus point-in-time lineage."""
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(SCHEMA_SQL)
        rows = await conn.fetch(
            """
            SELECT o.captured_at, o.symbol, o.side, o.candidate,
                   COALESCE(c.feature_available_at, o.captured_at) AS available_at,
                   c.feature_available_at, c.provenance_version, c.trial_id, c.trial_fingerprint
            FROM swing_liquidity_forward_observations AS o
            JOIN swing_liquidity_forward_captures AS c ON c.captured_at = o.captured_at
            ORDER BY o.captured_at ASC, o.symbol ASC, o.side ASC
            """
        )
    finally:
        await conn.close()
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        candidate = row["candidate"]
        if isinstance(candidate, str):
            candidate = json.loads(candidate)
        if not isinstance(candidate, dict):
            continue
        snapshots.append(
            {
                "captured_at": row["captured_at"].isoformat(),
                "available_at": row["available_at"].isoformat(),
                "feature_available_at": row["feature_available_at"].isoformat() if row["feature_available_at"] else None,
                "point_in_time_verified": row["provenance_version"] == "pit-v1" and row["feature_available_at"] is not None,
                "provenance_version": row["provenance_version"] or "legacy-captured-at-v0",
                "trial_id": row["trial_id"],
                "trial_fingerprint": row["trial_fingerprint"],
                "symbol": str(row["symbol"]).upper(),
                "side": str(row["side"]).lower(),
                "candidate": candidate,
            }
        )
    return snapshots


def build_events_from_snapshots_and_klines(
    snapshots: list[dict[str, Any]],
    klines_by_symbol: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Construct preregistered label-blind events keyed by symbol/side/trigger bar."""
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for snapshot in snapshots:
        symbol = str(snapshot.get("symbol") or "").upper()
        side = str(snapshot.get("side") or "").lower()
        if symbol and side in {"long", "short"}:
            grouped[(symbol, side)].append(snapshot)

    events: list[dict[str, Any]] = []
    for (symbol, side), rows in sorted(grouped.items()):
        payload = klines_by_symbol.get(symbol)
        if not isinstance(payload, dict):
            continue
        candles = compact_closed_4h_candles(payload, now=cutoff)
        events.extend(build_trigger_events(rows, candles, symbol=symbol, side=side))
    events.sort(key=lambda item: (item["trigger_close_at"], item["symbol"], item["side"]))
    return events


async def forward_event_status(client: BybitClient) -> dict[str, Any]:
    snapshots = await load_forward_snapshots()
    symbols = sorted({str(row.get("symbol") or "").upper() for row in snapshots if row.get("symbol")})
    klines: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            klines[symbol] = await client.kline(symbol, interval="240", limit=300, category="spot")
        except Exception as exc:
            errors[symbol] = type(exc).__name__

    checked_at = datetime.now(timezone.utc)
    events = build_events_from_snapshots_and_klines(snapshots, klines, now=checked_at)
    matured = [event for event in events if datetime.fromisoformat(event["matures_at"]) <= checked_at]
    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": STUDY,
        "builder_version": "swing-liquidity-event-builder-v1",
        "event_identity": "symbol_side_first_qualifying_4h_trigger_bar",
        "checked_at": checked_at.isoformat(),
        "durable_snapshot_rows": len(snapshots),
        "symbol_count": len(symbols),
        "kline_symbol_count": len(klines),
        "kline_errors": errors,
        "event_count": len(events),
        "matured_event_count": len(matured),
        "point_in_time_verified_event_count": sum(1 for event in events if event.get("point_in_time_verified") is True),
        "legacy_or_unverified_event_count": sum(1 for event in events if event.get("point_in_time_verified") is not True),
        "events": events,
        "note": "Repeated snapshots are covariates; unique symbol/side/first qualifying 4H trigger bars are independent label-blind events. PIT-v1 events use feature availability time; legacy rows are retained but explicitly unverified.",
    }


async def forward_status() -> dict[str, Any]:
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(SCHEMA_SQL)
        registry = await trial_registry_status(conn, STUDY)
        capture = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS capture_count,
                   MIN(captured_at) AS first_capture_at,
                   MAX(captured_at) AS last_capture_at,
                   COALESCE(SUM(candidate_count), 0)::int AS candidate_observations,
                   COALESCE(SUM(orderbook_error_count), 0)::int AS orderbook_errors,
                   COUNT(*) FILTER (WHERE provenance_version = 'pit-v1' AND feature_available_at IS NOT NULL)::int AS point_in_time_verified_captures
            FROM swing_liquidity_forward_captures
            """
        )
        tier_rows = await conn.fetch(
            """
            SELECT turnover_tier, COUNT(*)::int AS n
            FROM swing_liquidity_forward_observations
            GROUP BY turnover_tier
            ORDER BY turnover_tier
            """
        )
        spread_rows = await conn.fetch(
            """
            SELECT spread_tier, COUNT(*)::int AS n
            FROM swing_liquidity_forward_observations
            GROUP BY spread_tier
            ORDER BY spread_tier
            """
        )
    finally:
        await conn.close()
    verified_captures = int(capture["point_in_time_verified_captures"] or 0)
    capture_count = int(capture["capture_count"] or 0)
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": STUDY,
        "trial_registry": registry,
        "capture_count": capture_count,
        "first_capture_at": capture["first_capture_at"].isoformat() if capture["first_capture_at"] else None,
        "last_capture_at": capture["last_capture_at"].isoformat() if capture["last_capture_at"] else None,
        "candidate_observations": int(capture["candidate_observations"] or 0),
        "orderbook_errors": int(capture["orderbook_errors"] or 0),
        "point_in_time_verified_captures": verified_captures,
        "legacy_or_unverified_captures": capture_count - verified_captures,
        "turnover_tiers": {row["turnover_tier"]: row["n"] for row in tier_rows},
        "spread_tiers": {row["spread_tier"]: row["n"] for row in spread_rows},
        "development_target_matured_events": 60,
        "validation_target_matured_events": 40,
        "note": "Observation counts are prospective covariates, not matured independent trigger events. PIT-v1 coverage is reported separately from retained legacy captures.",
    }


def attach_research_swing_liquidity_routes(
    app: FastAPI,
    require_api_key: Callable[..., None],
    *,
    bybit_client: BybitClient | None = None,
) -> None:
    client = bybit_client or BybitClient()

    @app.get(
        "/v1/research/swing-liquidity/orderbook/{symbol}",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def research_swing_liquidity_orderbook(symbol: str) -> dict[str, Any]:
        normalized = validate_usdc_symbol(symbol)
        try:
            payload = await client.orderbook(normalized, limit=50, category="spot")
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Bybit EU public orderbook unavailable: {type(exc).__name__}",
            ) from exc
        return compact_orderbook_payload(normalized, payload)

    @app.post(
        "/v1/research/swing-liquidity/forward-snapshot",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def research_swing_liquidity_forward_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return await persist_forward_snapshot(snapshot)

    @app.get(
        "/v1/research/swing-liquidity/forward-status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def research_swing_liquidity_forward_status() -> dict[str, Any]:
        return await forward_status()

    @app.get(
        "/v1/research/swing-liquidity/forward-event-status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def research_swing_liquidity_forward_event_status() -> dict[str, Any]:
        return await forward_event_status(client)
