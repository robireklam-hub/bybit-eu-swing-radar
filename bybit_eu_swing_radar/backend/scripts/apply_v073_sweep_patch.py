from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def remove_once(path: Path, old: str) -> None:
    replace_once(path, old, "")


# --- sweep_research.py: add an efficient current-bar confirmation helper ---
sweep = ROOT / "sweep_research.py"
replace_once(
    sweep,
    '''def latest_sweep_setup(\n    bars_5m: Iterable[Any],\n    side: Side,\n    *,\n    bars_15m: Iterable[Any] | None = None,\n    config: SweepResearchConfig = DEFAULT_CONFIG,\n    entry_ready_only: bool = False,\n) -> dict[str, Any] | None:\n    events = scan_sweep_setups(\n        bars_5m,\n        side,\n        bars_15m=bars_15m,\n        config=config,\n        include_incomplete=not entry_ready_only,\n    )\n    if entry_ready_only:\n        events = [event for event in events if event["entry_ready"]]\n    return events[-1] if events else None\n\n\ndef config_dict''',
    '''def latest_sweep_setup(\n    bars_5m: Iterable[Any],\n    side: Side,\n    *,\n    bars_15m: Iterable[Any] | None = None,\n    config: SweepResearchConfig = DEFAULT_CONFIG,\n    entry_ready_only: bool = False,\n) -> dict[str, Any] | None:\n    events = scan_sweep_setups(\n        bars_5m,\n        side,\n        bars_15m=bars_15m,\n        config=config,\n        include_incomplete=not entry_ready_only,\n    )\n    if entry_ready_only:\n        events = [event for event in events if event["entry_ready"]]\n    return events[-1] if events else None\n\n\ndef latest_bar_sweep_setup(\n    bars_5m: Iterable[Any],\n    side: Side,\n    *,\n    bars_15m: Iterable[Any] | None = None,\n    config: SweepResearchConfig = DEFAULT_CONFIG,\n) -> dict[str, Any] | None:\n    """Return an entry-ready sweep whose 5m confirmation is the latest closed bar.\n\n    Only sweep starts inside the bounded confirmation window are evaluated, so live\n    and historical replay can call this on every closed 5m bar without rescanning\n    the full history.\n    """\n    bars = normalize_bars(bars_5m)\n    if not bars:\n        return None\n    fifteen = normalize_bars(bars_15m) if bars_15m is not None else None\n    required_history = max(\n        config.liquidity_lookback,\n        config.structure_lookback_5m,\n        config.atr_period,\n    )\n    first_sweep = max(required_history, len(bars) - 1 - config.max_confirmation_bars)\n    latest_time = iso_from_ms(bars[-1].start_ms)\n    for sweep_index in range(first_sweep, len(bars)):\n        event = _evaluate_sweep_normalized(\n            bars,\n            sweep_index,\n            side,\n            bars_15m=fifteen,\n            config=config,\n        )\n        if (\n            event.get("entry_ready")\n            and event.get("structure_shift_time_5m") == latest_time\n        ):\n            return event\n    return None\n\n\ndef config_dict''',
)
replace_once(
    sweep,
    '    "latest_sweep_setup",\n',
    '    "latest_sweep_setup",\n    "latest_bar_sweep_setup",\n',
)

# --- day_worker.py: promote the research detector into the v0.7.3 live trigger ---
day = ROOT / "day_worker.py"
text = day.read_text(encoding="utf-8")
text = text.replace("day-trade worker v0.7.2", "day-trade worker v0.7.3", 1)
text = text.replace("matching strategy v0.7.2", "matching strategy v0.7.3")
text = text.replace("v0.7.2 creates no historical backfill", "v0.7.3 creates no historical backfill")
text = text.replace('"strategy_version": "0.7.2"', '"strategy_version": DAY_STRATEGY_VERSION')
text = text.replace('"User-Agent": "Bybit-EU-Trading-Radar-Day/0.7.2"', '"User-Agent": f"Bybit-EU-Trading-Radar-Day/{DAY_STRATEGY_VERSION}"')
day.write_text(text, encoding="utf-8")

replace_once(
    day,
    'from journal import persist_day_journal\n\nfrom worker import (',
    'from journal import persist_day_journal\nfrom sweep_research import SweepResearchConfig, latest_bar_sweep_setup\n\nfrom worker import (',
)
replace_once(
    day,
    'DATABASE_URL = os.getenv("DATABASE_URL", "")\n',
    'DATABASE_URL = os.getenv("DATABASE_URL", "")\nDAY_STRATEGY_VERSION = "0.7.3"\n',
)
remove_once(
    day,
    '    if conflict_4h:\n        return "TIMEFRAME_CONFLICT"\n',
)
remove_once(
    day,
    '        and not item["timeframe_conflict"]\n',
)

replace_once(
    day,
    '''    conflict_4h = (\n        "bearish" in analysis.structure_4h\n        if side == "long"\n        else "bullish" in analysis.structure_4h\n    )\n\n    previous_above_vwap''',
    '''    conflict_4h = (\n        "bearish" in analysis.structure_4h\n        if side == "long"\n        else "bullish" in analysis.structure_4h\n    )\n\n    sweep_config = SweepResearchConfig(\n        volume_confirmation_ratio=DAY_TRIGGER_VOLUME_RATIO\n    )\n    sweep_trigger = latest_bar_sweep_setup(\n        analysis.bars_5m,\n        side,\n        bars_15m=analysis.bars_15m,\n        config=sweep_config,\n    )\n    # v0.7.3: a live trigger requires the complete closed-bar sequence:\n    # sweep -> reclaim -> 5m structure shift -> non-opposing 15m structure -> volume.\n    triggered = sweep_trigger is not None\n\n    previous_above_vwap''',
)
replace_once(
    day,
    '''    if triggered and aligned_15m:\n        setup_type = "INTRADAY_BREAKOUT"\n''',
    '''    if triggered:\n        setup_type = "LIQUIDITY_SWEEP_RECLAIM"\n''',
)
replace_once(
    day,
    '''    else:\n        stop = max(\n            max(bar.high for bar in recent),\n            trigger_price + 1.2 * analysis.atr_5m,\n        )\n        entry_low = trigger_price - 0.15 * analysis.atr_5m\n        entry_high = trigger_price\n        invalidation = (\n            f"Closed 5m candle above {round_to_tick(stop, analysis.instrument.tick_size)} "\n            "or reclaim of the 15m lower-high structure"\n        )\n\n    entry = trigger_price\n''',
    '''    else:\n        stop = max(\n            max(bar.high for bar in recent),\n            trigger_price + 1.2 * analysis.atr_5m,\n        )\n        entry_low = trigger_price - 0.15 * analysis.atr_5m\n        entry_high = trigger_price\n        invalidation = (\n            f"Closed 5m candle above {round_to_tick(stop, analysis.instrument.tick_size)} "\n            "or reclaim of the 15m lower-high structure"\n        )\n\n    if sweep_trigger is not None:\n        trigger_price = float(sweep_trigger["candidate_entry"])\n        stop = float(sweep_trigger["candidate_invalidation"])\n        entry_low = trigger_price\n        entry_high = trigger_price\n        distance_atr = abs(trigger_price - current) / max(analysis.atr_5m, 1e-12)\n        invalidation = (\n            f"Sweep extreme {round_to_tick(stop, analysis.instrument.tick_size)} is invalidated"\n        )\n\n    entry = trigger_price\n''',
)
replace_once(
    day,
    '    trigger_window_start_ms = previous_5m[0].start_ms\n',
    '''    trigger_window_start_ms = previous_5m[0].start_ms\n    if sweep_trigger is not None and sweep_trigger.get("sweep_index") is not None:\n        sweep_index = int(sweep_trigger["sweep_index"])\n        if 0 <= sweep_index < len(analysis.bars_5m):\n            trigger_window_start_ms = analysis.bars_5m[sweep_index].start_ms\n''',
)
replace_once(
    day,
    '    strict = strict_execution and strict_scores and not conflict_4h\n',
    '    strict = strict_execution and strict_scores\n',
)
replace_once(
    day,
    '    if strict and triggered and analysis.volume_ratio_5m >= DAY_TRIGGER_VOLUME_RATIO:\n',
    '    if strict and triggered:\n',
)
remove_once(
    day,
    '    if conflict_4h:\n        liquidity_reasons.append("4H timeframe conflicts with the proposed day-trade direction")\n',
)
replace_once(
    day,
    '''    execution_status = (\n        "DAY_TRADE_EXECUTABLE"\n        if strict_execution and not conflict_4h\n        else "DAY_TRADE_BLOCKED"\n    )\n''',
    '''    execution_status = (\n        "DAY_TRADE_EXECUTABLE"\n        if strict_execution\n        else "DAY_TRADE_BLOCKED"\n    )\n''',
)
replace_once(
    day,
    '''    trigger_condition = (\n        f"Closed 5m candle above {round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n        if side == "long"\n        else f"Closed 5m candle below {round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n    )\n''',
    '''    trigger_condition = (\n        "Closed 5m liquidity sweep below prior liquidity -> reclaim -> bullish 5m "\n        f"structure shift confirmation near {round_to_tick(trigger_price, analysis.instrument.tick_size)}, "\n        "with non-opposing closed 15m structure"\n        if side == "long"\n        else "Closed 5m liquidity sweep above prior liquidity -> reclaim -> bearish 5m "\n        f"structure shift confirmation near {round_to_tick(trigger_price, analysis.instrument.tick_size)}, "\n        "with non-opposing closed 15m structure"\n    )\n''',
)
replace_once(
    day,
    '''    if triggered:\n        why_now.append("The latest closed 5m candle crossed the trigger level")\n''',
    '''    if triggered:\n        why_now.append("Latest closed 5m bar completed the sweep/reclaim/structure confirmation sequence")\n    if conflict_4h:\n        why_now.append("4H structure conflicts with the side but is context-only in v0.7.3")\n''',
)
replace_once(
    day,
    '''            "volume_confirmation": f">={DAY_TRIGGER_VOLUME_RATIO:.1f}x recent 5m average volume",\n            "triggered": triggered,\n''',
    '''            "volume_confirmation": f">={DAY_TRIGGER_VOLUME_RATIO:.1f}x prior 20-bar mean volume on confirmation",\n            "triggered": triggered,\n            "model": "LIQUIDITY_SWEEP_RECLAIM_5M_STRUCTURE_15M_CONFIRMATION",\n            "sweep_confirmation": sweep_trigger,\n''',
)
replace_once(
    day,
    '''            "distance_to_trigger_atr_5m": round(distance_atr, 3),\n''',
    '''            "distance_to_trigger_atr_5m": round(distance_atr, 3),\n            "sweep_confirmation": sweep_trigger,\n            "four_hour_conflict_context_only": conflict_4h,\n''',
)
replace_once(
    day,
    '''            "Day-trade context uses 4H/1H, setup 15m and closed 5m trigger.",\n''',
    '''            "Day-trade v0.7.3 uses 4H/1H as context; live trigger is closed 5m sweep/reclaim/structure confirmation with non-opposing closed 15m structure.",\n            "4H conflict is context-only and does not veto strict eligibility or execution.",\n''',
)
replace_once(
    day,
    '''                "trigger_timeframe": "5m closed candle",\n''',
    '''                "trigger_timeframe": "5m closed sweep/reclaim/structure confirmation",\n                "confirmation_timeframe": "15m closed non-opposing structure",\n                "four_hour_role": "CONTEXT_ONLY",\n''',
)

# --- journal.py: isolate v0.7.3 samples and stop using 4H conflict as a SHADOW veto ---
journal = ROOT / "journal.py"
replace_once(journal, 'STRATEGY_VERSION = "0.7.2"', 'STRATEGY_VERSION = "0.7.3"')
remove_once(
    journal,
    '        and not bool(candidate.get("timeframe_conflict"))\n',
)

# --- backtest.py: v0.7.3 must discover sweep confirmations rather than legacy breakouts ---
backtest = ROOT / "backtest.py"
replace_once(backtest, 'STRATEGY_VERSION = "0.7.2"', 'STRATEGY_VERSION = "0.7.3"')
replace_once(
    backtest,
    '''    DAY_MIN_TURNOVER_USDC,\n    DayAnalysis,\n''',
    '''    DAY_MIN_TURNOVER_USDC,\n    DAY_TRIGGER_VOLUME_RATIO,\n    DayAnalysis,\n''',
)
replace_once(
    backtest,
    'from worker import Bar, BybitAPI, Instrument, safe_float\n',
    'from sweep_research import SweepResearchConfig, latest_bar_sweep_setup\nfrom worker import Bar, BybitAPI, Instrument, safe_float\n',
)
remove_once(
    backtest,
    '        and not bool(candidate.get("timeframe_conflict"))\n',
)
replace_once(
    backtest,
    '''        previous_window = bars_5m[index - 12:index]\n        if len(previous_window) < 12:\n            continue\n        previous_close = bars_5m[index - 1].close\n        prior_high = max(bar.high for bar in previous_window)\n        prior_low = min(bar.low for bar in previous_window)\n        trigger_sides: list[str] = []\n        if current_bar.close > prior_high and previous_close <= prior_high:\n            trigger_sides.append("long")\n        if current_bar.close < prior_low and previous_close >= prior_low:\n            trigger_sides.append("short")\n        if not trigger_sides:\n            continue\n        bars5_slice = bars_5m[max(0, index - 219):index + 1]\n''',
    '''        bars5_slice = bars_5m[max(0, index - 219):index + 1]\n        sweep_config = SweepResearchConfig(\n            volume_confirmation_ratio=DAY_TRIGGER_VOLUME_RATIO\n        )\n        trigger_sides = [\n            side\n            for side in ("long", "short")\n            if latest_bar_sweep_setup(\n                bars5_slice,\n                side,\n                config=sweep_config,\n            ) is not None\n        ]\n        if not trigger_sides:\n            continue\n''',
)

print("v0.7.3 sweep patch applied")
