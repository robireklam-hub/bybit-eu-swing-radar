"""Official keyless fallbacks for macro-liquidity research series.

This module is research-only. It reproduces the source semantics of two FRED
series from their primary official publishers when fred.stlouisfed.org is not
reachable from production:
- WALCL from Federal Reserve Board H.4.1 total assets, Wednesday level.
- RRPONTSYD from NY Fed overnight reverse-repo operation results.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import httpx

from research.btc_macro_cycle_etf_shadow import summarize_series

H41_WALCL_URL = (
    "https://www.federalreserve.gov/datadownload/Output.aspx?filetype=csv&from=&label=include&"
    "lastobs=100&layout=seriescolumn&rel=H41&series=3ab1b33ad80c27bc5cc4f8122b7a6440&to=&type=package"
)
H41_WALCL_SERIES = "RESPPMA_N.WW"
NYFED_RRP_URL = "https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json"

SUPPORTED_SERIES = {"WALCL", "RRPONTSYD"}


def _h41_series_name(value: str) -> str:
    return value.strip().split("/")[-1]


def parse_h41_series(text: str, series_code: str) -> list[tuple[str, float]]:
    rows = [list(row) for row in csv.reader(io.StringIO(text))]
    header = next((row for row in rows if row and row[0].strip().lower() == "series"), None)
    data = next(
        (row for row in rows if row and _h41_series_name(row[0]) == series_code),
        None,
    )
    if header is None or data is None:
        raise ValueError(f"H.4.1 series not found: {series_code}")

    points: list[tuple[str, float]] = []
    for index in range(2, min(len(header), len(data))):
        date = header[index].strip()
        raw = data[index].strip().replace(",", "")
        if not date or raw in {"", "ND", "NA", "."}:
            continue
        try:
            points.append((date, float(raw)))
        except ValueError:
            continue
    points.sort(key=lambda item: item[0])
    if not points:
        raise ValueError(f"H.4.1 series has no usable observations: {series_code}")
    return points


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _is_overnight_operation(row: Mapping[str, Any]) -> bool:
    term = str(row.get("term") or "").strip().lower()
    if term and term not in {"overnight", "1", "1 day", "1-day"}:
        return False
    term_days = row.get("termCalendarDays")
    if term_days is None:
        term_days = row.get("termCalenderDays")
    if term_days not in (None, "", 1, "1"):
        return False
    return True


def parse_nyfed_rrp(payload: Mapping[str, Any]) -> list[tuple[str, float]]:
    repo = payload.get("repo") or {}
    operations = repo.get("operations") or [] if isinstance(repo, Mapping) else []
    daily: defaultdict[str, float] = defaultdict(float)

    for row in operations:
        if not isinstance(row, Mapping) or not _is_overnight_operation(row):
            continue
        date = str(row.get("operationDate") or "").strip()
        amount = _safe_float(row.get("totalAmtAccepted"))
        if not date or amount is None:
            continue
        daily[date] += amount

    points = sorted(daily.items(), key=lambda item: item[0])
    if not points:
        raise ValueError("NY Fed reverse-repo response has no usable overnight observations")
    return points


async def fetch_official_liquidity_fallback(
    client: httpx.AsyncClient,
    requested_fred_series: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if requested_fred_series == "WALCL":
        response = await client.get(H41_WALCL_URL, timeout=httpx.Timeout(12.0, connect=5.0))
        response.raise_for_status()
        points = parse_h41_series(response.text, H41_WALCL_SERIES)
        payload = {
            "series_id": "WALCL",
            "source_series_id": H41_WALCL_SERIES,
            "observation_frequency": "W",
            "units": "millions_usd",
            **summarize_series(points),
        }
        status = {
            "status": "LIVE",
            "provider": "Federal Reserve Board H.4.1 Data Download Program",
            "official": True,
            "requested_fred_series": "WALCL",
            "source_series_id": H41_WALCL_SERIES,
            "observation_frequency": "W",
            "units": "millions_usd",
            "fallback_from": "FRED_CSV_TIMEOUT_OR_ERROR",
            "url": H41_WALCL_URL,
        }
        return payload, status

    if requested_fred_series == "RRPONTSYD":
        now = datetime.now(timezone.utc)
        response = await client.get(
            NYFED_RRP_URL,
            params={
                "startDate": (now - timedelta(days=120)).date().isoformat(),
                "endDate": now.date().isoformat(),
            },
            timeout=httpx.Timeout(12.0, connect=5.0),
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise ValueError("NY Fed reverse-repo response is not an object")
        points = parse_nyfed_rrp(data)
        payload = {
            "series_id": "RRPONTSYD",
            "source_series_id": "NYFED_RRP_OPERATIONS",
            "observation_frequency": "D",
            "units": "billions_usd",
            **summarize_series(points),
        }
        status = {
            "status": "LIVE",
            "provider": "Federal Reserve Bank of New York Markets Data API",
            "official": True,
            "requested_fred_series": "RRPONTSYD",
            "source_series_id": "NYFED_RRP_OPERATIONS",
            "observation_frequency": "D",
            "units": "billions_usd",
            "fallback_from": "FRED_CSV_TIMEOUT_OR_ERROR",
            "url": NYFED_RRP_URL,
        }
        return payload, status

    raise ValueError(f"unsupported official liquidity fallback: {requested_fred_series}")
