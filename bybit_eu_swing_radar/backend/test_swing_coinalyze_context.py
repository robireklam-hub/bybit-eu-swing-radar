from __future__ import annotations

from datetime import datetime, timezone

import pytest

import worker
from worker import (
    Analysis,
    Instrument,
    apply_shortability,
    build_market_regime,
    build_setup,
    build_watch_setup,
    enrich_coinalyze,
)


def make_analysis(symbol: str = "TESTUSDC") -> Analysis:
    base = symbol.removesuffix("USDC")
    instrument = Instrument(
        symbol=symbol,
        base=base,
        quote="USDC",
        margin_trading="both",
        tick_size=0.01,
        turnover_24h=10_000_000.0,
        volume_24h=1_000_000.0,
        last_price=100.0,
        bid=99.99,
        ask=100.01,
        spread_bps=2.0,
        price_change_24h_pct=1.0,
        tradeable=True,
        liquidity_reasons=[],
        discovery_source="market",
    )
    bars = [
        worker.Bar(
            start_ms=1_700_000_000_000 + i * 14_400_000,
            open=90.0 + i * 0.1,
            high=91.0 + i * 0.1,
            low=89.0 + i * 0.1,
            close=90.5 + i * 0.1,
            volume=1000.0,
            turnover=100_000.0,
        )
        for i in range(100)
    ]
    return Analysis(
        instrument=instrument,
        bars_1h=bars,
        bars_4h=bars,
        bars_1d=bars,
        atr_4h=2.0,
        ema20_4h=98.0,
        ema50_4h=95.0,
        ema20_1d=97.0,
        ema50_1d=94.0,
        range_high=101.0,
        range_low=90.0,
        recent_high=100.0,
        recent_low=94.0,
        volume_ratio=1.2,
        bb_width_percentile=20.0,
        atr_ratio=0.9,
        expansion_score=70.0,
        direction_score=60.0,
        quality_score=75.0,
        relative_strength_4h=2.0,
        structure_4h="bullish HH/HL-compatible",
        structure_1d="bullish HH/HL-compatible",
        derivatives={},
        missing_data=[],
    )


class FakeCoinalyze:
    async def future_markets(self):
        return [
            {
                "symbol": "TESTUSDT_PERP.A",
                "base_asset": "TEST",
                "quote_asset": "USDT",
                "exchange": "Binance",
                "is_perpetual": True,
            }
        ]

    async def batch_current(self, endpoint, symbols, convert_to_usd=False):
        if endpoint == "/open-interest":
            return [{"symbol": symbols[0], "value": 1_000_000.0}]
        if endpoint == "/funding-rate":
            return [{"symbol": symbols[0], "value": 0.0001}]
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
        if endpoint == "/open-interest-history":
            history = [
                {"c": 900_000.0 + i * 5_000.0}
                for i in range(30)
            ]
            return [{"symbol": symbols[0], "history": history}]
        if endpoint == "/liquidation-history":
            return [
                {
                    "symbol": symbols[0],
                    "history": [{"l": 1000.0, "s": 1200.0} for _ in range(6)],
                }
            ]
        raise AssertionError(endpoint)


class PartialFakeCoinalyze(FakeCoinalyze):
    async def batch_current(self, endpoint, symbols, convert_to_usd=False):
        if endpoint == "/funding-rate":
            raise RuntimeError("funding temporarily unavailable")
        return await super().batch_current(endpoint, symbols, convert_to_usd)


class FailedFakeCoinalyze(FakeCoinalyze):
    async def batch_current(self, endpoint, symbols, convert_to_usd=False):
        raise RuntimeError("all current endpoints unavailable")

    async def batch_history(
        self,
        endpoint,
        symbols,
        from_ts,
        to_ts,
        convert_to_usd=False,
        interval="4hour",
    ):
        raise RuntimeError("all history endpoints unavailable")


@pytest.mark.asyncio
async def test_swing_enrichment_does_not_mutate_strict_scores(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")
    analysis = make_analysis()
    before = (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    )

    ok, error = await enrich_coinalyze(
        [analysis],
        FakeCoinalyze(),
        mutate_scores=False,
        partial_safe=True,
    )

    assert ok is True
    assert error is None
    assert analysis.derivatives["strict_score_mutation_applied"] is False
    assert (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    ) == before


@pytest.mark.asyncio
async def test_swing_partial_enrichment_retains_available_context(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")
    analysis = make_analysis()

    ok, error = await enrich_coinalyze(
        [analysis],
        PartialFakeCoinalyze(),
        mutate_scores=False,
        partial_safe=True,
    )

    assert ok is False
    assert "funding-rate" in (error or "")
    assert analysis.derivatives["open_interest_usd"] == 1_000_000.0
    assert analysis.derivatives["funding_rate"] is None
    assert analysis.derivatives["availability"]["current_oi"] is True
    assert analysis.derivatives["availability"]["funding"] is False
    assert analysis.derivatives["strict_score_mutation_applied"] is False
    assert "Coinalyze derivatives context partial" in analysis.missing_data


@pytest.mark.asyncio
async def test_shortability_requires_public_borrowability_and_pair_margin():
    analyses = [make_analysis("TESTUSDC"), make_analysis("NOMARGINUSDC")]
    analyses[1].instrument.margin_trading = "none"

    class FakeBybit:
        async def vip_margin_data(self):
            return {
                "TEST": {"borrowable": True, "maxBorrowingAmount": "25"},
                "NOMARGIN": {"borrowable": True, "maxBorrowingAmount": "25"},
            }

    ok, error = await apply_shortability(analyses, FakeBybit())
    assert ok is True
    assert error is None
    assert analyses[0].shortable is True
    assert analyses[0].max_borrowing_amount == 25.0
    assert analyses[1].shortable is False


def test_swing_derivatives_context_cannot_change_setup_scores():
    analysis = make_analysis()
    analysis.shortable = True
    now = datetime.now(timezone.utc)
    before = build_setup(analysis, "long", now)
    assert before is not None

    analysis.derivatives = {
        "source": "Coinalyze",
        "funding_rate": 0.5,
        "oi_change_24h_pct": -99.0,
        "strict_score_mutation_applied": False,
        "context_score_adjustments": {
            "expansion": 999.0,
            "direction": -999.0,
            "quality": 999.0,
        },
    }
    after = build_setup(analysis, "long", now)
    assert after is not None
    assert after["expansion_score"] == before["expansion_score"]
    assert after["direction_score"] == before["direction_score"]
    assert after["quality_score"] == before["quality_score"]
    assert after["setup_score"] == before["setup_score"]


def test_watch_setup_exposes_derivatives_without_changing_watch_score():
    analysis = make_analysis()
    now = datetime.now(timezone.utc)
    before = build_watch_setup(analysis, now)
    analysis.derivatives = {
        "source": "Coinalyze",
        "funding_rate": -0.01,
        "strict_score_mutation_applied": False,
    }
    after = build_watch_setup(analysis, now)
    assert after["setup_score"] == before["setup_score"]
    assert after["metrics"]["derivatives"]["source"] == "Coinalyze"


@pytest.mark.asyncio
async def test_failed_context_never_blocks_core_swing_setup(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")
    analysis = make_analysis()
    analysis.shortable = True
    before = (
        analysis.expansion_score,
        analysis.direction_score,
        analysis.quality_score,
    )

    ok, error = await enrich_coinalyze(
        [analysis],
        FailedFakeCoinalyze(),
        mutate_scores=False,
        partial_safe=True,
    )

    assert ok is False
    assert error
    assert not analysis.derivatives
    setup = build_setup(analysis, "long", datetime.now(timezone.utc))
    assert setup is not None
    assert setup["data_quality"] == "PARTIAL"
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
        "1/1 selected targets" in note
        and "compact top/watch coverage is reported separately" in note
        for note in regime["notes"]
    )
