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


def test_day_context_market_prefers_bybit_usdt_over_usdc() -> None:
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
        quote_order=("USDT", "USDC", "USD"),
    )
    assert selected["BTC"]["exchange_name"] == "Bybit"
    assert selected["BTC"]["exchange_code"] == "B"
    assert selected["BTC"]["quote_asset"] == "USDT"
    assert selected["BTC"]["symbol"] == "BTCUSDT_PERP.B"


@pytest.mark.asyncio
async def test_day_enrichment_uses_bybit_usdt_context_symbol(monkeypatch) -> None:
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")

    class FakeAPI:
        def __init__(self) -> None:
            self.exchange_calls = 0
            self.symbol_batches: list[tuple[str, tuple[str, ...]]] = []

        async def future_markets(self):
            return [
                {
                    "symbol": "BTCUSDC_PERP.6",
                    "base_asset": "BTC",
                    "quote_asset": "USDC",
                    "exchange": "6",
                    "is_perpetual": True,
                },
                {
                    "symbol": "BTCUSDT_PERP.6",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                    "exchange": "6",
                    "is_perpetual": True,
                },
            ]

        async def exchanges(self):
            self.exchange_calls += 1
            return [{"code": "6", "name": "Bybit"}]

        async def batch_current(self, endpoint, symbols, convert_to_usd=False):
            self.symbol_batches.append((endpoint, tuple(symbols)))
            return []

        async def batch_history(
            self,
            endpoint,
            symbols,
            _from_ts,
            _to_ts,
            convert_to_usd=False,
            interval=None,
        ):
            self.symbol_batches.append((endpoint, tuple(symbols)))
            return []

    api = FakeAPI()
    ok, _error = await enrich_coinalyze(
        [_analysis()],
        api,
        mutate_scores=True,
        partial_safe=False,
    )
    assert api.exchange_calls == 1
    assert ok is False
    assert len(api.symbol_batches) == 4
    assert all(symbols == ("BTCUSDT_PERP.6",) for _, symbols in api.symbol_batches)


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
