"""Public Bybit historical derivatives collector for research dataset v2.

Research only. Uses public global derivatives market data to create contextual
OI/funding features; it does not imply or alter Bybit EU execution eligibility.
"""
from __future__ import annotations

from typing import Any

import httpx

from research_historical_flow_v2 import FundingPoint, OIPoint, normalize_bybit_funding, normalize_bybit_oi

DEFAULT_BASE_URL = "https://api.bybit.com"
OI_INTERVAL = "1h"
PAGE_LIMIT = 200


class HistoricalFlowAPI:
    def __init__(self, client: httpx.AsyncClient, base_url: str = DEFAULT_BASE_URL) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit global API error {payload.get('retCode')}: {payload.get('retMsg')}"
            )
        return payload

    async def linear_instruments(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(10):
            params: dict[str, Any] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("/v5/market/instruments-info", **params)
            result = payload.get("result") or {}
            rows.extend(result.get("list") or [])
            cursor = str(result.get("nextPageCursor") or "")
            if not cursor:
                break
        return rows

    async def linear_tickers(self) -> list[dict[str, Any]]:
        payload = await self._get("/v5/market/tickers", category="linear")
        return list((payload.get("result") or {}).get("list") or [])

    async def open_interest_history(
        self, symbol: str, *, start_ms: int, end_ms: int
    ) -> list[OIPoint]:
        rows: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _ in range(100):
            params: dict[str, Any] = {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": OI_INTERVAL,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": PAGE_LIMIT,
            }
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("/v5/market/open-interest", **params)
            result = payload.get("result") or {}
            page = list(result.get("list") or [])
            rows.extend(page)
            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor or next_cursor in seen_cursors or not page:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return normalize_bybit_oi(rows)

    async def funding_history(
        self, symbol: str, *, start_ms: int, end_ms: int
    ) -> list[FundingPoint]:
        rows: list[dict[str, Any]] = []
        page_end = end_ms
        for _ in range(100):
            payload = await self._get(
                "/v5/market/funding/history",
                category="linear",
                symbol=symbol,
                endTime=page_end,
                limit=PAGE_LIMIT,
            )
            page = list((payload.get("result") or {}).get("list") or [])
            if not page:
                break
            rows.extend(page)
            timestamps = []
            for item in page:
                try:
                    timestamps.append(int(item.get("fundingRateTimestamp")))
                except (TypeError, ValueError):
                    continue
            if not timestamps:
                break
            oldest = min(timestamps)
            if oldest <= start_ms:
                break
            next_end = oldest - 1
            if next_end >= page_end:
                break
            page_end = next_end
        return [point for point in normalize_bybit_funding(rows) if start_ms // 1000 <= point.ts <= end_ms // 1000]


def choose_derivative_market(
    base: str,
    instruments: list[dict[str, Any]],
    tickers: list[dict[str, Any]],
) -> str | None:
    """Match live Flow Context market-selection policy without affecting execution."""
    ticker_map = {str(row.get("symbol", "")).upper(): row for row in tickers}
    candidates: list[tuple[float, int, str]] = []
    for item in instruments:
        if str(item.get("status")) != "Trading":
            continue
        if str(item.get("baseCoin", "")).upper() != base.upper():
            continue
        if "perpetual" not in str(item.get("contractType", "")).lower():
            continue
        symbol = str(item.get("symbol", "")).upper()
        quote = str(item.get("quoteCoin", "")).upper()
        if quote not in {"USDT", "USDC"}:
            continue
        try:
            turnover = float(ticker_map.get(symbol, {}).get("turnover24h") or 0.0)
        except (TypeError, ValueError):
            turnover = 0.0
        quote_rank = 2 if quote == "USDT" else 1
        candidates.append((turnover, quote_rank, symbol))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]
