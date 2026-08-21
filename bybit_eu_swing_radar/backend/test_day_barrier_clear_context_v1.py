from datetime import datetime, timezone
from types import SimpleNamespace

import research.day_barrier_clear_context_v1 as context
from worker import Bar


FIVE = 5 * 60 * 1000
FIFTEEN = 15 * 60 * 1000
HOUR = 60 * 60 * 1000


def _bar(start_ms: int, close: float, volume: float) -> Bar:
    return Bar(
        start_ms=start_ms,
        open=close - 0.1,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=volume,
        turnover=volume * close,
    )


def _analysis(*, with_future: bool = False):
    clear_close = datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)
    clear_close_ms = int(clear_close.timestamp() * 1000)

    bars_5m = []
    for index in range(30):
        start = clear_close_ms - (30 - index) * FIVE
        volume = 200.0 if index == 29 else 100.0
        bars_5m.append(_bar(start, 100.0 + index * 0.05, volume))
    if with_future:
        bars_5m.append(_bar(clear_close_ms, 999.0, 100_000.0))

    bars_15m = []
    for index in range(60):
        start = clear_close_ms - (60 - index) * FIFTEEN
        volume = 600.0 if index == 59 else 300.0
        bars_15m.append(_bar(start, 98.0 + index * 0.05, volume))
    if with_future:
        bars_15m.append(_bar(clear_close_ms, 999.0, 100_000.0))

    bars_1h = [
        _bar(clear_close_ms - (60 - index) * HOUR, 95.0 + index * 0.1, 1000.0)
        for index in range(60)
    ]
    if with_future:
        bars_1h.append(_bar(clear_close_ms, 999.0, 100_000.0))

    instrument = SimpleNamespace(
        symbol="BTCUSDC",
        last_price=101.5,
        bid=101.4,
        ask=101.6,
        spread_bps=19.7,
        turnover_24h=5_000_000.0,
        volume_24h=50_000.0,
        tradeable=True,
        liquidity_reasons=[],
    )
    return SimpleNamespace(
        bars_5m=bars_5m,
        bars_15m=bars_15m,
        bars_1h=bars_1h,
        instrument=instrument,
    )


def test_clear_context_is_point_in_time_and_future_bars_do_not_contaminate(monkeypatch):
    monkeypatch.setattr(context, "classify_15m_structure", lambda *args, **kwargs: "BULLISH_CONTINUATION")
    base = context.build_clear_context_snapshot(_analysis(), 29)
    future = context.build_clear_context_snapshot(_analysis(with_future=True), 29)

    assert base == future
    candle = base["point_in_time_candle_context"]
    assert candle["uses_bars_after_clear"] is False
    assert candle["volume_ratio_5m"] == 2.0
    assert candle["volume_ratio_15m"] == 2.0
    assert candle["turnover_ratio_5m"] > 2.0
    assert candle["turnover_ratio_15m"] > 2.0
    assert candle["sweep_structure_15m"] == "BULLISH_CONTINUATION"
    assert candle["session"]["bucket"] == "US_13_21_UTC"


def test_spread_is_explicitly_observer_run_not_reconstructed_at_clear(monkeypatch):
    monkeypatch.setattr(context, "classify_15m_structure", lambda *args, **kwargs: "NEUTRAL")
    result = context.build_clear_context_snapshot(_analysis(), 29)
    market = result["observer_run_market_snapshot"]

    assert market["spread_bps"] == 19.7
    assert market["point_in_time_at_clear"] is False
    assert market["spread_at_clear_reconstructed"] is False
    assert market["snapshot_timing"] == "OBSERVER_RUN_NEAR_CLEAR_NOT_RECONSTRUCTED"
    assert market["tradeable"] is True


def test_context_firewalls_and_regime_provenance_are_frozen(monkeypatch):
    monkeypatch.setattr(context, "classify_15m_structure", lambda *args, **kwargs: "NEUTRAL")
    result = context.build_clear_context_snapshot(_analysis(), 29)

    assert result["context_version"] == "day-barrier-clear-context-v1"
    assert result["research_only"] is True
    assert result["label_free"] is True
    assert result["execution_authorized"] is False
    assert result["live_strategy_mutation"] is False
    assert result["score_mutation"] is False
    assert result["ranking_mutation"] is False
    assert result["eligibility_mutation"] is False
    regime = result["point_in_time_candle_context"]["regime_context"]
    assert regime["full_market_regime_not_reconstructed"] is True
    assert regime["basis"] == "POINT_IN_TIME_DAY_FEATURES_PLUS_FROZEN_MARKET_REGIME_ATR_THRESHOLDS"


def test_insufficient_history_is_missing_not_fake_zero(monkeypatch):
    monkeypatch.setattr(context, "classify_15m_structure", lambda *args, **kwargs: "UNKNOWN")
    analysis = _analysis()
    analysis.bars_5m = analysis.bars_5m[:10]
    analysis.bars_15m = analysis.bars_15m[:10]
    analysis.bars_1h = analysis.bars_1h[:10]
    result = context.build_clear_context_snapshot(analysis, 9)
    candle = result["point_in_time_candle_context"]

    assert candle["volume_ratio_5m"] is None
    assert candle["volume_ratio_15m"] is None
    assert candle["structure_15m"] is None
    assert candle["structure_1h"] is None
    assert candle["atr_ratio_15m"] is None
    assert candle["regime_context"]["volatility_state"] == "UNKNOWN"
