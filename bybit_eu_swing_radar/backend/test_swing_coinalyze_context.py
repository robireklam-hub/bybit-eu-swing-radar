from __future__ import annotations

from datetime import datetime, timezone

import pytest

import worker
from worker import Analysis, Bar, Instrument, build_market_regime, enrich_coinalyze


def make_analysis(symbol: str = "BTCUSDC") -> Analysis:
    base = symbol.removesuffix("USDC")
    instrument = Instrument(
        symbol=symbol,
        base=base,
        quote="USDC",
        margin_trading="utaOnly",
        tick_size=0.1,
        turnover_24h=1_000_000.0,
        volume_24h=1_000.0,
        last_price=100.0,
        bid=99.9,
        ask=100.1,
        spread_bps=20.0,
        price_change_24h_pct=0.0,
        tradeable=True,
        liquidity_reasons=[],
        discovery_source="mandatory",
    )
    bar = Bar(
        start_ms=0,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=100.0,
        turnover=10_000.0,
    )
    return Analysis(
        instrument=instrument,
        bars_1h=[bar] * 100,
        bars_4h=[bar] * 100,
        bars_1d=[bar] * 100,
        atr_4h=2.0,
        ema20_4h=101.0,
        ema50_4h=102.0,
        ema20_1d=101.0,
        ema50_1d=102.0,
        range_high=110.0,
        range_low=90.0,
        recent_high=105.0,
        recent_low=95.0,
        volume_ratio=1.2,
        bb_width_percentile=30.0,
        atr_ratio=1.0,
        expansion_score=54.0,
        direction_score=-50.0,
        quality_score=80.0,
        relative_strength_4h=-2.0,
        structure_4h="bearish LH/LL-compatible",
        structure_1d="bearish LH/LL-compatible",
        derivatives={},
        missing_data=[],
        shortable=True,
        max_borrowing_amount=10.0,
    )


class FakeCoinalyze:
    def __init__(self, fail_endpoint: str | None = None) -> None:
        self.fail_endpoint = fail_endpoint
        self.calls: list[str] = []

    async def future_markets(self):
        return [
            {
                "symbol": "BTCUSDT_PERP.A",
                "exchange": "Binance",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "is_perpetual": True,
            }
        ]

    async def batch_current(self, endpoint, symbols, convert_to_usd=False):
        self.calls.append(endpoint)
        if endpoint == self.fail_endpoint:
            raise RuntimeError(f"forced {endpoint} failure")
        if endpoint == "/open-interest":
            return [{"symbol": symbols[0], "value": 1_250_000.0}]
        if endpoint == "/funding-rate":
            return [{"symbol": symbols[0], "value": 0.0015}]
        raise AssertionError(endpoint)

    async def batch_history(
        self,
        endpoint,
        symbols,
        from_ts,
        to_ts,
        convert_to_usd=False,
        interval="4hour",
    ):
        self.calls.append(endpoint)
        if endpoint == self.fail_endpoint:
            raise RuntimeError(f"forced {endpoint} failure")
        if endpoint == "/open-interest-history":
            history = [
                {"t": i, "c": 1_000_000.0 + i * 10_000.0}
                for i in range(30)
            ]
            return [{"symbol": symbols[0], "history": history}]
        if endpoint == "/liquidation-history":
            return [
                {
                    "symbol": symbols[0],
                    "history": [
                        {"t": i, "l": 10_000.0, "s": 5_000.0}
                        for i in range(6)
                    ],
                }
            ]
        raise AssertionError(endpoint)


@pytest.mark.asyncio
async def test_context_only_enrichment_never_mutates_swing_core_scores(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")
    analysis = make_analysis()
    api = FakeCoinalyze()
    before = (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    )

    ok, error = await enrich_coinalyze(
        [analysis],
        api,
        mutate_scores=False,
        partial_safe=True,
    )

    assert ok is True
    assert error is None
    assert (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    ) == before
    assert analysis.derivatives["strict_score_mutation_applied"] is False
    adjustments = analysis.derivatives["context_score_adjustments"]
    assert adjustments["expansion"] != 0.0
    assert adjustments["direction"] != 0.0
    assert adjustments["quality"] != 0.0
    assert api.calls == [
        "/open-interest",
        "/funding-rate",
        "/open-interest-history",
        "/liquidation-history",
    ]


@pytest.mark.asyncio
async def test_partial_endpoint_failure_preserves_available_context(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")
    analysis = make_analysis()
    api = FakeCoinalyze(fail_endpoint="/funding-rate")
    before = (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    )

    ok, error = await enrich_coinalyze(
        [analysis],
        api,
        mutate_scores=False,
        partial_safe=True,
    )

    assert ok is False
    assert error is not None
    assert "funding-rate" in error
    assert analysis.derivatives
    assert analysis.derivatives["open_interest_usd"] == 1_250_000.0
    assert analysis.derivatives["funding_rate"] is None
    assert analysis.derivatives["availability"]["current_oi"] is True
    assert analysis.derivatives["availability"]["funding"] is False
    assert analysis.derivatives["availability"]["oi_history"] is True
    assert analysis.derivatives["availability"]["liquidations"] is True
    assert "Coinalyze derivatives context partial" in analysis.missing_data
    assert (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    ) == before
    assert api.calls == [
        "/open-interest",
        "/funding-rate",
        "/open-interest-history",
        "/liquidation-history",
    ]


@pytest.mark.asyncio
async def test_default_enrichment_mode_keeps_day_worker_score_behavior(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")
    analysis = make_analysis()
    api = FakeCoinalyze()
    before = (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    )

    ok, error = await enrich_coinalyze([analysis], api)

    assert ok is True
    assert error is None
    assert analysis.derivatives["strict_score_mutation_applied"] is True
    assert (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    ) != before


@pytest.mark.asyncio
async def test_default_day_mode_keeps_all_or_nothing_failure(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")
    analysis = make_analysis()
    api = FakeCoinalyze(fail_endpoint="/funding-rate")
    before = (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    )

    ok, error = await enrich_coinalyze([analysis], api)

    assert ok is False
    assert error is not None
    assert analysis.derivatives == {}
    assert "Coinalyze enrichment failed" in analysis.missing_data
    assert (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    ) == before


def test_market_regime_coinalyze_quality_uses_targeted_coverage(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_ENRICH_LIMIT", 1)
    analyses = [make_analysis("BTCUSDC")]
    for i in range(9):
        analyses.append(make_analysis(f"TEST{i}USDC"))
    analyses[0].derivatives = {"source": "Coinalyze"}

    regime = build_market_regime(
        analyses,
        datetime.now(timezone.utc),
        coinalyze_ok=True,
        borrow_ok=True,
    )

    assert regime["source_quality"]["coinalyze_derivatives"] == "GOOD"
    assert any(
        "1/1 targeted symbols" in note for note in regime["notes"]
    )
