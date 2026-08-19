from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import worker
from day_worker import build_day_regime
from worker import enrich_coinalyze, select_coinalyze_markets


def _analysis(symbol: str = "BTCUSDC") -> SimpleNamespace:
    return SimpleNamespace(
        instrument=SimpleNamespace(symbol=symbol, base=symbol.removesuffix("USDC")),
        derivatives={},
        missing_data=[],
        direction_score=0.0,
        expansion_score=50.0,
        quality_score=70.0,
        atr_ratio_15m=1.0,
        structure_4h="range",
        structure_1h="range",
        structure_15m="range",
    )


def test_day_market_selection_resolves_exchange_codes_and_keeps_usdc_first() -> None:
    markets = [
        {
            "symbol": "BTCUSDC_PERP.A",
            "base_asset": "BTC",
            "quote_asset": "USDC",
            "exchange": "A",
            "is_perpetual": True,
        },
        {
            "symbol": "BTCUSDT_PERP.B",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "exchange": "B",
            "is_perpetual": True,
        },
        {
            "symbol": "BTCUSDC_PERP.B",
            "base_asset": "BTC",
            "quote_asset": "USDC",
            "exchange": "B",
            "is_perpetual": True,
        },
    ]
    selected = select_coinalyze_markets(
        markets,
        ["BTC"],
        exchange_names={"A": "Binance", "B": "Bybit"},
        quote_order=("USDC", "USDT", "USD"),
    )
    assert selected["BTC"]["exchange_name"] == "Bybit"
    assert selected["BTC"]["exchange_code"] == "B"
    assert selected["BTC"]["quote_asset"] == "USDC"


@pytest.mark.asyncio
async def test_day_enrichment_resolves_exchange_metadata_before_selection(monkeypatch) -> None:
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")

    class FakeAPI:
        exchange_calls = 0

        async def future_markets(self):
            return []

        async def exchanges(self):
            self.exchange_calls += 1
            return [{"code": "B", "name": "Bybit"}]

        def __getattr__(self, _name):
            async def empty(*_args, **_kwargs):
                return []
            return empty

    api = FakeAPI()
    ok, _error = await enrich_coinalyze(
        [_analysis()],
        api,
        mutate_scores=True,
        partial_safe=False,
    )
    assert api.exchange_calls == 1
    assert ok is False


def test_day_regime_requires_complete_derivatives_for_good_source_quality() -> None:
    analyses = [_analysis()]
    now = datetime.now(timezone.utc)
    partial = build_day_regime(
        analyses,
        now,
        True,
        1,
        True,
        coinalyze_complete_symbols=0,
    )
    assert partial["source_quality"]["Coinalyze derivatives"] == "PARTIAL"

    complete = build_day_regime(
        analyses,
        now,
        True,
        1,
        True,
        coinalyze_complete_symbols=1,
    )
    assert complete["source_quality"]["Coinalyze derivatives"] == "GOOD"
