from datetime import datetime, timezone

import day_worker
from day_worker import DayAnalysis, build_day_candidate
from worker import Bar, Instrument


def _bar(index: int, *, close: float = 99.5, high: float = 100.0, low: float = 99.0) -> Bar:
    return Bar(index * 5 * 60 * 1000, 99.5, high, low, close, 10.0, 1_000.0)


def _analysis(*, expansion: float = 80.0, direction: float = 80.0, quality: float = 90.0) -> DayAnalysis:
    prior = [_bar(i) for i in range(12)]
    prior[-1] = _bar(11, close=99.8)
    bars_5m = prior + [_bar(12, close=101.2, high=101.5, low=99.5)]
    bars_15m = [Bar(i * 15 * 60 * 1000, 99.0, 100.0, 98.8, 99.5, 20.0, 2_000.0) for i in range(4)]
    instrument = Instrument(
        symbol="BTCUSDC", base="BTC", quote="USDC", margin_trading="both",
        tick_size=0.1, turnover_24h=1_000_000_000.0, volume_24h=10_000.0,
        last_price=101.2, bid=101.1, ask=101.2, spread_bps=1.0,
        price_change_24h_pct=1.0, tradeable=True, liquidity_reasons=[], discovery_source="mandatory",
    )
    return DayAnalysis(
        instrument=instrument, bars_5m=bars_5m, bars_15m=bars_15m, bars_1h=[], bars_4h=[],
        atr_5m=1.0, atr_15m=2.0, rolling_vwap_24h=99.0,
        ema20_15m=99.0, ema50_15m=98.5, ema20_1h=99.0, ema50_1h=98.0,
        ema20_4h=99.0, ema50_4h=98.0, return_15m_pct=1.0, return_1h_pct=1.0, return_4h_pct=1.0,
        relative_strength_1h=0.0, relative_strength_4h=0.0, volume_ratio_5m=5.0, volume_ratio_15m=2.0,
        atr_ratio_15m=1.2, structure_15m="bullish", structure_1h="bullish", structure_4h="bullish",
        expansion_score=expansion, direction_score=direction, quality_score=quality,
        derivatives={}, missing_data=[], shortable=True, max_borrowing_amount=10.0,
    )


def test_direct_closed_5m_breakout_is_not_overwritten_by_missing_sweep(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    c = build_day_candidate(_analysis(), "long", datetime(2026, 8, 19, tzinfo=timezone.utc))
    assert c is not None
    assert (c["category"], c["state"], c["decision"]) == ("STRICT", "TRIGGERED", "TRADE")
    assert c["setup_type"] == "IMPULSE_BREAKOUT"
    assert c["trigger"]["route"] == "CLOSED_5M_RANGE_BREAKOUT"
    assert c["trigger"]["model"] == "CLOSED_5M_12_BAR_RANGE_BREAKOUT"


def test_direct_closed_5m_short_breakout_is_also_executable_when_strict(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    analysis = _analysis(direction=-80.0)
    prior = [_bar(i) for i in range(12)]
    prior[-1] = _bar(11, close=99.2, high=100.0, low=99.0)
    analysis.bars_5m = prior + [_bar(12, close=98.0, high=99.4, low=97.8)]
    analysis.structure_15m = "bearish"
    analysis.structure_1h = "bearish"
    analysis.structure_4h = "bearish"

    c = build_day_candidate(analysis, "short", datetime(2026, 8, 19, tzinfo=timezone.utc))
    assert c is not None
    assert (c["category"], c["state"], c["decision"]) == ("STRICT", "TRIGGERED", "TRADE")
    assert c["setup_type"] == "IMPULSE_BREAKOUT"
    assert c["trigger"]["route"] == "CLOSED_5M_RANGE_BREAKOUT"


def test_direct_breakout_does_not_bypass_strict_score_gates(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    c = build_day_candidate(_analysis(expansion=30.0, direction=20.0, quality=70.0), "long", datetime(2026, 8, 19, tzinfo=timezone.utc))
    assert c is not None
    assert c["trigger"]["triggered"] is True
    assert c["category"] == "WATCH_ONLY"
    assert c["decision"] == "NO_TRADE"


def test_sweep_route_still_takes_precedence(monkeypatch):
    sweep = {"candidate_entry": 100.8, "candidate_invalidation": 99.2, "sweep_index": 8, "entry_ready": True}
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: sweep)
    c = build_day_candidate(_analysis(), "long", datetime(2026, 8, 19, tzinfo=timezone.utc))
    assert c is not None
    assert (c["category"], c["state"], c["decision"]) == ("STRICT", "TRIGGERED", "TRADE")
    assert c["setup_type"] == "LIQUIDITY_SWEEP_RECLAIM"
    assert c["trigger"]["route"] == "LIQUIDITY_SWEEP_RECLAIM"
    assert c["trigger"]["price"] == 100.8


def test_v073_policy_keeps_direct_breakout_non_executable(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    c = build_day_candidate(
        _analysis(),
        "long",
        datetime(2026, 8, 19, tzinfo=timezone.utc),
        strategy_version="0.7.3",
    )
    assert c is not None
    assert c["strategy_version"] == "0.7.3"
    assert c["trigger"]["triggered"] is False
    assert c["trigger"]["route"] == "NONE"
    assert c["trigger"]["model"] == "NONE"
    assert c["decision"] != "TRADE"

def _with_follow_through(analysis: DayAnalysis, *, close: float = 101.1, high: float = 101.3, low: float = 100.6) -> DayAnalysis:
    analysis.bars_5m = list(analysis.bars_5m) + [_bar(13, close=close, high=high, low=low)]
    analysis.instrument.last_price = close
    analysis.instrument.bid = close - 0.1
    analysis.instrument.ask = close
    return analysis


def test_v075_immediate_next_closed_5m_bar_cannot_erase_valid_breakout(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    c = build_day_candidate(
        _with_follow_through(_analysis()),
        "long",
        datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    assert c is not None
    assert (c["category"], c["state"], c["decision"]) == ("STRICT", "TRIGGERED", "TRADE")
    assert c["trigger"]["route"] == "CLOSED_5M_RANGE_BREAKOUT"
    assert c["trigger"]["age_bars"] == 1
    assert c["trigger"]["validity_bars"] == 2
    assert c["trigger"]["boundary_held"] is True
    assert c["trigger"]["price"] == 100.0


def test_v074_historical_semantics_remain_crossing_bar_only(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    c = build_day_candidate(
        _with_follow_through(_analysis()),
        "long",
        datetime(2026, 8, 20, tzinfo=timezone.utc),
        strategy_version="0.7.4",
    )
    assert c is not None
    assert c["strategy_version"] == "0.7.4"
    assert c["trigger"]["triggered"] is False
    assert c["decision"] != "TRADE"


def test_v075_follow_through_invalidates_only_if_original_boundary_is_lost(monkeypatch):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    c = build_day_candidate(
        _with_follow_through(_analysis(), close=99.8, high=101.0, low=99.5),
        "long",
        datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    assert c is not None
    assert c["trigger"]["triggered"] is False
    assert c["decision"] != "TRADE"

