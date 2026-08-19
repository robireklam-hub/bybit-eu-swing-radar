from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import ScanResponse
from app.repository import _get_top_candidates
from app.swing_priority import select_compact_priority_sections


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def setup(
    symbol: str,
    side: str,
    score: float,
    *,
    expansion: float = 65.0,
    direction: float | None = None,
    quality: float = 70.0,
    rr: float = 2.5,
    shortable: bool = True,
    tradeable: bool = True,
    execution_status: str = "EXECUTION_ELIGIBLE",
) -> dict:
    if direction is None:
        direction = 50.0 if side == "long" else -50.0
    return {
        "symbol": symbol,
        "base_asset": symbol.removesuffix("USDC"),
        "quote_asset": "USDC",
        "side": side,
        "state": "WATCH",
        "grade": "WATCH",
        "confidence": "LOW",
        "last_price": 1.0,
        "shortable": shortable,
        "execution_modes": ["spot_usdc_watch_only"] if side == "long" else ["spot_margin_short_usdc_watch_only"],
        "expansion_score": expansion,
        "direction_score": direction,
        "quality_score": quality,
        "setup_score": score,
        "expected_rr": rr,
        "metrics": {
            "tradeable": tradeable,
            "execution_status": execution_status,
            "liquidity_reasons": ["turnover below live threshold"] if not tradeable else [],
            "turnover_24h_usdc": 50_000.0 if not tradeable else 1_000_000.0,
            "spread_bps": 4.0,
        },
        "data_quality": "GOOD",
        "missing_data": [],
        "data_as_of": NOW,
    }


def scan_payload() -> dict:
    strict_long = setup("STRICTLUSDC", "long", 80.0)
    strict_short = setup("STRICTSUSDC", "short", 79.0, shortable=True)
    nonshortable = setup("NOSHORTUSDC", "short", 95.0, shortable=False)
    low_watch = setup(
        "LOWWATCHUSDC",
        "long",
        52.0,
        expansion=35.0,
        quality=45.0,
        tradeable=False,
        execution_status="LIQUIDITY_BLOCKED",
    )
    high_watch = setup("HIGHWATCHUSDC", "long", 68.0, expansion=50.0)
    unrelated = setup("UNRELATEDUSDC", "long", 99.0)
    return {
        "data_as_of": NOW,
        "data_as_of_budapest": "2026-08-19T14:00:00+02:00",
        "data_quality": "GOOD",
        "market_regime": {
            "data_as_of": NOW,
            "data_quality": "GOOD",
            "btc_regime": "range",
            "volatility_regime": "normal",
            "preferred_side": "neutral",
            "source_quality": {},
            "notes": [],
        },
        "longs": [strict_long],
        "shorts": [strict_short],
        "extended_watchlist": [high_watch, nonshortable, low_watch],
        "liquidity_blocked": [low_watch],
        "coverage": {},
        "exclusions": [],
        # Not returned by the compact API and therefore must never displace a
        # real top/watch candidate merely because its generic score is higher.
        "_unrelated_analysis_fixture": unrelated,
    }


class FakeRepository:
    def __init__(self, payload: dict):
        self.payload = dict(payload)
        self.payload.pop("_unrelated_analysis_fixture", None)

    async def get_cache(self, key: str):
        assert key == "latest_scan"
        return self.payload


@pytest.mark.asyncio
async def test_worker_priority_symbols_match_actual_top_candidates_default_semantics():
    payload = scan_payload()
    scan = ScanResponse.model_validate({k: v for k, v in payload.items() if not k.startswith("_")})

    worker_sections = select_compact_priority_sections(
        scan.longs,
        scan.shorts,
        scan.extended_watchlist,
        scan.liquidity_blocked,
        limit=3,
    )
    api = await _get_top_candidates(FakeRepository(payload), limit=3, include_watchlist=True)
    assert api is not None

    api_symbols = [
        item.symbol
        for section in (
            api.strict_longs,
            api.strict_shorts,
            api.watch_only_longs,
            api.watch_only_shorts,
        )
        for item in section
    ]

    assert worker_sections["priority_symbols"] == api_symbols
    assert "LOWWATCHUSDC" in api_symbols
    assert "NOSHORTUSDC" in api_symbols
    assert api.strict_long_count == worker_sections["strict_long_count"]
    assert api.strict_short_count == worker_sections["strict_short_count"]


def test_priority_selection_is_independent_of_derivatives_context():
    payload = scan_payload()
    scan = ScanResponse.model_validate({k: v for k, v in payload.items() if not k.startswith("_")})
    before = select_compact_priority_sections(
        scan.longs, scan.shorts, scan.extended_watchlist, scan.liquidity_blocked, limit=3
    )["priority_symbols"]

    for row in [*scan.longs, *scan.shorts, *scan.extended_watchlist, *scan.liquidity_blocked]:
        row.metrics["derivatives"] = {
            "open_interest_usd": 123.0,
            "funding_rate": 0.001,
            "strict_score_mutation_applied": False,
        }

    after = select_compact_priority_sections(
        scan.longs, scan.shorts, scan.extended_watchlist, scan.liquidity_blocked, limit=3
    )["priority_symbols"]
    assert after == before
