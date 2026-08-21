from datetime import datetime, timezone

import day_worker
import journal_core
from day_worker import DayAnalysis, build_day_candidate
from worker import Bar, Instrument


def bar(i, close=99.5, high=100.0, low=99.0):
    return Bar(i * 300_000, 99.5, high, low, close, 10.0, 1000.0)


def analysis(follow=False):
    prior=[bar(i) for i in range(12)]
    prior[-1]=bar(11, close=99.8)
    bars=prior+[bar(12, close=101.2, high=101.5, low=99.5)]
    if follow:
        bars.append(bar(13, close=101.1, high=101.3, low=100.6))
    inst=Instrument(symbol="BTCUSDC",base="BTC",quote="USDC",margin_trading="both",tick_size=0.1,turnover_24h=1e9,volume_24h=1e4,last_price=bars[-1].close,bid=bars[-1].close-0.1,ask=bars[-1].close,spread_bps=1.0,price_change_24h_pct=1.0,tradeable=True,liquidity_reasons=[],discovery_source="mandatory")
    bars15=[Bar(i*900_000,99,100,98.8,99.5,20,2000) for i in range(4)]
    return DayAnalysis(instrument=inst,bars_5m=bars,bars_15m=bars15,bars_1h=[],bars_4h=[],atr_5m=1.0,atr_15m=2.0,rolling_vwap_24h=99.0,ema20_15m=99.0,ema50_15m=98.5,ema20_1h=99.0,ema50_1h=98.0,ema20_4h=99.0,ema50_4h=98.0,return_15m_pct=1.0,return_1h_pct=1.0,return_4h_pct=1.0,relative_strength_1h=0.0,relative_strength_4h=0.0,volume_ratio_5m=5.0,volume_ratio_15m=2.0,atr_ratio_15m=1.2,structure_15m="bullish",structure_1h="bullish",structure_4h="bullish",expansion_score=80.0,direction_score=80.0,quality_score=90.0,derivatives={},missing_data=[],shortable=True,max_borrowing_amount=10.0)


def test_persistent_follow_through_reuses_original_breakout_signal_key(monkeypatch):
    monkeypatch.setattr(day_worker,"latest_bar_sweep_setup",lambda *a,**k:None)
    monkeypatch.setattr(journal_core,"STRATEGY_VERSION","0.7.5")
    a0=analysis(False); a1=analysis(True)
    c0=build_day_candidate(a0,"long",datetime(2026,8,20,tzinfo=timezone.utc),strategy_version="0.7.5")
    c1=build_day_candidate(a1,"long",datetime(2026,8,20,tzinfo=timezone.utc),strategy_version="0.7.5")
    r0=journal_core.build_signal_record(c0,a0.bars_5m,{})
    r1=journal_core.build_signal_record(c1,a1.bars_5m,{})
    assert r0 is not None and r1 is not None
    assert r0["signal_key"] == r1["signal_key"]
    assert r0["signal_bar_start"] != r1["signal_bar_start"]
    assert c1["trigger"]["age_bars"] == 1
