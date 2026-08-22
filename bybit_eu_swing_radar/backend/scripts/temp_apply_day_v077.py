from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "bybit_eu_swing_radar" / "backend"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 occurrence, found {count}")
    return text.replace(old, new, 1)


worker_path = BACKEND / "day_worker.py"
text = worker_path.read_text()
text = replace_once(text, 'day-trade worker v0.7.6.', 'day-trade worker v0.7.7.', 'doc version')
text = replace_once(
    text,
    'V075_DAY_STRATEGY_VERSION = "0.7.5"\nDAY_STRATEGY_VERSION = "0.7.6"',
    'V075_DAY_STRATEGY_VERSION = "0.7.5"\nV076_DAY_STRATEGY_VERSION = "0.7.6"\nDAY_STRATEGY_VERSION = "0.7.7"',
    'version constants',
)
text = replace_once(
    text,
    'if strategy_version in {IMPULSE_DAY_STRATEGY_VERSION, V075_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION}:',
    'if strategy_version in {IMPULSE_DAY_STRATEGY_VERSION, V075_DAY_STRATEGY_VERSION, V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION}:',
    'trigger version set',
)
text = replace_once(
    text,
    '    if strategy_version == DAY_STRATEGY_VERSION:\n        breakout_event = active_structural_breakout_context(analysis.bars_5m, side)\n        persistent_breakout_context = breakout_event is not None\n        range_breakout_triggered = bool(\n            breakout_event is not None and int(breakout_event.get("age_bars", -1)) == 0\n        )',
    '    if strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION}:\n        breakout_event = active_structural_breakout_context(analysis.bars_5m, side)\n        persistent_breakout_context = breakout_event is not None\n        range_breakout_triggered = bool(\n            breakout_event is not None and int(breakout_event.get("age_bars", -1)) == 0\n        )',
    'structural context version isolation',
)
text = replace_once(
    text,
    '    triggered, trigger_route = resolve_day_trigger_policy(\n        strategy_version,\n        range_breakout_triggered=range_breakout_triggered,\n        sweep_triggered=sweep_triggered,\n    )\n',
    '    triggered, trigger_route = resolve_day_trigger_policy(\n        strategy_version,\n        range_breakout_triggered=range_breakout_triggered,\n        sweep_triggered=sweep_triggered,\n    )\n    # v0.7.7: once a fully closed 5m bar confirms the breakout origin, the\n    # recommendation remains confirmed while every later fully closed 5m bar\n    # holds that same original boundary. Passage of one more 5m candle is not\n    # an invalidation. Fresh setup/execution/RR/barrier gates below are still\n    # recomputed on every run and can independently withdraw the recommendation.\n    if (\n        strategy_version == DAY_STRATEGY_VERSION\n        and not triggered\n        and persistent_breakout_context\n    ):\n        triggered = True\n        trigger_route = "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"\n',
    'persistent confirmed trigger',
)
text = replace_once(
    text,
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT" or (\n        strategy_version == DAY_STRATEGY_VERSION and persistent_breakout_context\n    ):',
    '    elif trigger_route in {"CLOSED_5M_RANGE_BREAKOUT", "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"} or (\n        strategy_version == V076_DAY_STRATEGY_VERSION and persistent_breakout_context\n    ):',
    'setup type persistent route',
)
text = replace_once(
    text,
    '        strategy_version == DAY_STRATEGY_VERSION\n        and persistent_breakout_context\n        and sweep_trigger is None',
    '        strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION}\n        and persistent_breakout_context\n        and sweep_trigger is None',
    'fresh geometry version set',
)
text = replace_once(
    text,
    '    if strategy_version == DAY_STRATEGY_VERSION:\n        entry_state = classify_entry_state(',
    '    if strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION}:\n        entry_state = classify_entry_state(',
    'state machine version set',
)
text = replace_once(
    text,
    '        if strict or (strategy_version == DAY_STRATEGY_VERSION and technical_setup)',
    '        if strict or (strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION} and technical_setup)',
    'display grade version set',
)
text = replace_once(
    text,
    '    elif strategy_version == DAY_STRATEGY_VERSION and technical_setup:',
    '    elif strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION} and technical_setup:',
    'watch bucket version set',
)
text = replace_once(
    text,
    '        strategy_version == DAY_STRATEGY_VERSION and technical_setup\n    ):',
    '        strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION} and technical_setup\n    ):',
    'watch-only decision version set',
)
text = replace_once(
    text,
    '    elif strategy_version == DAY_STRATEGY_VERSION and persistent_breakout_context:\n        trigger_condition = (',
    '    elif strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION} and persistent_breakout_context:\n        trigger_condition = (',
    'persistent condition version set',
)
text = replace_once(
    text,
    '    if strategy_version == DAY_STRATEGY_VERSION and technical_setup:\n        weakest = {',
    '    if strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION} and technical_setup:\n        weakest = {',
    'weakest point version set',
)
text = replace_once(
    text,
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT" or (\n        strategy_version == DAY_STRATEGY_VERSION and persistent_breakout_context\n    ):',
    '    elif trigger_route in {"CLOSED_5M_RANGE_BREAKOUT", "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"} or (\n        strategy_version == V076_DAY_STRATEGY_VERSION and persistent_breakout_context\n    ):',
    'why-now route',
)
text = replace_once(
    text,
    '        "breakout_context": breakout_event if strategy_version == DAY_STRATEGY_VERSION else None,',
    '        "breakout_context": breakout_event if strategy_version in {V076_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION} else None,',
    'breakout context output',
)
text = replace_once(
    text,
    '                if trigger_route == "CLOSED_5M_RANGE_BREAKOUT"\n                else "Not triggered"',
    '                if trigger_route in {"CLOSED_5M_RANGE_BREAKOUT", "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"}\n                else "Not triggered"',
    'held route volume semantics',
)
text = replace_once(
    text,
    '                else "CLOSED_5M_12_BAR_RANGE_BREAKOUT"\n                if trigger_route == "CLOSED_5M_RANGE_BREAKOUT"\n                else "STRUCTURE_PERSISTENT_5M_12_BAR_RANGE_BREAKOUT"',
    '                else "CLOSED_5M_12_BAR_RANGE_BREAKOUT"\n                if trigger_route == "CLOSED_5M_RANGE_BREAKOUT"\n                else "STRUCTURE_PERSISTENT_CONFIRMED_5M_12_BAR_RANGE_BREAKOUT"\n                if trigger_route == "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"\n                else "STRUCTURE_PERSISTENT_5M_12_BAR_RANGE_BREAKOUT"',
    'trigger model',
)
text = text.replace('Day-trade v0.7.6 separates setup validity from entry readiness:', 'Day-trade v0.7.7 preserves confirmed breakout recommendations across later closed 5m bars while the original boundary and all fresh execution gates remain valid:')
text = text.replace('"trigger_timeframe": "v0.7.6 setup context persists structurally; executable confirmation remains closed-5m breakout/sweep while intrabar provisional acceptance is research-only",', '"trigger_timeframe": "v0.7.7 confirmed breakout persists across later closed 5m bars while the original boundary and fresh setup/execution/RR/barrier gates remain valid",')
text = text.replace('"Prospective journal records are version-separated; v0.7.6 creates no historical backfill into v0.7.3-v0.7.5 cohorts.",', '"Prospective journal records are version-separated; v0.7.7 creates no historical backfill into v0.7.3-v0.7.6 cohorts.",')
worker_path.write_text(text)

# Journal is a new prospective cohort; signal key remains anchored to the
# original breakout event bar, preventing one signal per follow-through candle.
journal = BACKEND / "journal_core.py"
jt = journal.read_text()
jt = replace_once(jt, 'STRATEGY_VERSION = "0.7.6"', 'STRATEGY_VERSION = "0.7.7"', 'journal strategy version')
journal.write_text(jt)

repo = BACKEND / "app" / "repository.py"
rt = repo.read_text()
rt = replace_once(rt, 'CURRENT_DAY_STRATEGY_VERSION = "0.7.6"', 'CURRENT_DAY_STRATEGY_VERSION = "0.7.7"', 'repository strategy version')
repo.write_text(rt)

main = BACKEND / "app" / "main.py"
mt = main.read_text()
mt = replace_once(mt, 'version="0.7.6"', 'version="0.7.7"', 'API version')
mt = replace_once(mt, 'day-trade strategy v0.7.6 with separated setup/entry state', 'day-trade strategy v0.7.7 with persistent confirmed-breakout recommendation state', 'API description')
main.write_text(mt)

# Regression tests target the exact failure: the next CLOSED 5m bar must not
# revoke a valid confirmed recommendation; real invalidations still must.
test_path = BACKEND / "test_day_v077_persistent_recommendation.py"
test_path.write_text(r'''from datetime import datetime, timezone

import pytest

import day_worker
from day_worker import DayAnalysis, build_day_candidate
from journal_core import build_signal_record
from worker import Bar, Instrument


def _bar(i: int, *, close: float = 99.5, high: float = 100.0, low: float = 99.0) -> Bar:
    return Bar(i * 300_000, 99.5, high, low, close, 10.0, 1000.0)


def _analysis(*, follow_close: float, tradeable: bool = True, shortable: bool = True) -> DayAnalysis:
    prior = [_bar(i) for i in range(12)]
    prior[-1] = _bar(11, close=99.8)
    bars = prior + [_bar(12, close=100.2, high=100.4, low=99.5)]
    bars.append(_bar(13, close=follow_close, high=max(follow_close + 0.2, 100.4), low=100.05))
    instrument = Instrument(
        symbol="BTCUSDC", base="BTC", quote="USDC", margin_trading="both", tick_size=0.1,
        turnover_24h=1e9, volume_24h=1e4, last_price=follow_close,
        bid=follow_close - 0.1, ask=follow_close, spread_bps=1.0,
        price_change_24h_pct=1.0, tradeable=tradeable,
        liquidity_reasons=[] if tradeable else ["blocked"], discovery_source="mandatory",
    )
    bars15 = [Bar(i * 900_000, 99.0, 100.0, 98.8, 99.5, 20.0, 2000.0) for i in range(8)]
    return DayAnalysis(
        instrument=instrument, bars_5m=bars, bars_15m=bars15, bars_1h=[], bars_4h=[],
        atr_5m=1.0, atr_15m=2.0, rolling_vwap_24h=99.0,
        ema20_15m=99.0, ema50_15m=98.5, ema20_1h=99.0, ema50_1h=98.0,
        ema20_4h=99.0, ema50_4h=98.0, return_15m_pct=1.0, return_1h_pct=1.0,
        return_4h_pct=1.0, relative_strength_1h=0.0, relative_strength_4h=0.0,
        volume_ratio_5m=1.84, volume_ratio_15m=3.83, atr_ratio_15m=1.2,
        structure_15m="bullish", structure_1h="bullish", structure_4h="bullish",
        expansion_score=62.05, direction_score=57.6, quality_score=99.99,
        derivatives={}, missing_data=[], shortable=shortable, max_borrowing_amount=10.0,
    )


def _no_barrier(*args, **kwargs):
    return None


def _candidate(monkeypatch, *, follow_close=100.35, strategy_version="0.7.7", tradeable=True):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(day_worker, "nearest_structural_barrier", _no_barrier)
    return build_day_candidate(
        _analysis(follow_close=follow_close, tradeable=tradeable),
        "long", datetime(2026, 8, 22, tzinfo=timezone.utc), strategy_version=strategy_version,
    )


def test_next_closed_5m_bar_cannot_revoke_valid_confirmed_breakout(monkeypatch):
    candidate = _candidate(monkeypatch)
    assert candidate is not None
    assert candidate["strategy_version"] == "0.7.7"
    assert candidate["trigger"]["age_bars"] == 1
    assert candidate["trigger"]["boundary_held"] is True
    assert candidate["trigger"]["triggered"] is True
    assert candidate["trigger"]["route"] == "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"
    assert candidate["setup_state"] == "VALID"
    assert candidate["rr_valid"] is True
    assert candidate["execution_valid"] is True
    assert candidate["entry_state"] == "ENTRY_CONFIRMED"
    assert candidate["category"] == "STRICT"
    assert candidate["state"] == "TRIGGERED"
    assert candidate["decision"] == "TRADE"


def test_v076_historical_behavior_remains_provisional_on_followthrough(monkeypatch):
    candidate = _candidate(monkeypatch, strategy_version="0.7.6")
    assert candidate is not None
    assert candidate["trigger"]["age_bars"] == 1
    assert candidate["trigger"]["triggered"] is False
    assert candidate["entry_state"] == "ENTRY_PROVISIONAL"
    assert candidate["state"] == "ARMED"
    assert candidate["decision"] == "WAIT"


def test_execution_block_still_revokes_trade(monkeypatch):
    candidate = _candidate(monkeypatch, tradeable=False)
    assert candidate is not None
    assert candidate["trigger"]["triggered"] is True
    assert candidate["execution_valid"] is False
    assert candidate["entry_state"] == "EXECUTION_BLOCKED"
    assert candidate["decision"] == "NO_TRADE"


def test_boundary_loss_removes_persistent_confirmation(monkeypatch):
    analysis = _analysis(follow_close=99.7)
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(day_worker, "nearest_structural_barrier", _no_barrier)
    candidate = build_day_candidate(analysis, "long", datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert candidate is not None
    assert candidate["trigger"]["route"] != "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"
    assert candidate["decision"] != "TRADE"


def test_persistent_followthrough_reuses_origin_event_for_journal_dedupe(monkeypatch):
    c1 = _candidate(monkeypatch, follow_close=100.35)
    c2 = _candidate(monkeypatch, follow_close=100.45)
    assert c1 and c2
    bars1 = _analysis(follow_close=100.35).bars_5m
    bars2 = _analysis(follow_close=100.45).bars_5m
    regime = {"data_quality": "GOOD"}
    r1 = build_signal_record(c1, bars1, regime)
    r2 = build_signal_record(c2, bars2, regime)
    assert r1 is not None and r2 is not None
    assert c1["trigger"]["event_bar_start_ms"] == c2["trigger"]["event_bar_start_ms"]
    assert r1["signal_key"] == r2["signal_key"]
''')

print("DAY_V077_PATCH_APPLIED")
