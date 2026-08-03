from typing import Any
import httpx

from app.config import settings


class CoinalyzeClient:
    def __init__(self) -> None:
        self.base_url = settings.coinalyze_base_url.rstrip("/")
        self.headers = {"api_key": settings.coinalyze_api_key}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            response = await client.get(f"{self.base_url}{path}", params=params)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "unknown")
                raise RuntimeError(f"Coinalyze rate limit; retry after {retry_after}s")
            response.raise_for_status()
            return response.json()

    async def future_markets(self) -> list[dict[str, Any]]:
        return await self._get("/future-markets")

    async def current_open_interest(self, symbols: list[str]) -> Any:
        return await self._get(
            "/open-interest",
            {"symbols": ",".join(symbols[:20]), "convert_to_usd": "true"},
        )

    async def current_funding(self, symbols: list[str]) -> Any:
        return await self._get("/funding-rate", {"symbols": ",".join(symbols[:20])})

    async def history(
        self,
        endpoint: str,
        symbols: list[str],
        interval: str,
        from_ts: int,
        to_ts: int,
        convert_to_usd: bool = False,
    ) -> Any:
        params = {
            "symbols": ",".join(symbols[:20]),
            "interval": interval,
            "from": from_ts,
            "to": to_ts,
        }
        if convert_to_usd:
            params["convert_to_usd"] = "true"
        return await self._get(endpoint, params)
