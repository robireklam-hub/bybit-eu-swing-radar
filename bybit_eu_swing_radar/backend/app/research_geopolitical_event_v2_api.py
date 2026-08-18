"""Research-only GDELT Event 2.0 static-stream capture/status API."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.geopolitical_event_shadow_v2 import (
    PROVIDER,
    SPEC_VERSION,
    build_snapshot,
    normalize_event_columns,
    spec,
)

GDELT_LASTUPDATE_HTTPS = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_LASTUPDATE_HTTP = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_DATA_HOST = "data.gdeltproject.org"
MAX_COMPRESSED_BYTES = 75 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_EVENT_ROWS = 500_000
MAX_SOURCE_AGE_SECONDS = 3 * 60 * 60

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_geopolitical_event_v2_snapshots (
    spec_version TEXT NOT NULL,
    source_file_timestamp TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_file_url TEXT NOT NULL,
    source_file_md5 TEXT,
    source_commit_sha TEXT,
    data_quality TEXT NOT NULL,
    valid_rows INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, source_file_timestamp)
);
CREATE INDEX IF NOT EXISTS idx_research_geopolitical_event_v2_time
ON research_geopolitical_event_v2_snapshots(source_file_timestamp DESC);
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


def parse_lastupdate_manifest(text: str) -> dict[str, Any]:
    """Select the current Event Database export from GDELT lastupdate.txt."""
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if not parts:
            continue
        url = next(
            (part for part in parts if part.endswith(".export.CSV.zip")),
            None,
        )
        if not url:
            continue
        filename = url.rsplit("/", 1)[-1]
        match = re.fullmatch(r"(\d{14})\.export\.CSV\.zip", filename)
        if match is None:
            continue
        expected_bytes = None
        if parts and parts[0].isdigit():
            expected_bytes = int(parts[0])
        md5 = None
        for part in parts:
            if re.fullmatch(r"[0-9a-fA-F]{32}", part):
                md5 = part.lower()
                break
        timestamp = datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
        return {
            "manifest_url": None,
            "manifest_transport": None,
            "manifest_declared_bytes": expected_bytes,
            "manifest_md5": md5,
            "source_file_timestamp": timestamp,
            "source_file_url_manifest": url,
            "source_filename": filename,
        }
    raise ValueError("GDELT lastupdate manifest contains no Event 2.0 export file")


def _https_source_url(manifest_url: str) -> str:
    if manifest_url.startswith(f"http://{GDELT_DATA_HOST}/"):
        return "https://" + manifest_url[len("http://") :]
    return manifest_url


async def _fetch_manifest(client: httpx.AsyncClient) -> tuple[str, dict[str, Any]]:
    try:
        response = await client.get(GDELT_LASTUPDATE_HTTPS)
        response.raise_for_status()
        return response.text, {
            "manifest_url": GDELT_LASTUPDATE_HTTPS,
            "manifest_transport": "HTTPS",
            "manifest_transport_security": "TLS",
        }
    except (httpx.ConnectError, httpx.ConnectTimeout) as https_exc:
        response = await client.get(GDELT_LASTUPDATE_HTTP)
        response.raise_for_status()
        return response.text, {
            "manifest_url": GDELT_LASTUPDATE_HTTP,
            "manifest_transport": "HTTP",
            "manifest_transport_security": "PLAINTEXT_PROVIDER_FALLBACK",
            "manifest_transport_reason": f"HTTPS {type(https_exc).__name__}",
        }


async def _download_event_zip(
    client: httpx.AsyncClient,
    manifest_url: str,
) -> tuple[bytes, dict[str, Any]]:
    primary_url = _https_source_url(manifest_url)
    try:
        response = await client.get(primary_url)
        response.raise_for_status()
        content = response.content
        meta = {
            "download_url": primary_url,
            "download_transport": "HTTPS" if primary_url.startswith("https://") else "HTTP",
            "download_transport_security": "TLS" if primary_url.startswith("https://") else "PLAINTEXT",
        }
    except (httpx.ConnectError, httpx.ConnectTimeout) as https_exc:
        if not manifest_url.startswith("http://") or primary_url == manifest_url:
            raise
        response = await client.get(manifest_url)
        response.raise_for_status()
        content = response.content
        meta = {
            "download_url": manifest_url,
            "download_transport": "HTTP",
            "download_transport_security": "PLAINTEXT_PROVIDER_FALLBACK",
            "download_transport_reason": f"HTTPS {type(https_exc).__name__}",
        }
    if len(content) > MAX_COMPRESSED_BYTES:
        raise ValueError("GDELT Event export exceeds bounded compressed-size limit")
    return content, meta


def parse_event_zip(content: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse one bounded GDELT Event export ZIP using only stable core fields."""
    events: list[dict[str, Any]] = []
    total_rows = 0
    invalid_rows = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) != 1:
            raise ValueError("GDELT Event ZIP must contain exactly one data member")
        member = members[0]
        if member.file_size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("GDELT Event export exceeds bounded uncompressed-size limit")
        with archive.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            reader = csv.reader(text, delimiter="\t")
            for columns in reader:
                total_rows += 1
                if total_rows > MAX_EVENT_ROWS:
                    raise ValueError("GDELT Event export exceeds bounded row limit")
                normalized = normalize_event_columns(columns)
                if normalized is None:
                    invalid_rows += 1
                    continue
                events.append(normalized)
    return events, {
        "total_rows": total_rows,
        "valid_rows": len(events),
        "invalid_rows": invalid_rows,
        "zip_member": member.filename,
        "zip_uncompressed_bytes": member.file_size,
    }


async def _prospective_file_count_before(source_timestamp: datetime) -> int:
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        value = await connection.fetchval(
            """
            SELECT COUNT(*)::int
            FROM research_geopolitical_event_v2_snapshots
            WHERE spec_version=$1
              AND source_file_timestamp < $2
              AND source_file_timestamp >= $2 - INTERVAL '24 hours'
            """,
            SPEC_VERSION,
            source_timestamp,
        )
        return int(value or 0)
    finally:
        await connection.close()


async def build_current_snapshot() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    timeout = httpx.Timeout(45.0, connect=12.0)
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        headers={"User-Agent": "bybit-eu-geopolitical-event-shadow-v2/1"},
    ) as client:
        manifest_text, manifest_transport = await _fetch_manifest(client)
        source = parse_lastupdate_manifest(manifest_text)
        source.update(manifest_transport)
        source_timestamp = source["source_file_timestamp"]
        if source_timestamp > now + timedelta(minutes=5):
            raise ValueError("GDELT source file timestamp is unexpectedly in the future")
        source_age = max(0.0, (now - source_timestamp).total_seconds())
        source["source_age_seconds"] = round(source_age, 3)
        source["freshness"] = (
            "FRESH" if source_age <= MAX_SOURCE_AGE_SECONDS else "STALE"
        )

        content, download_transport = await _download_event_zip(
            client, str(source["source_file_url_manifest"])
        )
        source.update(download_transport)

    actual_md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()
    source["actual_compressed_bytes"] = len(content)
    source["actual_md5"] = actual_md5
    declared_bytes = source.get("manifest_declared_bytes")
    if declared_bytes is not None and int(declared_bytes) != len(content):
        raise ValueError("GDELT Event export byte size does not match manifest")
    manifest_md5 = source.get("manifest_md5")
    if manifest_md5 and str(manifest_md5).lower() != actual_md5:
        raise ValueError("GDELT Event export MD5 does not match manifest")

    events, parse_meta = parse_event_zip(content)
    source.update(parse_meta)
    prospective_count = await _prospective_file_count_before(source_timestamp)
    payload = build_snapshot(
        events,
        total_rows=int(parse_meta["total_rows"]),
        invalid_rows=int(parse_meta["invalid_rows"]),
        source_file={
            **source,
            "source_file_timestamp": source_timestamp.isoformat(),
        },
        captured_at=now,
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
        prospective_file_count=prospective_count,
    )
    if source.get("freshness") != "FRESH" and payload.get("data_quality") == "COMPLETE":
        payload["data_quality"] = "PARTIAL"
    return payload


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    source = snapshot.get("source_file") or {}
    source_timestamp = datetime.fromisoformat(
        str(source["source_file_timestamp"]).replace("Z", "+00:00")
    )
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        await connection.execute(
            """
            INSERT INTO research_geopolitical_event_v2_snapshots (
                spec_version,source_file_timestamp,captured_at,source_file_url,
                source_file_md5,source_commit_sha,data_quality,valid_rows,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,NOW())
            ON CONFLICT (spec_version,source_file_timestamp) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_file_url=EXCLUDED.source_file_url,
                source_file_md5=EXCLUDED.source_file_md5,
                source_commit_sha=EXCLUDED.source_commit_sha,
                data_quality=EXCLUDED.data_quality,
                valid_rows=EXCLUDED.valid_rows,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            source_timestamp,
            captured_at,
            str(source.get("download_url") or source.get("source_file_url_manifest") or ""),
            source.get("actual_md5"),
            snapshot.get("source_commit_sha"),
            str(snapshot.get("data_quality") or "DEGRADED"),
            int((snapshot.get("coverage") or {}).get("valid_rows") or 0),
            json.dumps(snapshot, separators=(",", ":")),
        )
    finally:
        await connection.close()
    return {**snapshot, "persisted": True}


async def capture_current_snapshot() -> dict[str, Any]:
    return await persist_snapshot(await build_current_snapshot())


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """
            SELECT source_file_timestamp,source_commit_sha,data_quality,payload
            FROM research_geopolitical_event_v2_snapshots
            WHERE spec_version=$1
            ORDER BY source_file_timestamp DESC LIMIT 1
            """,
            SPEC_VERSION,
        )
        snapshot_count = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_geopolitical_event_v2_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
        first_timestamp = await connection.fetchval(
            "SELECT MIN(source_file_timestamp) FROM research_geopolitical_event_v2_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()

    latest_payload = None
    if latest:
        latest_payload = _decode(latest["payload"])
        latest_payload["source_commit_sha"] = latest["source_commit_sha"]
        latest_payload["data_quality"] = latest["data_quality"]
        latest_payload["source_file_timestamp"] = latest["source_file_timestamp"].isoformat()
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "spec": spec(),
        "snapshot_count": int(snapshot_count or 0),
        "prospective_start_at": first_timestamp.isoformat() if first_timestamp else None,
        "latest": latest_payload,
    }


def attach_geopolitical_event_v2_research(
    app: FastAPI, require_api_key: Callable[..., Any]
) -> None:
    @app.get(
        "/v1/research/geopolitical-event-v2/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def geopolitical_event_v2_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/geopolitical-event-v2/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def geopolitical_event_v2_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research geopolitical-event-v2 capture unavailable: {type(exc).__name__}",
            ) from exc

    @app.get(
        "/v1/research/geopolitical-event-v2/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def geopolitical_event_v2_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Research geopolitical-event-v2 status unavailable: {type(exc).__name__}",
            ) from exc
