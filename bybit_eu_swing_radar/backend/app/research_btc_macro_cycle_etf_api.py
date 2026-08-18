"""Research-only BTC Macro / Cycle / ETF Intelligence v1 capture API."""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException

from research.btc_macro_cycle_etf_shadow import (
    SPEC_VERSION,
    build_snapshot,
    spec,
    summarize_btc_price,
    summarize_cycle,
    summarize_etf_rows,
    summarize_series,
)

MEMPOOL_TIP_URL = "https://mempool.space/api/blocks/tip/height"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
FRED_SERIES = {
    "us_10y_yield": "DGS10",
    "broad_usd_index": "DTWEXBGS",
    "fed_total_assets": "WALCL",
    "overnight_reverse_repo": "RRPONTSYD",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_btc_macro_cycle_etf_snapshots (
    spec_version TEXT NOT NULL,
    captured_hour TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source_commit_sha TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_version, captured_hour)
);
CREATE INDEX IF NOT EXISTS idx_research_btc_macro_cycle_etf_time
ON research_btc_macro_cycle_etf_snapshots(captured_at DESC);
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def _bybit_base_url() -> str:
    return os.getenv("BYBIT_BASE_URL", "https://api.bybit.eu").rstrip("/")


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _source_status(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, **extra}


def parse_fred_csv(text: str, series_id: str) -> list[tuple[str, float]]:
    reader = csv.DictReader(io.StringIO(text))
    rows: list[tuple[str, float]] = []
    for row in reader:
        date = row.get("DATE") or row.get("observation_date") or ""
        raw = row.get(series_id)
        if raw in (None, "", "."):
            continue
        try:
            rows.append((str(date), float(raw)))
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda item: item[0])
    return rows


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _parse_flow_millions(raw: str) -> float | None:
    text = str(raw).strip().replace(",", "")
    if text in {"", "-", "–", "—"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text) * 1_000_000.0
    except ValueError:
        return None
    return -value if negative else value


def parse_farside_html(text: str) -> list[dict[str, Any]]:
    parser = _TableParser()
    parser.feed(text)
    header: list[str] | None = None
    output: list[dict[str, Any]] = []
    for row in parser.rows:
        if row and row[0].strip().lower() == "date" and any(cell.strip().lower() == "total" for cell in row):
            header = row
            continue
        if header is None or len(row) < 2:
            continue
        try:
            date = datetime.strptime(row[0].strip(), "%d %b %Y").date().isoformat()
        except ValueError:
            continue
        values = row[1:]
        names = header[1:]
        mapping = {names[index]: values[index] for index in range(min(len(names), len(values)))}
        total_key = next((name for name in names if name.strip().lower() == "total"), None)
        total = _parse_flow_millions(mapping.get(total_key, "")) if total_key else None
        funds: dict[str, float | None] = {}
        for name in names:
            if name == total_key:
                continue
            funds[name] = _parse_flow_millions(mapping.get(name, ""))
        output.append({"date": date, "total_usd": total, "funds": funds})
    output.sort(key=lambda item: item["date"])
    return output


async def _fetch_cycle(client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    response = await client.get(MEMPOOL_TIP_URL)
    response.raise_for_status()
    tip = int(response.text.strip())
    return summarize_cycle(tip), _source_status("LIVE", provider="mempool.space", url=MEMPOOL_TIP_URL)


async def _fetch_btc_price(client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"{_bybit_base_url()}/v5/market/kline"
    response = await client.get(url, params={"category": "spot", "symbol": "BTCUSDC", "interval": "D", "limit": 300})
    response.raise_for_status()
    payload = response.json()
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit kline error {payload.get('retCode')}")
    day_start_ms = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    result = dict(payload.get("result") or {})
    rows = result.get("list") or []
    result["list"] = [row for row in rows if isinstance(row, (list, tuple)) and row and int(row[0]) < day_start_ms]
    closed_payload = {**payload, "result": result}
    return summarize_btc_price(closed_payload), _source_status("LIVE", provider="Bybit EU", symbol="BTCUSDC", closed_daily_only=True, url=url)


async def _fetch_fred_series(client: httpx.AsyncClient, name: str, series_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    response = await client.get(FRED_CSV_URL, params={"id": series_id})
    response.raise_for_status()
    points = parse_fred_csv(response.text, series_id)
    summary = summarize_series(points)
    return name, {"series_id": series_id, **summary}, _source_status(
        "LIVE", provider="FRED / Federal Reserve Bank of St. Louis", series_id=series_id,
        url=f"https://fred.stlouisfed.org/series/{series_id}",
    )


async def _fetch_etf(client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    response = await client.get(FARSIDE_URL)
    response.raise_for_status()
    rows = parse_farside_html(response.text)
    summary = summarize_etf_rows(rows)
    return summary, _source_status("LIVE", provider="Farside Investors", official=False, url=FARSIDE_URL)


async def _safe(name: str, awaitable: Any) -> tuple[str, Any, dict[str, Any]]:
    try:
        value, status = await awaitable
        return name, value, status
    except Exception as exc:
        return name, None, _source_status("ERROR", reason=f"{type(exc).__name__}: {str(exc)[:180]}")


async def build_current_snapshot() -> dict[str, Any]:
    timeout = httpx.Timeout(30.0, connect=10.0)
    headers = {
        "User-Agent": "bybit-eu-btc-macro-cycle-etf-shadow/1",
        "Accept": "text/html,application/json,text/csv,text/plain,*/*",
    }
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        cycle_name, cycle, cycle_status = await _safe("cycle", _fetch_cycle(client))
        price_name, btc_price, price_status = await _safe("btc_price", _fetch_btc_price(client))
        macro_results = []
        for name, series_id in FRED_SERIES.items():
            try:
                item_name, summary, status = await _fetch_fred_series(client, name, series_id)
                macro_results.append((item_name, summary, status))
            except Exception as exc:
                macro_results.append((name, None, _source_status("ERROR", reason=f"{type(exc).__name__}: {str(exc)[:180]}", series_id=series_id)))
        etf_name, etf, etf_status = await _safe("etf_flows", _fetch_etf(client))

    if cycle is None:
        raise RuntimeError("BTC cycle source unavailable")
    if btc_price is None:
        raise RuntimeError("BTCUSDC daily price source unavailable")

    macro: dict[str, Any] = {}
    status: dict[str, Any] = {cycle_name: cycle_status, price_name: price_status, etf_name: etf_status}
    for name, summary, item_status in macro_results:
        if summary is not None:
            macro[name] = summary
        status[f"fred_{name}"] = item_status

    return build_snapshot(
        cycle=cycle,
        btc_price=btc_price,
        macro=macro,
        etf=etf,
        source_status=status,
        source_commit_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
    )


async def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    captured_hour = captured_at.replace(minute=0, second=0, microsecond=0)
    connection = await asyncpg.connect(_database_url(), timeout=30)
    try:
        await connection.execute(SCHEMA_SQL)
        await connection.execute(
            """
            INSERT INTO research_btc_macro_cycle_etf_snapshots (
                spec_version,captured_hour,captured_at,source_commit_sha,payload,updated_at
            ) VALUES ($1,$2,$3,$4,$5::jsonb,NOW())
            ON CONFLICT (spec_version,captured_hour) DO UPDATE SET
                captured_at=EXCLUDED.captured_at,
                source_commit_sha=EXCLUDED.source_commit_sha,
                payload=EXCLUDED.payload,
                updated_at=NOW()
            """,
            SPEC_VERSION,
            captured_hour,
            captured_at,
            snapshot.get("source_commit_sha"),
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
            """
            SELECT captured_at,captured_hour,source_commit_sha,payload
            FROM research_btc_macro_cycle_etf_snapshots
            WHERE spec_version=$1
            ORDER BY captured_at DESC LIMIT 1
            """,
            SPEC_VERSION,
        )
        total = await connection.fetchval(
            "SELECT COUNT(*)::int FROM research_btc_macro_cycle_etf_snapshots WHERE spec_version=$1",
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
        "promotion_allowed": False,
        "spec": spec(),
        "snapshot_count": int(total or 0),
        "latest": latest_payload,
    }


def attach_btc_macro_cycle_etf_research(app: FastAPI, require_api_key: Callable[..., Any]) -> None:
    @app.get(
        "/v1/research/btc-macro-cycle-etf/spec",
        dependencies=[Depends(require_api_key)], include_in_schema=False,
    )
    async def btc_macro_cycle_etf_spec() -> dict[str, Any]:
        return spec()

    @app.post(
        "/v1/research/btc-macro-cycle-etf/capture",
        dependencies=[Depends(require_api_key)], include_in_schema=False,
    )
    async def btc_macro_cycle_etf_capture() -> dict[str, Any]:
        try:
            return await capture_current_snapshot()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Research BTC macro/cycle/ETF capture unavailable: {type(exc).__name__}") from exc

    @app.get(
        "/v1/research/btc-macro-cycle-etf/status",
        dependencies=[Depends(require_api_key)], include_in_schema=False,
    )
    async def btc_macro_cycle_etf_status() -> dict[str, Any]:
        try:
            return await status_payload()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Research BTC macro/cycle/ETF status unavailable: {type(exc).__name__}") from exc
