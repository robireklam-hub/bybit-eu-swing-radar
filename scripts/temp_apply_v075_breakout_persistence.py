from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "bybit_eu_swing_radar" / "backend"


def replace_exact(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} occurrences, got {actual}: {old[:100]!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def replace_all(path: Path, old: str, new: str, minimum: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual < minimum:
        raise RuntimeError(f"{path}: expected at least {minimum} occurrences, got {actual}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# --- day_worker: versioned persistence semantics ---
day = BACKEND / "day_worker.py"
replace_exact(day, '"""Bybit EU Trading Radar — day-trade worker v0.7.4.', '"""Bybit EU Trading Radar — day-trade worker v0.7.5.')
replace_exact(
    day,
    'LEGACY_DAY_STRATEGY_VERSION = "0.7.3"\nDAY_STRATEGY_VERSION = "0.7.4"',
    'LEGACY_DAY_STRATEGY_VERSION = "0.7.3"\nIMPULSE_DAY_STRATEGY_VERSION = "0.7.4"\nDAY_STRATEGY_VERSION = "0.7.5"\n# A breakout event stays executable on its own closed bar and the immediately\n# following closed 5m bar while the original boundary remains held.\nDAY_BREAKOUT_ACTIVE_BARS = 2',
)

marker = '\ndef resolve_day_trigger_policy(\n'
text = day.read_text(encoding="utf-8")
if marker not in text:
    raise RuntimeError("resolve_day_trigger_policy marker missing")
helper = r'''

def recent_closed_5m_range_breakout(
    bars_5m: list[Bar],
    side: str,
    *,
    active_bars: int = DAY_BREAKOUT_ACTIVE_BARS,
) -> dict[str, Any] | None:
    """Return the newest still-active closed-5m range breakout event.

    The event boundary is anchored to the 12 fully closed bars preceding the
    breakout bar. A newly closed follow-through bar must not erase a valid
    recommendation merely because the crossing happened one bar earlier.
    The state expires after ``active_bars`` closed bars or immediately when a
    close loses the original boundary. No future/unfinished candle is read.
    """
    if side not in {"long", "short"} or active_bars < 1 or len(bars_5m) < 13:
        return None
    current_close = bars_5m[-1].close
    for age_bars in range(active_bars):
        event_index = len(bars_5m) - 1 - age_bars
        if event_index < 12:
            break
        prior = bars_5m[event_index - 12:event_index]
        boundary = (
            max(bar.high for bar in prior)
            if side == "long"
            else min(bar.low for bar in prior)
        )
        previous_close = bars_5m[event_index - 1].close
        event_bar = bars_5m[event_index]
        crossed = (
            event_bar.close > boundary and previous_close <= boundary
            if side == "long"
            else event_bar.close < boundary and previous_close >= boundary
        )
        if not crossed:
            continue
        boundary_held = (
            current_close > boundary if side == "long" else current_close < boundary
        )
        if not boundary_held:
            return None
        return {
            "trigger_price": boundary,
            "event_bar_start_ms": event_bar.start_ms,
            "event_bar_time": _iso_from_ms(event_bar.start_ms),
            "event_close": event_bar.close,
            "age_bars": age_bars,
            "validity_bars": active_bars,
            "boundary_held": True,
            "trigger_window_start_ms": prior[0].start_ms,
        }
    return None
'''
text = text.replace(marker, helper + marker, 1)
day.write_text(text, encoding="utf-8")

old_trigger = '''    previous_5m = analysis.bars_5m[-13:-1]\n    trigger_price = (\n        max(bar.high for bar in previous_5m)\n        if side == "long"\n        else min(bar.low for bar in previous_5m)\n    )\n    last = analysis.bars_5m[-1]\n    previous_close = analysis.bars_5m[-2].close\n    # Closed-5m range breakout is a first-class live trigger route.\n    # Keep it separate from the sweep detector so a missing sweep can never\n    # overwrite a valid direct impulse breakout again.\n    range_breakout_triggered = (\n        last.close > trigger_price and previous_close <= trigger_price\n        if side == "long"\n        else last.close < trigger_price and previous_close >= trigger_price\n    )\n    distance_atr = abs(trigger_price - current) / max(analysis.atr_5m, 1e-12)\n'''
new_trigger = '''    previous_5m = analysis.bars_5m[-13:-1]\n    rolling_trigger_price = (\n        max(bar.high for bar in previous_5m)\n        if side == "long"\n        else min(bar.low for bar in previous_5m)\n    )\n    last = analysis.bars_5m[-1]\n    previous_close = analysis.bars_5m[-2].close\n    range_breakout_crossed_now = (\n        last.close > rolling_trigger_price and previous_close <= rolling_trigger_price\n        if side == "long"\n        else last.close < rolling_trigger_price and previous_close >= rolling_trigger_price\n    )\n    breakout_event = None\n    if strategy_version == DAY_STRATEGY_VERSION:\n        breakout_event = recent_closed_5m_range_breakout(analysis.bars_5m, side)\n        range_breakout_triggered = breakout_event is not None\n    elif strategy_version == IMPULSE_DAY_STRATEGY_VERSION:\n        # Preserve v0.7.4 historical semantics exactly: crossing bar only.\n        range_breakout_triggered = range_breakout_crossed_now\n        if range_breakout_crossed_now:\n            breakout_event = recent_closed_5m_range_breakout(\n                analysis.bars_5m, side, active_bars=1\n            )\n    else:\n        range_breakout_triggered = False\n    trigger_price = (\n        float(breakout_event["trigger_price"])\n        if breakout_event is not None\n        else rolling_trigger_price\n    )\n    distance_atr = abs(trigger_price - current) / max(analysis.atr_5m, 1e-12)\n'''
replace_exact(day, old_trigger, new_trigger)

replace_exact(
    day,
    '    if strategy_version == DAY_STRATEGY_VERSION:\n        if sweep_triggered:\n            return True, "LIQUIDITY_SWEEP_RECLAIM"\n        if range_breakout_triggered:\n            return True, "CLOSED_5M_RANGE_BREAKOUT"\n        return False, "NONE"',
    '    if strategy_version in {IMPULSE_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION}:\n        if sweep_triggered:\n            return True, "LIQUIDITY_SWEEP_RECLAIM"\n        if range_breakout_triggered:\n            return True, "CLOSED_5M_RANGE_BREAKOUT"\n        return False, "NONE"',
)

replace_exact(
    day,
    '    trigger_window_start_ms = previous_5m[0].start_ms\n    if sweep_trigger is not None and sweep_trigger.get("sweep_index") is not None:',
    '    trigger_window_start_ms = (\n        int(breakout_event["trigger_window_start_ms"])\n        if breakout_event is not None\n        else previous_5m[0].start_ms\n    )\n    if sweep_trigger is not None and sweep_trigger.get("sweep_index") is not None:',
)

replace_exact(
    day,
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        trigger_condition = (\n            f"Closed 5m candle crosses above the prior 12-bar high near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n            if side == "long"\n            else f"Closed 5m candle crosses below the prior 12-bar low near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n        )',
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        trigger_condition = (\n            f"Closed 5m breakout above the anchored prior 12-bar high near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}; remains active "\n            f"through the immediate next closed 5m bar while the boundary holds"\n            if side == "long"\n            else f"Closed 5m breakdown below the anchored prior 12-bar low near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}; remains active "\n            f"through the immediate next closed 5m bar while the boundary holds"\n        )',
)

replace_exact(
    day,
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        why_now.append("Latest closed 5m bar crossed the prior 12-bar range boundary")',
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        age_bars = int((breakout_event or {}).get("age_bars", 0))\n        why_now.append(\n            "Latest closed 5m bar crossed the prior 12-bar range boundary"\n            if age_bars == 0\n            else "Prior breakout remains executable on the immediate next closed 5m bar; original boundary is still held"\n        )',
)

replace_exact(
    day,
    '            "sweep_confirmation": sweep_trigger,\n        },',
    '            "sweep_confirmation": sweep_trigger,\n            "event_bar_start_ms": (\n                None if breakout_event is None else int(breakout_event["event_bar_start_ms"])\n            ),\n            "event_bar_time": (\n                None if breakout_event is None else breakout_event["event_bar_time"]\n            ),\n            "age_bars": (None if breakout_event is None else int(breakout_event["age_bars"])),\n            "validity_bars": (\n                None if breakout_event is None else int(breakout_event["validity_bars"])\n            ),\n            "boundary_held": (\n                None if breakout_event is None else bool(breakout_event["boundary_held"])\n            ),\n        },',
)

replace_exact(
    day,
    '            "model": trigger.get("model", ""),\n        },',
    '            "model": trigger.get("model", ""),\n            "event_bar_time": trigger.get("event_bar_time"),\n            "age_bars": trigger.get("age_bars"),\n            "validity_bars": trigger.get("validity_bars"),\n            "boundary_held": trigger.get("boundary_held"),\n        },',
)

# --- journal: dedupe persistent route by original breakout event, not follow-through bar ---
journal = BACKEND / "journal_core.py"
replace_exact(journal, 'STRATEGY_VERSION = "0.7.4"', 'STRATEGY_VERSION = "0.7.5"')
replace_exact(
    journal,
    '    latest_bar = bars_5m[-1]\n    signal_bar_start = _bar_start(latest_bar)\n    opened_at = signal_bar_start + timedelta(minutes=5)',
    '    latest_bar = bars_5m[-1]\n    signal_bar_start = _bar_start(latest_bar)\n    opened_at = signal_bar_start + timedelta(minutes=5)',
)
replace_exact(
    journal,
    '    trigger = candidate.get("trigger") or {}\n    entry_zone = candidate.get("entry_zone") or {}',
    '    trigger = candidate.get("trigger") or {}\n    trigger_event_start_ms = int(_as_float(trigger.get("event_bar_start_ms"), 0.0))\n    signal_key_bar_start = (\n        datetime.fromtimestamp(trigger_event_start_ms / 1000.0, tz=timezone.utc)\n        if trigger_event_start_ms > 0\n        else signal_bar_start\n    )\n    entry_zone = candidate.get("entry_zone") or {}',
)
replace_exact(
    journal,
    '        "signal_key": _signal_key(str(candidate["symbol"]), side, signal_bar_start),',
    '        "signal_key": _signal_key(str(candidate["symbol"]), side, signal_key_bar_start),',
)

# --- backtest: replay the same direct/persistent route as live ---
backtest = BACKEND / "backtest.py"
replace_all(backtest, 'v0.7.4', 'v0.7.5', minimum=3)
replace_exact(backtest, 'STRATEGY_VERSION = "0.7.4"', 'STRATEGY_VERSION = "0.7.5"')
replace_exact(backtest, '"v074-90d-netrr-structural-barrier"', '"v075-90d-netrr-structural-barrier"')
replace_exact(
    backtest,
    '    DAY_ASSUMED_ROUND_TRIP_COST_BPS,\n    DAY_MAX_SPREAD_BPS,',
    '    DAY_ASSUMED_ROUND_TRIP_COST_BPS,\n    DAY_BREAKOUT_ACTIVE_BARS,\n    DAY_MAX_SPREAD_BPS,',
)
replace_exact(
    backtest,
    '    normalize_usdc_universe,\n)',
    '    normalize_usdc_universe,\n    recent_closed_5m_range_breakout,\n)',
)
old_prefilter = '''        trigger_sides = [\n            side\n            for side in ("long", "short")\n            if latest_bar_sweep_setup(\n                bars5_slice,\n                side,\n                config=sweep_config,\n            ) is not None\n        ]\n'''
new_prefilter = '''        trigger_sides = []\n        for side in ("long", "short"):\n            sweep_ready = latest_bar_sweep_setup(\n                bars5_slice,\n                side,\n                config=sweep_config,\n            ) is not None\n            breakout_ready = recent_closed_5m_range_breakout(\n                bars5_slice,\n                side,\n                active_bars=DAY_BREAKOUT_ACTIVE_BARS,\n            ) is not None\n            if sweep_ready or breakout_ready:\n                trigger_sides.append(side)\n'''
replace_exact(backtest, old_prefilter, new_prefilter)

# --- current API / repository / flow parent contract ---
repo = BACKEND / "app" / "repository.py"
replace_exact(repo, 'CURRENT_DAY_STRATEGY_VERSION = "0.7.4"', 'CURRENT_DAY_STRATEGY_VERSION = "0.7.5"')
main = BACKEND / "app" / "main.py"
replace_all(main, '0.7.4', '0.7.5', minimum=4)
flow = BACKEND / "flow_context.py"
replace_all(flow, '0.7.4', '0.7.5', minimum=4)

# --- user-facing contracts: current strategy only; v0.7.4 historical cohort remains in research files ---
openapi = ROOT / "bybit_eu_swing_radar" / "action" / "openapi.yaml"
replace_all(openapi, '0.7.4', '0.7.5', minimum=3)
agent = ROOT / "bybit_eu_swing_radar" / "agent" / "AGENT_INSTRUCTIONS_HU.md"
replace_all(agent, 'Day-trade v0.7.4 külön szabályok', 'Day-trade v0.7.5 külön szabályok', minimum=1)
replace_all(agent, 'v0.7.4-ben', 'v0.7.5-ben', minimum=1)
replace_all(agent, 'day-trade stratégia verziója v0.7.4', 'day-trade stratégia verziója v0.7.5', minimum=1)
agent_text = agent.read_text(encoding="utf-8")
needle = '- TRADE csak akkor mondható, ha az API `category=STRICT`, `state=TRIGGERED`, `decision=TRADE` értékeket ad. WATCH/ARMED nem belépő.\n'
if needle not in agent_text:
    raise RuntimeError("agent insertion point missing")
agent_text = agent_text.replace(
    needle,
    needle + '- A direct 5m breakout crossing esemény a saját lezárt gyertyáján és az azt közvetlenül követő lezárt 5m gyertyán is aktív marad, ha az eredeti 12-bar boundary nem veszett el. A következő 5m gyertya puszta lezárása nem lehet hard-veto.\n',
    1,
)
agent.write_text(agent_text, encoding="utf-8")

spec = ROOT / "bybit_eu_swing_radar" / "BACKEND_SPEC_HU.md"
spec_text = spec.read_text(encoding="utf-8")
section = '''\n## 15. Day-trade v0.7.5 breakout-aktiváció\nA v0.7.4 crossing-bar-only impulse trigger történeti szemantikája változatlanul reprodukálható. A v0.7.5 külön live stratégia-kohorsz.\n\n- A `CLOSED_5M_RANGE_BREAKOUT` esemény az eredeti breakout gyertyán és az azt közvetlenül követő egy lezárt 5m gyertyán aktív marad (`validity_bars=2`), amennyiben az eredeti 12-bar range boundary továbbra is tart.\n- A következő lezárt 5m gyertya puszta megjelenése nem törölheti a már valid breakout ajánlást. Hard invalidáció csak az eredeti boundary elvesztése vagy a meglévő STRICT score/RR/target-path/liquidity/execution gate hibája lehet.\n- A triggerár az eredeti breakout boundaryhez marad horgonyozva; a rolling 12-bar high/low nem ratchetelheti el az aktív eventet a következő gyertyán.\n- Journal deduplikáció az eredeti breakout event gyertyájához kötött, ezért a follow-through gyertya nem hozhat létre duplikált signalt.\n- Historical replay ugyanazt a direct breakout + egy follow-through gyertya triggerutat használja.\n- Journal és historical replay `strategy_version=0.7.5`; a v0.7.3 és v0.7.4 korábbi kohorszok nem kerülnek visszamenőleg átértelmezésre.\n- USDC-only execution, spot long, igazolt USDC spot-margin short és context-only derivatives invariáns változatlan.\n'''
if '## 15. Day-trade v0.7.5 breakout-aktiváció' not in spec_text:
    spec.write_text(spec_text.rstrip() + "\n" + section, encoding="utf-8")

# --- regression tests ---
impulse_test = BACKEND / "test_day_impulse_breakout_trigger.py"
impulse_text = impulse_test.read_text(encoding="utf-8")
impulse_text = impulse_text.replace(
    'def test_v073_policy_keeps_direct_breakout_non_executable(monkeypatch):',
    'def test_v073_policy_keeps_direct_breakout_non_executable(monkeypatch):',
)
extra = r'''

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
'''
if 'test_v075_immediate_next_closed_5m_bar_cannot_erase_valid_breakout' not in impulse_text:
    impulse_test.write_text(impulse_text.rstrip() + extra + "\n", encoding="utf-8")

journal_test = BACKEND / "test_day_breakout_journal_dedupe_v075.py"
journal_test.write_text(r'''from datetime import datetime, timezone

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
    a0=analysis(False); a1=analysis(True)
    c0=build_day_candidate(a0,"long",datetime(2026,8,20,tzinfo=timezone.utc))
    c1=build_day_candidate(a1,"long",datetime(2026,8,20,tzinfo=timezone.utc))
    r0=journal_core.build_signal_record(c0,a0.bars_5m,{})
    r1=journal_core.build_signal_record(c1,a1.bars_5m,{})
    assert r0 is not None and r1 is not None
    assert r0["signal_key"] == r1["signal_key"]
    assert r0["signal_bar_start"] != r1["signal_bar_start"]
    assert c1["trigger"]["age_bars"] == 1
''', encoding="utf-8")

# Existing current-version contract tests.
contract = BACKEND / "test_v073_contract_alignment.py"
replace_all(contract, '0.7.4', '0.7.5', minimum=8)
replace_all(contract, '074', '075', minimum=5)
version_iso = BACKEND / "test_v073_version_isolation.py"
replace_all(version_iso, '0.7.4', '0.7.5', minimum=4)
replace_all(version_iso, '074', '075', minimum=3)
dispatch = BACKEND / "test_v073_railway_backtest_dispatch.py"
replace_exact(dispatch, 'response["strategy_version"] == "0.7.4"', 'response["strategy_version"] == "0.7.5"')
replace_exact(dispatch, 'response["job_name"] == "v074-90d-netrr-structural-barrier"', 'response["job_name"] == "v075-90d-netrr-structural-barrier"')

print("V075_BREAKOUT_PERSISTENCE_PATCH_APPLIED")
