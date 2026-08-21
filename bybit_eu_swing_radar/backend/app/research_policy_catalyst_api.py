"""Research-only primary-source policy/liquidity catalyst capture API."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.policy_catalyst_feed_v1 import (
    DEFAULT_EVENT_LOOKBACK_HOURS,
    SPEC_VERSION,
    build_snapshot,
    extract_published_at_from_html,
    normalize_event,
    parse_official_html_index,
    parse_rss_or_atom,
    spec,
)
from research.policy_catalyst_sources_v1 import enabled_source_registry

MAX_HTML_DETAILS_PER_SOURCE = 12
MAX_CAPTURE_EVENTS = 100
CAPTURE_FRESH_SECONDS = 30 * 60

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_policy_catalyst_events (
    spec_version TEXT NOT NULL,
    event_id TEXT NOT NULL,
    provider_code TEXT NOT NULL,
    primary_event_class TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    headline TEXT NOT NULL,
    source_url TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, event_id)
);
CREATE INDEX IF NOT EXISTS idx_research_policy_catalyst_first_seen
ON research_policy_catalyst_events(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_policy_catalyst_published
ON research_policy_catalyst_events(published_at DESC);

CREATE TABLE IF NOT EXISTS research_policy_catalyst_captures (
    spec_version TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    data_quality TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_research_policy_catalyst_capture_time
ON research_policy_catalyst_captures(captured_at DESC);
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        loaded = json.loads(value)
        return dict(loaded) if isinstance(loaded, Mapping) else {}
    return {}


def _parse_iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _within_lookback(event: Mapping[str, Any], now: datetime) -> bool:
    published = _parse_iso(event.get("published_at"))
    if published is None:
        return True
    return published >= now - timedelta(hours=DEFAULT_EVENT_LOOKBACK_HOURS)


async def _fetch_source(
    client: httpx.AsyncClient,
    source: Mapping[str, Any],
    *,
    captured_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider_code = str(source["provider_code"])
    fetch_url = str(source["fetch_url"])
    parser_mode = str(source["parser_mode"])
    started = datetime.now(timezone.utc)
    response = await client.get(fetch_url)
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    detail_errors = 0

    if parser_mode == "RSS":
        events = parse_rss_or_atom(
            response.text,
            provider_code=provider_code,
            captured_at=captured_at,
        )
        events = [event for event in events if _within_lookback(event, captured_at)]
    elif parser_mode == "HTML_INDEX":
        rows = parse_official_html_index(
            response.text,
            provider_code=provider_code,
            base_url=str(source["monitor_url"]),
            allowed_prefixes=list(source.get("allowed_path_prefixes") or []),
        )
        for row in rows[:MAX_HTML_DETAILS_PER_SOURCE]:
            try:
                detail = await client.get(row["url"])
                detail.raise_for_status()
                published = extract_published_at_from_html(detail.text)
                normalized = normalize_event(
                    provider_code=provider_code,
                    headline=row["headline"],
                    url=row["url"],
                    published_at=published,
                    captured_at=captured_at,
                )
                if normalized is not None and _within_lookback(normalized, captured_at):
                    events.append(normalized)
            except Exception:
                detail_errors += 1
    else:
        raise ValueError(f"Unsupported policy source parser mode: {parser_mode}")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return events, {
        "provider": source["provider"],
        "provider_code": provider_code,
        "authority_tier": source["authority_tier"],
        "fetch_url": fetch_url,
        "parser_mode": parser_mode,
        "status": "OK",
        "http_status": response.status_code,
        "event_count": len(events),
        "detail_errors": detail_errors,
        "captured_at": captured_at.isoformat(),
        "latency_seconds": round(elapsed, 3),
    }


async def build_current_snapshot() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    events: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    timeout = httpx.Timeout(15.0, connect=7.0)
    headers = {
        "User-Agent": "bybit-eu-trading-radar-policy-context/1.0 robireklam-hub@users.noreply.github.com",
        "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.5",
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for source in enabled_source_registry():
            try:
                source_events, source_result = await _fetch_source(client, source, captured_at=now)
                events.extend(source_events)
                source_results.append(source_result)
            except Exception as exc:
                source_results.append(
                    {
                        "provider": source["provider"],
                        "provider_code": source["provider_code"],
                        "authority_tier": source["authority_tier"],
                        "fetch_url": source["fetch_url"],
                        "parser_mode": source["parser_mode"],
                        "status": "ERROR",
                        "error": type(exc).__name__,
                        "captured_at": now.isoformat(),
                        "event_count": 0,
                    }
                )
    snapshot = build_snapshot(
        events[:MAX_CAPTURE_EVENTS],
        source_results=source_results,
        captured_at=now,
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
    )
    return snapshot


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = _parse_iso(snapshot.get("captured_at"))
    if captured_at is None:
        raise ValueError("snapshot captured_at is invalid")
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        persisted_events: list[dict[str, Any]] = []
        for raw_event in snapshot.get("events") or []:
            event = dict(raw_event)
            existing_first_seen = await connection.fetchval(
                """
                SELECT first_seen_at
                FROM research_policy_catalyst_events
                WHERE spec_version=$1 AND event_id=$2
                """,
                SPEC_VERSION,
                event["event_id"],
            )
            first_seen_at = existing_first_seen or captured_at
            event["first_seen_at"] = first_seen_at.isoformat()
            await connection.execute(
                """
                INSERT INTO research_policy_catalyst_events (
                    spec_version,event_id,provider_code,primary_event_class,published_at,
                    first_seen_at,last_seen_at,headline,source_url,payload
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                ON CONFLICT (spec_version,event_id) DO UPDATE SET
                    published_at=COALESCE(research_policy_catalyst_events.published_at, EXCLUDED.published_at),
                    last_seen_at=EXCLUDED.last_seen_at,
                    headline=EXCLUDED.headline,
                    primary_event_class=EXCLUDED.primary_event_class,
                    payload=EXCLUDED.payload
                """,
                SPEC_VERSION,
                event["event_id"],
                event["provider_code"],
                event["primary_event_class"],
                _parse_iso(event.get("published_at")),
                first_seen_at,
                captured_at,
                event["headline"],
                event["url"],
                json.dumps(event, separators=(",", ":")),
            )
            persisted_events.append(event)
        snapshot["events"] = persisted_events
        await connection.execute(
            """
            INSERT INTO research_policy_catalyst_captures (
                spec_version,captured_at,source_commit_sha,data_quality,payload
            ) VALUES ($1,$2,$3,$4,$5::jsonb)
            ON CONFLICT (spec_version,captured_at) DO NOTHING
            """,
            SPEC_VERSION,
            captured_at,
            snapshot.get("source_commit_sha"),
            str(snapshot.get("data_quality") or "DEGRADED"),
            json.dumps(snapshot, separators=(",", ":")),
        )
    finally:
        await connection.close()
    return {**snapshot, "persisted": True}


async def capture_current_snapshot() -> dict[str, Any]:
    return await persist_snapshot(await build_current_snapshot())


async def status_payload() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """
            SELECT captured_at,source_commit_sha,data_quality,payload
            FROM research_policy_catalyst_captures
            WHERE spec_version=$1
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            SPEC_VERSION,
        )
        recent = await connection.fetch(
            """
            SELECT provider_code,primary_event_class,published_at,first_seen_at,last_seen_at,
                   headline,source_url,payload
            FROM research_policy_catalyst_events
            WHERE spec_version=$1 AND first_seen_at >= NOW() - INTERVAL '24 hours'
            ORDER BY first_seen_at DESC
            LIMIT 25
            """,
            SPEC_VERSION,
        )
        event_count = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_policy_catalyst_events WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()

    latest_payload = _decode(latest["payload"]) if latest else None
    latest_at = latest["captured_at"] if latest else None
    age_seconds = max((now - latest_at).total_seconds(), 0.0) if latest_at else None
    freshness = (
        "FRESH"
        if age_seconds is not None and age_seconds <= CAPTURE_FRESH_SECONDS
        else "STALE"
        if age_seconds is not None
        else "UNAVAILABLE"
    )
    recent_events = []
    for row in recent:
        payload = _decode(row["payload"])
        payload.update(
            {
                "provider_code": row["provider_code"],
                "primary_event_class": row["primary_event_class"],
                "published_at": row["published_at"].isoformat() if row["published_at"] else None,
                "first_seen_at": row["first_seen_at"].isoformat(),
                "last_seen_at": row["last_seen_at"].isoformat(),
                "headline": row["headline"],
                "url": row["source_url"],
            }
        )
        recent_events.append(payload)
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "hard_gate": False,
        "live_strategy_mutated": False,
        "spec": spec(),
        "freshness": freshness,
        "latest_capture_at": latest_at.isoformat() if latest_at else None,
        "latest_capture_age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "latest_capture": latest_payload,
        "event_count": int(event_count or 0),
        "recent_24h_events": recent_events,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
    }


def attach_policy_catalyst_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get(
        "/v1/research/policy-catalyst/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def policy_catalyst_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/policy-catalyst/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def policy_catalyst_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research policy-catalyst capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/policy-catalyst/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def policy_catalyst_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research policy-catalyst status unavailable: {type(exc).__name__}",
            ) from exc
