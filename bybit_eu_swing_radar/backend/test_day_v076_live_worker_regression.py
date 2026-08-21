from datetime import datetime, timezone

import pytest

import day_worker
from day_worker import DayAnalysis, build_day_candidate
from worker import Bar, Instrument


def _bar(i: int, *, close: float = 99.5, high: float = 100.0, low: float = 99.0) -> Bar:
    return Bar(i * 300_000, 99.5, high, low, close, 10.0, 1000.0)


def _analysis(*, follow_close: float) -> DayAnalysis:
    prior = [_bar(i) for i in range(12)]
    prior[-1] = _bar(11, close=99.8)
    # Origin breakout over the prior 12-bar high = 100.0.
    bars = prior + [_bar(12, close=100.2, high=100.4, low=99.5)]
    # A later closed bar keeps the original breakout boundary structurally held.
    bars.append(_bar(13, close=follow_close, high=follow_close + 0.2, low=100.1))

    instrument = Instrument(
        symbol="BTCUSDC",
        base="BTC",
        quote="USDC",
        margin_trading="both",
        tick_size=0.1,
        turnover_24h=1e9,
        volume_24h=1e4,
        last_price=follow_close,
        bid=follow_close - 0.1,
        ask=follow_close,
        spread_bps=1.0,
        price_change_24h_pct=1.0,
        tradeable=True,
        liquidity_reasons=[],
        discovery_source="mandatory",
    )
    bars15 = [Bar(i * 900_000, 99.0, 100.0, 98.8, 99.5, 20.0, 2000.0) for i in range(8)]
    return DayAnalysis(
        instrument=instrument,
        bars_5m=bars,
        bars_15m=bars15,
        bars_1h=[],
        bars_4h=[],
        atr_5m=1.0,
        atr_15m=2.0,
        rolling_vwap_24h=99.0,
        ema20_15m=99.0,
        ema50_15m=98.5,
        ema20_1h=99.0,
        ema50_1h=98.0,
        ema20_4h=99.0,
        ema50_4h=98.0,
        return_15m_pct=1.0,
        return_1h_pct=1.0,
        return_4h_pct=1.0,
        relative_strength_1h=0.0,
        relative_strength_4h=0.0,
        volume_ratio_5m=1.84,
        volume_ratio_15m=3.83,
        atr_ratio_15m=1.2,
        structure_15m="bullish",
        structure_1h="bullish",
        structure_4h="bullish",
        expansion_score=62.05,
        direction_score=57.6,
        quality_score=99.99,
        derivatives={},
        missing_data=[],
        shortable=True,
        max_borrowing_amount=10.0,
    )


def _barrier(price: float):
    return {
        "price": price,
        "timeframe": "15m",
        "swing_type": "SWING_HIGH",
        "pivot_start_ms": 0,
        "pivot_time": "2026-08-20T00:00:00+00:00",
        "confirmed_at": "2026-08-20T00:45:00+00:00",
        "prominence": 1.0,
        "prominence_atr": 0.5,
        "search_window_start": "2026-08-19T00:00:00+00:00",
        "search_window_end": "2026-08-20T00:00:00+00:00",
        "trigger_window_start": "2026-08-20T00:00:00+00:00",
        "trigger_window_excluded": True,
        "same_structure_as_trigger": False,
    }


def test_valid_long_setup_survives_barrier_block_in_live_candidate(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(day_worker, "nearest_structural_barrier", lambda *args, **kwargs: _barrier(100.5))

    candidate = build_day_candidate(
        _analysis(follow_close=100.3),
        "long",
        datetime(2026, 8, 21, tzinfo=timezone.utc),
    )

    assert candidate is not None
    assert candidate["strategy_version"] == "0.7.6"
    assert candidate["setup_state"] == "VALID"
    assert candidate["entry_state"] == "BLOCKED_BY_BARRIER"
    assert candidate["decision"] == "WAIT"
    assert candidate["state"] == "WATCH"
    assert candidate["category"] == "WATCH_ONLY"
    assert candidate["watch_bucket"] == "BARRIER_BLOCKED_VALID_SETUP"
    assert candidate["rr_valid"] is False
    assert candidate["reference_entry"] == pytest.approx(100.3)
    assert candidate["trigger"]["price"] == pytest.approx(100.0)
    assert candidate["trigger"]["age_bars"] == 1
    assert candidate["trigger"]["validity_bars"] is None
    assert candidate["hard_stop"]["requires_candle_close"] is False
    assert candidate["hard_stop"]["activation"] == "INTRABAR_TOUCH_OR_CROSS"
    assert candidate["structure_invalidation"]["timeframe"] == "15m"


def test_cleared_old_barrier_uses_fresh_entry_and_becomes_provisional(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    # The same old swing-high is now behind current price; it must no longer cap target path.
    monkeypatch.setattr(day_worker, "nearest_structural_barrier", lambda *args, **kwargs: _barrier(100.5))

    candidate = build_day_candidate(
        _analysis(follow_close=100.7),
        "long",
        datetime(2026, 8, 21, tzinfo=timezone.utc),
    )

    assert candidate is not None
    assert candidate["setup_state"] == "VALID"
    assert candidate["entry_state"] == "ENTRY_PROVISIONAL"
    assert candidate["decision"] == "WAIT"  # research-only provisional acceptance is not auto-execution
    assert candidate["state"] == "ARMED"
    assert candidate["reference_entry"] == pytest.approx(100.7)
    assert candidate["reference_entry"] != candidate["trigger"]["price"]
    assert candidate["metrics"]["entry_geometry_mode"] == "FRESH_CURRENT_REFERENCE"
    assert candidate["metrics"]["barrier_before_tp2"] is False
    assert candidate["metrics"]["target_path_valid"] is True
    assert candidate["rr_valid"] is True
    assert candidate["hard_stop"]["requires_candle_close"] is False
