"""Research-only Event & Tokenomics Intelligence v1 capture/status API."""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

import asyncpg
from research.research_snapshot_history import append_snapshot_history
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.bls_schedule_fallback import embedded_bls_2026_events

from research.event_tokenomics_shadow import (
    SPEC_VERSION,
    build_snapshot,
    severity_from_impact,
    severity_from_unlock_pct_market_cap,
    spec,
)

BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FOMC_SOURCE_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BYBIT_ANNOUNCEMENTS_URL = "https://api.bybit.com/v5/announcements/index"
COINMARKETCAL_URL = "https://api.coinmarketcal.com/v2/events"
TOKENOMIST_BASE_URL = "https://api.tokenomist.ai"
DEFAULT_SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC", "HYPEUSDC", "ADAUSDC", "CRVUSDC", "XLMUSDC"]
NY = ZoneInfo("America/New_York")

# Federal Reserve official calendar dates captured in v1. Future meeting dates are
# explicitly marked tentative because the Fed itself states future dates are
# tentative until confirmed at the preceding meeting.
FOMC_SCHEDULE = [
    ("2026-08-19", "MACRO_FOMC_MINUTES", "FOMC Minutes — July 28–29 meeting", "HIGH", False),
    ("2026-09-16", "MACRO_FOMC_DECISION", "FOMC policy decision — September meeting", "CRITICAL", True),
    ("2026-10-28", "MACRO_FOMC_DECISION", "FOMC policy decision — October meeting", "CRITICAL", True),
    ("2026-12-09", "MACRO_FOMC_DECISION", "FOMC policy decision — December meeting", "CRITICAL", True),
    ("2027-01-27", "MACRO_FOMC_DECISION", "FOMC policy decision — January meeting", "CRITICAL", True),
    ("2027-03-17", "MACRO_FOMC_DECISION", "FOMC policy decision — March meeting", "CRITICAL", True),
    ("2027-04-28", "MACRO_FOMC_DECISION", "FOMC policy decision — April meeting", "CRITICAL", True),
    ("2027-06-09", "MACRO_FOMC_DECISION", "FOMC policy decision — June meeting", "CRITICAL", True),
    ("2027-07-28", "MACRO_FOMC_DECISION", "FOMC policy decision — July meeting", "CRITICAL", True),
    ("2027-09-15", "MACRO_FOMC_DECISION", "FOMC policy decision — September meeting", "CRITICAL", True),
    ("2027-10-27", "MACRO_FOMC_DECISION", "FOMC policy decision — October meeting", "CRITICAL", True),
    ("2027-12-08", "MACRO_FOMC_DECISION", "FOMC policy decision — December meeting", "CRITICAL", True),
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_event_tokenomics_events (
    spec_version TEXT NOT NULL,
    event_id TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    event_at TIMESTAMPTZ,
    event_type TEXT NOT NULL,
    source_name TEXT,
    payload JSONB NOT NULL,
    PRIMARY KEY (spec_version, event_id)
);
CREATE INDEX IF NOT EXISTS idx_research_event_tokenomics_event_at
ON research_event_tokenomics_events(event_at DESC);
CREATE TABLE IF NOT EXISTS research_event_tokenomics_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    event_count INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_event_tokenomics_snapshot_time
ON research_event_tokenomics_snapshots(captured_at DESC);
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


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _source_status(status: str, *, events: int = 0, reason: str | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status, "events": int(events)}
    if reason:
        payload["reason"] = reason
    payload.update(extra)
    return payload


def _unfold_ics(text: str) -> list[str]:
    output: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and output:
            output[-1] += line[1:]
        else:
            output.append(line)
    return output


def _parse_ics_datetime(key: str, value: str) -> tuple[datetime | None, str]:
    raw = value.strip()
    tz_name = None
    match = re.search(r"TZID=([^;:]+)", key)
    if match:
        tz_name = match.group(1)
    try:
        if raw.endswith("Z"):
            for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%MZ"):
                try:
                    return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc), "EXACT"
                except ValueError:
                    pass
        if "T" in raw:
            for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
                try:
                    local = datetime.strptime(raw, fmt)
                    tz = ZoneInfo(tz_name) if tz_name else timezone.utc
                    return local.replace(tzinfo=tz).astimezone(timezone.utc), "EXACT"
                except ValueError:
                    pass
        if len(raw) == 8:
            return datetime.strptime(raw, "%Y%m%d").replace(hour=12, tzinfo=timezone.utc), "DAY"
    except (ValueError, KeyError):
        return None, "UNKNOWN"
    return None, "UNKNOWN"


def parse_bls_ics(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                rows.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key] = value

    mapping = [
        ("consumer price index", "MACRO_CPI", "HIGH"),
        ("employment situation", "MACRO_JOBS", "HIGH"),
        ("producer price index", "MACRO_PPI", "MEDIUM_HIGH"),
        ("job openings and labor turnover", "MACRO_JOLTS", "MEDIUM_HIGH"),
        ("employment cost index", "MACRO_ECI", "MEDIUM"),
    ]
    events: list[dict[str, Any]] = []
    for row in rows:
        summary = next((value for key, value in row.items() if key.startswith("SUMMARY")), "")
        lower = summary.lower()
        matched = next((item for item in mapping if item[0] in lower), None)
        if matched is None:
            continue
        dt_key = next((key for key in row if key.startswith("DTSTART")), "")
        dt, precision = _parse_ics_datetime(dt_key, row.get(dt_key, ""))
        if dt is None:
            continue
        _, event_type, severity = matched
        uid = next((value for key, value in row.items() if key.startswith("UID")), "")
        stable = uid or f"{event_type}:{dt.isoformat()}"
        events.append({
            "event_id": f"bls:{stable}",
            "event_type": event_type,
            "title": summary,
            "event_at": dt.isoformat(),
            "date_precision": precision,
            "severity": severity,
            "symbols": [],
            "source": {"name": "U.S. Bureau of Labor Statistics", "url": BLS_ICS_URL, "official": True},
        })
    return events


def fomc_schedule_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for date_text, event_type, title, severity, tentative in FOMC_SCHEDULE:
        local = datetime.fromisoformat(date_text).replace(hour=14, tzinfo=NY)
        dt = local.astimezone(timezone.utc)
        events.append({
            "event_id": f"fed:{event_type}:{date_text}",
            "event_type": event_type,
            "title": title,
            "event_at": dt.isoformat(),
            "date_precision": "DAY" if event_type == "MACRO_FOMC_MINUTES" else "EXACT",
            "severity": severity,
            "symbols": [],
            "source": {"name": "Federal Reserve", "url": FOMC_SOURCE_URL, "official": True},
            "metadata": {"schedule_tentative": tentative},
        })
    return events


def _match_symbols(text: str, tracked_symbols: Iterable[str]) -> list[str]:
    upper = text.upper()
    matches: list[str] = []
    for symbol in tracked_symbols:
        normalized = str(symbol).upper()
        if not normalized.endswith("USDC"):
            continue
        base = normalized[:-4]
        if re.search(rf"(?<![A-Z0-9]){re.escape(base)}(?![A-Z0-9])", upper):
            matches.append(normalized)
    return sorted(set(matches))


def normalize_bybit_announcements(payload: Mapping[str, Any], tracked_symbols: Iterable[str]) -> list[dict[str, Any]]:
    result = payload.get("result") or {}
    rows = result.get("list") or []
    events: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        type_obj = row.get("type") or {}
        type_key = str(type_obj.get("key") if isinstance(type_obj, Mapping) else "").lower()
        tags = [str(item) for item in (row.get("tags") or [])]
        title = str(row.get("title") or "")
        description = str(row.get("description") or "")
        haystack = " ".join([title, description, *tags])
        lower = haystack.lower()
        if type_key == "delistings" or "delist" in lower:
            event_type, severity = "EXCHANGE_DELISTING", "HIGH"
        elif type_key == "new_crypto" or "spot listings" in lower or "new listing" in lower:
            event_type, severity = "EXCHANGE_LISTING", "MEDIUM_HIGH"
        elif type_key == "maintenance_updates" or "maintenance" in lower:
            event_type, severity = "EXCHANGE_MAINTENANCE", "MEDIUM"
        elif any(token in lower for token in ("network upgrade", "hard fork", "token migration", "mainnet upgrade")):
            event_type, severity = "PROTOCOL_UPGRADE", "MEDIUM_HIGH"
        elif any(token in lower for token in ("airdrop", "launchpool", "token distribution")):
            event_type, severity = "TOKEN_DISTRIBUTION", "MEDIUM"
        else:
            continue
        symbols = _match_symbols(haystack, tracked_symbols)
        if not symbols and event_type not in {"EXCHANGE_MAINTENANCE"}:
            continue
        ts_ms = row.get("startDataTimestamp") or row.get("dateTimestamp") or row.get("publishTime")
        try:
            dt = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
        stable = str(row.get("url") or row.get("id") or f"{int(dt.timestamp())}:{index}")
        events.append({
            "event_id": f"bybit:{stable}",
            "event_type": event_type,
            "title": title,
            "event_at": dt.isoformat(),
            "date_precision": "EXACT",
            "severity": severity,
            "symbols": symbols,
            "source": {
                "name": "Bybit official announcements",
                "url": row.get("url") or BYBIT_ANNOUNCEMENTS_URL,
                "official": True,
                "is_bybit_eu_specific": False,
            },
            "metadata": {"announcement_type": type_key, "tags": tags},
        })
    return events


def normalize_coinmarketcal(payload: Mapping[str, Any], tracked_symbols: Iterable[str]) -> list[dict[str, Any]]:
    tracked_bases = {str(symbol).upper()[:-4]: str(symbol).upper() for symbol in tracked_symbols if str(symbol).upper().endswith("USDC")}
    events: list[dict[str, Any]] = []
    for row in payload.get("data") or []:
        if not isinstance(row, Mapping):
            continue
        symbols = sorted({tracked_bases.get(str(coin.get("symbol") or "").upper()) for coin in (row.get("coins") or []) if isinstance(coin, Mapping)} - {None})
        if not symbols:
            continue
        categories = [str(item) for item in (row.get("categories") or [])]
        lower = " ".join(categories).lower()
        if "tokenomics" in lower:
            event_type = "OTHER"
        elif any(token in lower for token in ("release", "integration", "fork", "swap")):
            event_type = "PROTOCOL_RELEASE"
        elif any(token in lower for token in ("airdrop", "snapshot", "staking")):
            event_type = "TOKEN_DISTRIBUTION"
        elif any(token in lower for token in ("exchange", "listing")):
            event_type = "EXCHANGE_LISTING"
        elif "regulation" in lower:
            event_type = "REGULATORY_EVENT"
        else:
            event_type = "OTHER"
        impact = _safe_float(row.get("impact"))
        events.append({
            "event_id": f"coinmarketcal:{row.get('id') or row.get('slug')}",
            "event_type": event_type,
            "title": str(row.get("title") or ""),
            "event_at": row.get("date"),
            "display_date": row.get("displayedDate"),
            "date_precision": str(row.get("dateType") or "DATE").upper(),
            "is_estimated": bool(row.get("isEstimated")),
            "severity": severity_from_impact(impact),
            "symbols": symbols,
            "source": {"name": "CoinMarketCal", "url": row.get("sourceUrl") or "https://coinmarketcal.com", "official": False},
            "metadata": {"impact": impact, "categories": categories, "last_verified_at": row.get("lastVerifiedAt")},
        })
    return events


def normalize_tokenomist_unlocks(payload: Mapping[str, Any], tracked_symbols: Iterable[str]) -> list[dict[str, Any]]:
    tracked_bases = {str(symbol).upper()[:-4]: str(symbol).upper() for symbol in tracked_symbols if str(symbol).upper().endswith("USDC")}
    events: list[dict[str, Any]] = []
    for row in payload.get("data") or []:
        if not isinstance(row, Mapping):
            continue
        base = str(row.get("tokenSymbol") or "").upper()
        symbol = tracked_bases.get(base)
        if symbol is None:
            continue
        upcoming = row.get("upcomingEvent") or {}
        cliff = upcoming.get("cliffUnlocks") or {} if isinstance(upcoming, Mapping) else {}
        pct_mcap = _safe_float(cliff.get("valueToMarketCap") if isinstance(cliff, Mapping) else None)
        value_usd = _safe_float(cliff.get("totalCliffValue") if isinstance(cliff, Mapping) else None)
        amount = _safe_float(cliff.get("totalCliffAmount") if isinstance(cliff, Mapping) else None)
        event_at = upcoming.get("unlockDate") if isinstance(upcoming, Mapping) else None
        if not event_at:
            continue
        events.append({
            "event_id": f"tokenomist:unlock:{row.get('tokenId') or base}:{event_at}",
            "event_type": "TOKEN_UNLOCK",
            "title": f"{base} token unlock",
            "event_at": event_at,
            "date_precision": "EXACT",
            "severity": severity_from_unlock_pct_market_cap(pct_mcap),
            "symbols": [symbol],
            "source": {"name": "Tokenomist", "url": f"https://tokenomist.ai/{row.get('tokenId') or ''}", "official": False, "data_source": row.get("dataSource")},
            "tokenomics": {
                "token_amount": amount,
                "value_usd": value_usd,
                "value_to_market_cap_pct": pct_mcap,
                "released_percentage": _safe_float(row.get("releasedPercentage")),
            },
        })
    return events


def normalize_tokenomist_supply_action(payload: Mapping[str, Any], tracked_symbol: str, action: str) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, Mapping):
        return []
    key = "burns" if action == "burn" else "buybacks"
    rows = data.get(key) or []
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        event_at = row.get("burnDate") if action == "burn" else row.get("buybackDate")
        if not event_at:
            continue
        amount = _safe_float(row.get("amount") if action == "burn" else row.get("tokenAmount"))
        value = _safe_float(row.get("value"))
        label = row.get("burnEventLabel") if action == "burn" else row.get("buybackEventLabel")
        result.append({
            "event_id": f"tokenomist:{action}:{data.get('tokenId') or tracked_symbol}:{event_at}:{index}",
            "event_type": "TOKEN_BURN" if action == "burn" else "TOKEN_BUYBACK",
            "title": str(label or f"{tracked_symbol[:-4]} token {action}"),
            "event_at": event_at,
            "date_precision": "EXACT",
            "severity": "MEDIUM_HIGH" if value and value >= 1_000_000 else "MEDIUM",
            "symbols": [tracked_symbol],
            "source": {"name": "Tokenomist", "url": f"https://tokenomist.ai/{data.get('tokenId') or ''}", "official": False},
            "tokenomics": {"token_amount": amount, "value_usd": value, "action": action},
        })
    return result


async def _load_tracked_symbols(connection: asyncpg.Connection) -> list[str]:
    symbols = set(DEFAULT_SYMBOLS)
    try:
        rows = await connection.fetch(
            """
            SELECT cache_key FROM radar_cache
            WHERE cache_key LIKE 'day_trade_setup:%'
            ORDER BY updated_at DESC LIMIT 60
            """
        )
        for row in rows:
            symbol = str(row["cache_key"]).split(":", 1)[-1].upper()
            if symbol.endswith("USDC"):
                symbols.add(symbol)
    except Exception:
        pass
    try:
        row = await connection.fetchrow(
            """SELECT payload FROM research_market_regime_snapshots
               WHERE spec_version='market-regime-shadow-v1'
               ORDER BY captured_at DESC LIMIT 1"""
        )
        if row:
            payload = _decode(row["payload"])
            raw = payload.get("symbols") or []
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, Mapping):
                        symbol = str(item.get("symbol") or "").upper()
                        if symbol.endswith("USDC"):
                            symbols.add(symbol)
            elif isinstance(raw, Mapping):
                symbols.update(str(key).upper() for key in raw if str(key).upper().endswith("USDC"))
    except Exception:
        pass
    majors = {symbol: index for index, symbol in enumerate(DEFAULT_SYMBOLS)}
    return sorted(symbols, key=lambda symbol: (majors.get(symbol, 100), symbol))[:30]


async def _fetch_bls(client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        response = await client.get(BLS_ICS_URL)
        response.raise_for_status()
        events = parse_bls_ics(response.text)
        return events, _source_status("LIVE", events=len(events), official=True, url=BLS_ICS_URL)
    except Exception as exc:
        events = embedded_bls_2026_events()
        return events, _source_status(
            "PARTIAL",
            events=len(events),
            official=True,
            url=BLS_ICS_URL,
            fallback_mode="EMBEDDED_OFFICIAL_2026",
            reason=f"{type(exc).__name__}: {str(exc)[:120]}",
        )


async def _fetch_bybit(client: httpx.AsyncClient, tracked: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response = await client.get(BYBIT_ANNOUNCEMENTS_URL, params={"locale": "en-US", "limit": 50})
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("retCode", 0)) != 0:
        raise RuntimeError(f"Bybit announcement error {payload.get('retCode')}")
    events = normalize_bybit_announcements(payload, tracked)
    return events, _source_status("LIVE", events=len(events), official=True, scope="GLOBAL_CONTEXT", url=BYBIT_ANNOUNCEMENTS_URL)


async def _fetch_coinmarketcal(client: httpx.AsyncClient, tracked: list[str], now: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = os.getenv("COINMARKETCAL_API_KEY", "").strip()
    if not key:
        return [], _source_status("MISSING_KEY", reason="COINMARKETCAL_API_KEY not configured")
    response = await client.get(
        COINMARKETCAL_URL,
        params={"since": now.isoformat(), "limit": 100, "impactMin": 5.0},
        headers={"x-api-key": key},
    )
    response.raise_for_status()
    events = normalize_coinmarketcal(response.json(), tracked)
    return events, _source_status("LIVE", events=len(events), url=COINMARKETCAL_URL)


async def _fetch_tokenomist_unlocks(client: httpx.AsyncClient, tracked: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = os.getenv("TOKENOMIST_API_KEY", "").strip()
    if not key:
        return [], _source_status("MISSING_KEY", reason="TOKENOMIST_API_KEY not configured")
    url = f"{TOKENOMIST_BASE_URL}/v1/unlock/events/upcoming"
    response = await client.get(url, headers={"x-api-key": key})
    response.raise_for_status()
    events = normalize_tokenomist_unlocks(response.json(), tracked)
    return events, _source_status("LIVE", events=len(events), url=url)


async def _fetch_tokenomist_supply_actions(client: httpx.AsyncClient, tracked: list[str], now: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = os.getenv("TOKENOMIST_API_KEY", "").strip()
    if not key:
        return [], _source_status("MISSING_KEY", reason="TOKENOMIST_API_KEY not configured")
    token_url = f"{TOKENOMIST_BASE_URL}/v4/token/list"
    token_response = await client.get(token_url, headers={"x-api-key": key})
    token_response.raise_for_status()
    token_rows = token_response.json().get("data") or []
    tracked_map = {symbol[:-4]: symbol for symbol in tracked}
    selected = [row for row in token_rows if isinstance(row, Mapping) and str(row.get("symbol") or "").upper() in tracked_map]
    start = (now - timedelta(days=2)).date().isoformat()
    end = (now + timedelta(days=1)).date().isoformat()
    events: list[dict[str, Any]] = []
    errors = 0
    calls = 0
    for row in selected:
        token_id = str(row.get("id") or "")
        symbol = tracked_map[str(row.get("symbol") or "").upper()]
        for action, flag in (("burn", bool(row.get("hasBurn"))), ("buyback", bool(row.get("hasBuyback")))):
            if not flag or not token_id:
                continue
            calls += 1
            try:
                response = await client.get(
                    f"{TOKENOMIST_BASE_URL}/v1/{action}/{token_id}",
                    params={"start": start, "end": end},
                    headers={"x-api-key": key},
                )
                response.raise_for_status()
                events.extend(normalize_tokenomist_supply_action(response.json(), symbol, action))
            except Exception:
                errors += 1
    status = "PARTIAL" if errors else "LIVE"
    return events, _source_status(status, events=len(events), calls=calls, failed_calls=errors, url=token_url)


async def _safe_provider(name: str, awaitable: Any) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    try:
        events, status = await awaitable
        return name, events, status
    except Exception as exc:
        return name, [], _source_status("ERROR", reason=f"{type(exc).__name__}: {str(exc)[:180]}")


async def build_current_snapshot() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        tracked = await _load_tracked_symbols(connection)
    finally:
        await connection.close()

    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "bybit-eu-event-tokenomics-shadow/1"}) as client:
        provider_results = await asyncio.gather(
            _safe_provider("bls_macro", _fetch_bls(client)),
            _safe_provider("bybit_announcements", _fetch_bybit(client, tracked)),
            _safe_provider("coinmarketcal", _fetch_coinmarketcal(client, tracked, now)),
            _safe_provider("tokenomist_unlocks", _fetch_tokenomist_unlocks(client, tracked)),
            _safe_provider("tokenomist_supply_actions", _fetch_tokenomist_supply_actions(client, tracked, now)),
        )

    all_events = fomc_schedule_events()
    source_status: dict[str, dict[str, Any]] = {
        "fomc_schedule": _source_status("LIVE", events=len(FOMC_SCHEDULE), official=True, url=FOMC_SOURCE_URL)
    }
    for name, events, status in provider_results:
        all_events.extend(events)
        source_status[name] = status

    return build_snapshot(
        all_events,
        source_status,
        tracked,
        captured_at=now,
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
    )


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        async with connection.transaction():
            for event in snapshot.get("events") or []:
                source = event.get("source") or {}
                event_at = event.get("event_at")
                parsed_event_at = datetime.fromisoformat(str(event_at).replace("Z", "+00:00")) if event_at else None
                await connection.execute(
                    """
                    INSERT INTO research_event_tokenomics_events (
                        spec_version,event_id,first_seen_at,last_seen_at,event_at,event_type,source_name,payload
                    ) VALUES ($1,$2,$3,$3,$4,$5,$6,$7::jsonb)
                    ON CONFLICT (spec_version,event_id) DO UPDATE SET
                        last_seen_at=EXCLUDED.last_seen_at,
                        event_at=EXCLUDED.event_at,
                        event_type=EXCLUDED.event_type,
                        source_name=EXCLUDED.source_name,
                        payload=EXCLUDED.payload
                    """,
                    SPEC_VERSION,
                    event["event_id"],
                    captured_at,
                    parsed_event_at,
                    event.get("event_type"),
                    source.get("name"),
                    json.dumps(event, separators=(",", ":")),
                )
            history = await append_snapshot_history(
                connection,
                research_family="event-tokenomics",
                spec_version=SPEC_VERSION,
                captured_at=captured_at,
                capture_bucket=captured_hour,
                source_commit_sha=snapshot.get("source_commit_sha"),
                snapshot=snapshot,
            )
            snapshot["immutable_history"] = history
            await connection.execute(
                """
                INSERT INTO research_event_tokenomics_snapshots (
                    spec_version,captured_hour,captured_at,source_commit_sha,event_count,payload,updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,NOW())
                ON CONFLICT (spec_version,captured_hour) DO UPDATE SET
                    captured_at=EXCLUDED.captured_at,
                    source_commit_sha=EXCLUDED.source_commit_sha,
                    event_count=EXCLUDED.event_count,
                    payload=EXCLUDED.payload,
                    updated_at=NOW()
                """,
                SPEC_VERSION,
                captured_hour,
                captured_at,
                snapshot.get("source_commit_sha"),
                int(snapshot.get("event_count") or 0),
                json.dumps(snapshot, separators=(",", ":")),
            )
    finally:
        await connection.close()
    return {**snapshot, "persisted": True, "captured_hour": captured_hour.isoformat()}


async def capture_current_snapshot() -> dict[str, Any]:
    return await persist_snapshot(await build_current_snapshot())


async def status_payload() -> dict[str, Any]:
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        latest = await connection.fetchrow(
            """SELECT captured_hour,source_commit_sha,payload
               FROM research_event_tokenomics_snapshots
               WHERE spec_version=$1 ORDER BY captured_at DESC LIMIT 1""",
            SPEC_VERSION,
        )
        snapshot_count = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_event_tokenomics_snapshots WHERE spec_version=$1",
            SPEC_VERSION,
        )
        event_count = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_event_tokenomics_events WHERE spec_version=$1",
            SPEC_VERSION,
        )
    finally:
        await connection.close()
    latest_payload = None
    if latest:
        latest_payload = _decode(latest["payload"])
        latest_payload["captured_hour"] = latest["captured_hour"].isoformat()
        latest_payload["source_commit_sha"] = latest["source_commit_sha"]
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec": spec(),
        "snapshot_count": int(snapshot_count or 0),
        "observed_event_count": int(event_count or 0),
        "latest": latest_payload,
    }


def attach_event_tokenomics_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get(
        "/v1/research/event-tokenomics/spec",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def event_tokenomics_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/event-tokenomics/capture",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def event_tokenomics_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Research event-tokenomics capture unavailable: {type(exc).__name__}") from exc

    @app.get(
        "/v1/research/event-tokenomics/status",
        dependencies=[Depends(require_api_key)],
        include_in_schema=False,
    )
    async def event_tokenomics_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Research event-tokenomics status unavailable: {type(exc).__name__}") from exc
