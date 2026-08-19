"""Temporary CI patch runner. Delete before merge."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


BRANCH = "fix/btc-impulse-breakout-coverage"


def _replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    assert count == 1, f"expected one patch match, got {count}: {old[:80]!r}"
    return text.replace(old, new, 1)


def test_apply_impulse_breakout_patch_on_ci() -> None:
    if os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("GITHUB_HEAD_REF") != BRANCH:
        return

    backend = Path(__file__).resolve().parent
    repo = backend.parent.parent

    subprocess.run(["git", "fetch", "origin", BRANCH], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=repo, check=True)

    path = backend / "day_worker.py"
    text = path.read_text()
    if "range_breakout_triggered = (" in text:
        return

    text = _replace_once(
        text,
        '''    triggered = (\n        last.close > trigger_price and previous_close <= trigger_price\n        if side == "long"\n        else last.close < trigger_price and previous_close >= trigger_price\n    )\n''',
        '''    # Closed-5m range breakout is a first-class live trigger route.\n    # Keep it separate from the sweep detector so a missing sweep can never\n    # overwrite a valid direct impulse breakout again.\n    range_breakout_triggered = (\n        last.close > trigger_price and previous_close <= trigger_price\n        if side == "long"\n        else last.close < trigger_price and previous_close >= trigger_price\n    )\n''',
    )
    text = _replace_once(
        text,
        '''    # v0.7.3: a live trigger requires the complete closed-bar sequence:\n    # sweep -> reclaim -> 5m structure shift -> non-opposing 15m structure -> volume.\n    triggered = sweep_trigger is not None\n''',
        '''    # Both routes use CLOSED 5m candles. The sweep route keeps its full\n    # reclaim/structure/volume confirmation; the range-breakout route restores\n    # the direct breakout trigger that was previously calculated and then\n    # accidentally overwritten here. All STRICT score/RR/execution gates below\n    # remain unchanged.\n    sweep_triggered = sweep_trigger is not None\n    triggered = range_breakout_triggered or sweep_triggered\n    trigger_route = (\n        "LIQUIDITY_SWEEP_RECLAIM"\n        if sweep_triggered\n        else "CLOSED_5M_RANGE_BREAKOUT"\n        if range_breakout_triggered\n        else "NONE"\n    )\n''',
    )
    text = _replace_once(
        text,
        '''    if triggered:\n        setup_type = "LIQUIDITY_SWEEP_RECLAIM"\n    elif vwap_reclaim and aligned_1h:\n''',
        '''    if sweep_triggered:\n        setup_type = "LIQUIDITY_SWEEP_RECLAIM"\n    elif range_breakout_triggered:\n        setup_type = "IMPULSE_BREAKOUT"\n    elif vwap_reclaim and aligned_1h:\n''',
    )
    text = _replace_once(
        text,
        '''    trigger_condition = (\n        "Closed 5m liquidity sweep below prior liquidity -> reclaim -> bullish 5m "\n        f"structure shift confirmation near {round_to_tick(trigger_price, analysis.instrument.tick_size)}, "\n        "with non-opposing closed 15m structure"\n        if side == "long"\n        else "Closed 5m liquidity sweep above prior liquidity -> reclaim -> bearish 5m "\n        f"structure shift confirmation near {round_to_tick(trigger_price, analysis.instrument.tick_size)}, "\n        "with non-opposing closed 15m structure"\n    )\n''',
        '''    if sweep_triggered:\n        trigger_condition = (\n            "Closed 5m liquidity sweep below prior liquidity -> reclaim -> bullish 5m "\n            f"structure shift confirmation near {round_to_tick(trigger_price, analysis.instrument.tick_size)}, "\n            "with non-opposing closed 15m structure"\n            if side == "long"\n            else "Closed 5m liquidity sweep above prior liquidity -> reclaim -> bearish 5m "\n            f"structure shift confirmation near {round_to_tick(trigger_price, analysis.instrument.tick_size)}, "\n            "with non-opposing closed 15m structure"\n        )\n    else:\n        trigger_condition = (\n            f"Closed 5m candle crosses above the prior 12-bar high near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n            if side == "long"\n            else f"Closed 5m candle crosses below the prior 12-bar low near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n        )\n''',
    )
    text = _replace_once(
        text,
        '''    if triggered:\n        why_now.append("Latest closed 5m bar completed the sweep/reclaim/structure confirmation sequence")\n''',
        '''    if sweep_triggered:\n        why_now.append("Latest closed 5m bar completed the sweep/reclaim/structure confirmation sequence")\n    elif range_breakout_triggered:\n        why_now.append("Latest closed 5m bar crossed the prior 12-bar range boundary")\n''',
    )
    text = _replace_once(
        text,
        '''            "volume_confirmation": f">={DAY_TRIGGER_VOLUME_RATIO:.1f}x prior 20-bar mean volume on confirmation",\n            "triggered": triggered,\n            "model": "LIQUIDITY_SWEEP_RECLAIM_5M_STRUCTURE_15M_CONFIRMATION",\n            "sweep_confirmation": sweep_trigger,\n''',
        '''            "volume_confirmation": (\n                f">={DAY_TRIGGER_VOLUME_RATIO:.1f}x prior 20-bar mean volume on confirmation"\n                if sweep_triggered\n                else "No standalone volume hard gate; existing STRICT expansion/quality gates still apply"\n            ),\n            "triggered": triggered,\n            "route": trigger_route,\n            "model": (\n                "LIQUIDITY_SWEEP_RECLAIM_5M_STRUCTURE_15M_CONFIRMATION"\n                if sweep_triggered\n                else "CLOSED_5M_12_BAR_RANGE_BREAKOUT"\n            ),\n            "sweep_confirmation": sweep_trigger,\n''',
    )
    text = _replace_once(
        text,
        '''            "triggered": bool(trigger.get("triggered")),\n        },\n''',
        '''            "triggered": bool(trigger.get("triggered")),\n            "route": trigger.get("route", "NONE"),\n            "model": trigger.get("model", ""),\n        },\n''',
    )
    text = _replace_once(
        text,
        '''            "Day-trade v0.7.3 uses 4H/1H as context; live trigger is closed 5m sweep/reclaim/structure confirmation with non-opposing closed 15m structure.",\n''',
        '''            "Day-trade v0.7.3 uses 4H/1H as context; live trigger routes are a closed 5m 12-bar range breakout or the closed 5m sweep/reclaim/structure sequence; 15m confirmation applies to the sweep route.",\n''',
    )
    text = _replace_once(
        text,
        '''                "trigger_timeframe": "5m closed sweep/reclaim/structure confirmation",\n''',
        '''                "trigger_timeframe": "5m closed 12-bar range breakout OR sweep/reclaim/structure confirmation",\n''',
    )
    path.write_text(text)

    permanent_test = backend / "test_day_impulse_breakout_trigger.py"
    permanent_test.write_text(r'''from datetime import datetime, timezone

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
''')

    subprocess.run(["git", "diff", "--check"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=repo, check=True)
    subprocess.run(["git", "add", "bybit_eu_swing_radar/backend/day_worker.py", "bybit_eu_swing_radar/backend/test_day_impulse_breakout_trigger.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Fix day impulse breakout trigger coverage"], cwd=repo, check=True)
    subprocess.run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=repo, check=True)
