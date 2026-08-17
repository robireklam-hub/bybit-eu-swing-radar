from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, HTTPException

from app.config import settings
from app.providers.bybit import BybitClient

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
        or len(normalized) < 7
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
    orderbooks = snapshot.get("orderbooks") or {}
    errors = snapshot.get("orderbook_errors") or {}
    if not isinstance(orderbooks, dict) or not isinstance(errors, dict):
        raise HTTPException(status_code=400, detail="orderbook coverage fields are invalid")

    conn = await asyncpg.connect(settings.database_url)
    try:
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL)
            result = await conn.execute(
                """
                INSERT INTO swing_liquidity_forward_captures (
                    captured_at, study, source_commit_sha, scan_data_as_of,
                    candidate_count, orderbook_count, orderbook_error_count
                ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (captured_at) DO NOTHING
                """,
                captured_at,
                STUDY,
                os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
                scan_data_as_of,
                len(candidates),
                len(orderbooks),
                len(errors),
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
    finally:
        await conn.close()

    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": STUDY,
        "captured_at": captured_at.isoformat(),
        "inserted": inserted_capture,
        "candidate_count": len(candidates),
        "orderbook_count": len(orderbooks),
        "orderbook_error_count": len(errors),
    }


async def forward_status() -> dict[str, Any]:
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(SCHEMA_SQL)
        capture = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS capture_count,
                   MIN(captured_at) AS first_capture_at,
                   MAX(captured_at) AS last_capture_at,
                   COALESCE(SUM(candidate_count), 0)::int AS candidate_observations,
                   COALESCE(SUM(orderbook_error_count), 0)::int AS orderbook_errors
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
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": STUDY,
        "capture_count": int(capture["capture_count"] or 0),
        "first_capture_at": capture["first_capture_at"].isoformat() if capture["first_capture_at"] else None,
        "last_capture_at": capture["last_capture_at"].isoformat() if capture["last_capture_at"] else None,
        "candidate_observations": int(capture["candidate_observations"] or 0),
        "orderbook_errors": int(capture["orderbook_errors"] or 0),
        "turnover_tiers": {row["turnover_tier"]: row["n"] for row in tier_rows},
        "spread_tiers": {row["spread_tier"]: row["n"] for row in spread_rows},
        "development_target_matured_events": 60,
        "validation_target_matured_events": 40,
        "note": "Observation counts are prospective covariates, not matured independent trigger events.",
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
