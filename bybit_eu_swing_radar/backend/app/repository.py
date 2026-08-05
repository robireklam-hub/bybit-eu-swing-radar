import json

import asyncpg

from app.config import settings
from app.models import MarketRegime, MomentumResponse, ScanResponse, Setup, TopCandidatesResponse, WatchlistResponse


class RadarRepository:
    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(settings.database_url)

    async def get_cache(self, cache_key: str) -> dict | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT payload
                FROM radar_cache
                WHERE cache_key = $1
                LIMIT 1
                """,
                cache_key,
            )
        finally:
            await conn.close()
        if not row:
            return None
        payload = row["payload"]
        return json.loads(payload) if isinstance(payload, str) else payload

    async def get_latest_scan(self, direction: str, limit: int, min_score: float) -> ScanResponse | None:
        payload = await self.get_cache("latest_scan")
        if payload is None:
            return None
        response = ScanResponse.model_validate(payload)
        if direction == "long":
            response.shorts = []
        elif direction == "short":
            response.longs = []
        response.longs = [x for x in response.longs if x.setup_score >= min_score][:limit]
        response.shorts = [x for x in response.shorts if x.setup_score >= min_score][:limit]
        return response

    async def get_setup(self, symbol: str) -> Setup | None:
        payload = await self.get_cache(f"setup:{symbol.upper()}")
        return Setup.model_validate(payload) if payload is not None else None

    async def get_regime(self) -> MarketRegime | None:
        payload = await self.get_cache("market_regime")
        return MarketRegime.model_validate(payload) if payload is not None else None

    async def get_watchlist(self, limit: int = 20) -> WatchlistResponse | None:
        payload = await self.get_cache("watchlist")
        if payload is None:
            return None
        response = WatchlistResponse.model_validate(payload)
        response.items = response.items[:limit]
        return response

    async def get_data_status(self) -> dict | None:
        return await self.get_cache("data_status")


    async def get_momentum_radar(
        self, direction: str = "both", limit: int = 10, min_score: float = 50.0
    ) -> MomentumResponse | None:
        payload = await self.get_cache("momentum_radar")
        if payload is None:
            return None
        response = MomentumResponse.model_validate(payload)
        if direction in {"long", "short"}:
            response.items = [item for item in response.items if item.side == direction]
        response.items = [item for item in response.items if item.momentum_score >= min_score][:limit]
        return response



def _strict_candidate(setup: Setup) -> bool:
    """Apply the documented executable swing minimums, not score alone."""
    if setup.side not in {"long", "short"}:
        return False
    if setup.setup_score < 70:
        return False
    if setup.expansion_score < 55:
        return False
    if abs(setup.direction_score) < 35:
        return False
    if setup.quality_score < 60:
        return False
    if setup.expected_rr is None or setup.expected_rr < 2.0:
        return False
    if setup.side == "short" and not setup.shortable:
        return False
    return True


def _compact_candidate(setup: Setup, category: str) -> dict:
    metrics = setup.metrics or {}
    tradeable = bool(metrics.get("tradeable", category == "STRICT"))
    liquidity_reasons = list(metrics.get("liquidity_reasons", []))
    execution_status = str(
        metrics.get(
            "execution_status",
            "EXECUTION_ELIGIBLE" if category == "STRICT" else "WATCH_ONLY",
        )
    )
    volume_ratio = metrics.get("volume_ratio_4h")
    volume_confirmed = (
        isinstance(volume_ratio, (int, float)) and float(volume_ratio) >= 1.2
    )

    if category == "WATCH_ONLY":
        decision = "NO_TRADE"
    elif setup.state == "TRIGGERED" and volume_confirmed:
        decision = "TRADE"
    else:
        decision = "WAIT"

    # Defensive override: anything blocked or non-tradeable can never be WAIT/TRADE.
    if (
        not tradeable
        or "LIQUIDITY_BLOCKED" in execution_status
        or liquidity_reasons
        or (setup.side == "short" and not setup.shortable)
    ):
        decision = "NO_TRADE"

    return {
        "symbol": setup.symbol,
        "side": setup.side,
        "category": category,
        "state": setup.state,
        "grade": setup.grade,
        "decision": decision,
        "last_price": setup.last_price,
        "setup_score": setup.setup_score,
        "expansion_score": setup.expansion_score,
        "direction_score": setup.direction_score,
        "quality_score": setup.quality_score,
        "shortable": setup.shortable,
        "tradeable": tradeable,
        "execution_status": execution_status,
        "execution_modes": setup.execution_modes,
        "trigger": setup.trigger.model_dump() if setup.trigger else None,
        "entry_zone": setup.entry_zone.model_dump() if setup.entry_zone else None,
        "stop": setup.stop,
        "invalidation": setup.invalidation,
        "targets": setup.targets,
        "expected_rr": setup.expected_rr,
        "turnover_24h_usdc": metrics.get("turnover_24h_usdc"),
        "spread_bps": metrics.get("spread_bps"),
        "volume_ratio_4h": volume_ratio,
        "liquidity_reasons": liquidity_reasons,
        "weakest_point": setup.weakest_point,
        "data_quality": setup.data_quality,
        "missing_data": setup.missing_data,
    }


async def _get_top_candidates(
    repository: RadarRepository,
    limit: int = 3,
    include_watchlist: bool = True,
) -> TopCandidatesResponse | None:
    payload = await repository.get_cache("latest_scan")
    if payload is None:
        return None

    scan = ScanResponse.model_validate(payload)

    strict_longs_all = sorted(
        [item for item in scan.longs if _strict_candidate(item)],
        key=lambda item: item.setup_score,
        reverse=True,
    )
    strict_shorts_all = sorted(
        [item for item in scan.shorts if _strict_candidate(item)],
        key=lambda item: item.setup_score,
        reverse=True,
    )

    strict_symbols = {
        item.symbol for item in strict_longs_all + strict_shorts_all
    }

    watch_longs: list[Setup] = []
    watch_shorts: list[Setup] = []
    if include_watchlist:
        # Use the broad discovery list, but never merge it into strict results.
        combined_watch = list(scan.extended_watchlist)
        known = {item.symbol for item in combined_watch}
        for item in scan.liquidity_blocked:
            if item.symbol not in known:
                combined_watch.append(item)
                known.add(item.symbol)

        watch_longs = sorted(
            [
                item for item in combined_watch
                if item.side == "long" and item.symbol not in strict_symbols
            ],
            key=lambda item: item.setup_score,
            reverse=True,
        )[:limit]
        watch_shorts = sorted(
            [
                item for item in combined_watch
                if item.side == "short" and item.symbol not in strict_symbols
            ],
            key=lambda item: item.setup_score,
            reverse=True,
        )[:limit]

    regime = scan.market_regime
    compact_regime = {
        "btc_regime": regime.btc_regime,
        "btc_structure_1d": regime.btc_structure_1d,
        "btc_structure_4h": regime.btc_structure_4h,
        "alt_breadth": regime.alt_breadth,
        "volatility_regime": regime.volatility_regime,
        "preferred_side": regime.preferred_side,
        "source_quality": regime.source_quality,
    }

    return TopCandidatesResponse(
        data_as_of=scan.data_as_of,
        data_as_of_budapest=scan.data_as_of_budapest,
        data_quality=scan.data_quality,
        market_regime=compact_regime,
        requested_limit=limit,
        strict_long_count=len(strict_longs_all),
        strict_short_count=len(strict_shorts_all),
        strict_longs=[
            _compact_candidate(item, "STRICT")
            for item in strict_longs_all[:limit]
        ],
        strict_shorts=[
            _compact_candidate(item, "STRICT")
            for item in strict_shorts_all[:limit]
        ],
        watch_only_longs=[
            _compact_candidate(item, "WATCH_ONLY")
            for item in watch_longs
        ],
        watch_only_shorts=[
            _compact_candidate(item, "WATCH_ONLY")
            for item in watch_shorts
        ],
        notes=[
            "Strict lists enforce setup>=70, expansion>=55, abs(direction)>=35, quality>=60 and RR>=2.",
            "WATCH_ONLY lists are separate fallback context and must never be presented as executable candidates.",
            "Liquidity-blocked or non-shortable items always have decision=NO_TRADE.",
        ],
    )


# Bind as a repository method without changing existing database behavior.
RadarRepository.get_top_candidates = _get_top_candidates
