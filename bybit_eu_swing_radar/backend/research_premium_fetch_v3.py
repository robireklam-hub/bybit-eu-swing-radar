"""Historical Bybit premium-index collector for research v3."""
from __future__ import annotations

from research_historical_flow_fetch_v2 import HistoricalFlowAPI
from research_premium_microstructure_v3 import PremiumPoint, normalize_premium_klines

PREMIUM_INTERVAL = "60"
PAGE_LIMIT = 1000


class HistoricalPremiumAPI(HistoricalFlowAPI):
    async def premium_history(
        self, symbol: str, *, start_ms: int, end_ms: int
    ) -> list[PremiumPoint]:
        rows: list[list[str]] = []
        page_end = end_ms
        for _ in range(20):
            payload = await self._get(
                "/v5/market/premium-index-price-kline",
                category="linear",
                symbol=symbol,
                interval=PREMIUM_INTERVAL,
                start=start_ms,
                end=page_end,
                limit=PAGE_LIMIT,
            )
            page = list((payload.get("result") or {}).get("list") or [])
            if not page:
                break
            rows.extend(page)
            timestamps: list[int] = []
            for item in page:
                try:
                    timestamps.append(int(item[0]))
                except (IndexError, TypeError, ValueError):
                    continue
            if not timestamps:
                break
            oldest = min(timestamps)
            if oldest <= start_ms or len(page) < PAGE_LIMIT:
                break
            next_end = oldest - 1
            if next_end >= page_end:
                break
            page_end = next_end
        return [
            point
            for point in normalize_premium_klines(rows)
            if start_ms // 1000 <= point.ts <= end_ms // 1000
        ]
