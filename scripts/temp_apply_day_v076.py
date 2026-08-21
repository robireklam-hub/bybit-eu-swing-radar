from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all_checked(path: Path, old: str, new: str, minimum: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(f"{path}: expected >= {minimum} matches, found {count}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


backend = ROOT / "bybit_eu_swing_radar" / "backend"
day = backend / "day_worker.py"
models = backend / "app" / "models.py"
repo = backend / "app" / "repository.py"
main = backend / "app" / "main.py"
journal = backend / "journal_core.py"
backtest = backend / "backtest.py"
flow_context = backend / "flow_context.py"

replace_once(
    day,
    '"""Bybit EU Trading Radar — day-trade worker v0.7.5.\n',
    '"""Bybit EU Trading Radar — day-trade worker v0.7.6.\n',
)
replace_once(
    day,
    '- trigger: closed 5m candle\n',
    '- setup context: structure-persistent 5m breakout/sweep context; entry confirmation is separate\n',
)
replace_once(
    day,
    'from journal import persist_day_journal\nfrom sweep_research import SweepResearchConfig, latest_bar_sweep_setup\n',
    'from journal import persist_day_journal\nfrom day_v076 import (\n    active_structural_breakout_context,\n    classify_entry_state,\n    fresh_entry_zone,\n    hard_stop_contract,\n    setup_state_from_validity,\n    technical_setup_valid,\n)\nfrom sweep_research import SweepResearchConfig, latest_bar_sweep_setup\n',
)
replace_once(
    day,
    'LEGACY_DAY_STRATEGY_VERSION = "0.7.3"\nIMPULSE_DAY_STRATEGY_VERSION = "0.7.4"\nDAY_STRATEGY_VERSION = "0.7.5"\n# A breakout event stays executable on its own closed bar and the immediately\n# following closed 5m bar while the original boundary remains held.\nDAY_BREAKOUT_ACTIVE_BARS = 2\n',
    'LEGACY_DAY_STRATEGY_VERSION = "0.7.3"\nIMPULSE_DAY_STRATEGY_VERSION = "0.7.4"\nV075_DAY_STRATEGY_VERSION = "0.7.5"\nDAY_STRATEGY_VERSION = "0.7.6"\n# Historical v0.7.5 compatibility only. v0.7.6 setup context has no fixed\n# 5m-bar TTL; entry confirmation is classified separately.\nDAY_BREAKOUT_ACTIVE_BARS = 2\nDAY_MAX_PROVISIONAL_EXTENSION_ATR = env_float("DAY_MAX_PROVISIONAL_EXTENSION_ATR", 1.0)\n',
)
replace_once(
    day,
    '    entry = float(trigger.get("price") or 0.0)\n    return {\n',
    '    entry = float(candidate.get("reference_entry") or trigger.get("price") or 0.0)\n    return {\n',
)
replace_once(
    day,
    '        "watch_bucket": candidate.get("watch_bucket"),\n        "tradeable": bool(candidate.get("tradeable")),\n',
    '        "watch_bucket": candidate.get("watch_bucket"),\n        "setup_state": candidate.get("setup_state"),\n        "entry_state": candidate.get("entry_state"),\n        "execution_valid": candidate.get("execution_valid"),\n        "rr_valid": candidate.get("rr_valid"),\n        "reference_entry": candidate.get("reference_entry"),\n        "hard_stop": candidate.get("hard_stop"),\n        "structure_invalidation": candidate.get("structure_invalidation"),\n        "tradeable": bool(candidate.get("tradeable")),\n',
)
replace_once(
    day,
    '        "NEAR_STRICT": 4,\n        "LOW_CONVICTION": 3,\n        "TIMEFRAME_CONFLICT": 2,\n        "POOR_RR": 1,\n        "LIQUIDITY_OR_BORROW_BLOCKED": 0,\n',
    '        "ENTRY_PROVISIONAL": 7,\n        "VALID_SETUP_WAIT": 6,\n        "ENTRY_TOO_EXTENDED": 5,\n        "NEAR_STRICT": 4,\n        "BARRIER_BLOCKED_VALID_SETUP": 3,\n        "RR_BLOCKED_VALID_SETUP": 2,\n        "LOW_CONVICTION": 1,\n        "TIMEFRAME_CONFLICT": 1,\n        "POOR_RR": 0,\n        "LIQUIDITY_OR_BORROW_BLOCKED": -1,\n',
)
replace_once(
    day,
    '    if strategy_version in {IMPULSE_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION}:\n',
    '    if strategy_version in {IMPULSE_DAY_STRATEGY_VERSION, V075_DAY_STRATEGY_VERSION, DAY_STRATEGY_VERSION}:\n',
)
replace_once(
    day,
    '    breakout_event = None\n    if strategy_version == DAY_STRATEGY_VERSION:\n        breakout_event = recent_closed_5m_range_breakout(analysis.bars_5m, side)\n        range_breakout_triggered = breakout_event is not None\n    elif strategy_version == IMPULSE_DAY_STRATEGY_VERSION:\n        # Preserve v0.7.4 historical semantics exactly: crossing bar only.\n        range_breakout_triggered = range_breakout_crossed_now\n        if range_breakout_crossed_now:\n            breakout_event = recent_closed_5m_range_breakout(\n                analysis.bars_5m, side, active_bars=1\n            )\n    else:\n        range_breakout_triggered = False\n',
    '    breakout_event = None\n    persistent_breakout_context = False\n    if strategy_version == DAY_STRATEGY_VERSION:\n        breakout_event = active_structural_breakout_context(analysis.bars_5m, side)\n        persistent_breakout_context = breakout_event is not None\n        range_breakout_triggered = bool(\n            breakout_event is not None and int(breakout_event.get("age_bars", -1)) == 0\n        )\n    elif strategy_version == V075_DAY_STRATEGY_VERSION:\n        # Preserve v0.7.5 historical semantics exactly: breakout bar plus one\n        # immediate follow-through bar while the original boundary holds.\n        breakout_event = recent_closed_5m_range_breakout(analysis.bars_5m, side)\n        range_breakout_triggered = breakout_event is not None\n    elif strategy_version == IMPULSE_DAY_STRATEGY_VERSION:\n        # Preserve v0.7.4 historical semantics exactly: crossing bar only.\n        range_breakout_triggered = range_breakout_crossed_now\n        if range_breakout_crossed_now:\n            breakout_event = recent_closed_5m_range_breakout(\n                analysis.bars_5m, side, active_bars=1\n            )\n    else:\n        range_breakout_triggered = False\n',
)
replace_once(
    day,
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        setup_type = "IMPULSE_BREAKOUT"\n',
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT" or (\n        strategy_version == DAY_STRATEGY_VERSION and persistent_breakout_context\n    ):\n        setup_type = "IMPULSE_BREAKOUT"\n',
)
replace_once(
    day,
    '    recent = analysis.bars_5m[-9:]\n    if side == "long":\n        stop = min(\n            min(bar.low for bar in recent),\n            trigger_price - 1.2 * analysis.atr_5m,\n        )\n        entry_low = trigger_price\n        entry_high = trigger_price + 0.15 * analysis.atr_5m\n        invalidation = (\n            f"Closed 5m candle below {round_to_tick(stop, analysis.instrument.tick_size)} "\n            "or loss of the 15m higher-low structure"\n        )\n    else:\n        stop = max(\n            max(bar.high for bar in recent),\n            trigger_price + 1.2 * analysis.atr_5m,\n        )\n        entry_low = trigger_price - 0.15 * analysis.atr_5m\n        entry_high = trigger_price\n        invalidation = (\n            f"Closed 5m candle above {round_to_tick(stop, analysis.instrument.tick_size)} "\n            "or reclaim of the 15m lower-high structure"\n        )\n\n    if sweep_trigger is not None:\n        trigger_price = float(sweep_trigger["candidate_entry"])\n        stop = float(sweep_trigger["candidate_invalidation"])\n        entry_low = trigger_price\n        entry_high = trigger_price\n        distance_atr = abs(trigger_price - current) / max(analysis.atr_5m, 1e-12)\n        invalidation = (\n            f"Sweep extreme {round_to_tick(stop, analysis.instrument.tick_size)} is invalidated"\n        )\n\n    entry = trigger_price\n',
    '    recent = analysis.bars_5m[-9:]\n    fresh_breakout_geometry = bool(\n        strategy_version == DAY_STRATEGY_VERSION\n        and persistent_breakout_context\n        and sweep_trigger is None\n    )\n    reference_entry = current if fresh_breakout_geometry else trigger_price\n    if side == "long":\n        stop = min(\n            min(bar.low for bar in recent),\n            reference_entry - 1.2 * analysis.atr_5m,\n        )\n        if fresh_breakout_geometry:\n            entry_low, entry_high = fresh_entry_zone(\n                current_price=current, atr_5m=analysis.atr_5m, side=side\n            )\n        else:\n            entry_low = trigger_price\n            entry_high = trigger_price + 0.15 * analysis.atr_5m\n        structure_invalidation = {\n            "timeframe": "15m",\n            "condition": "loss of higher-low structure",\n            "requires_candle_close": True,\n        }\n        invalidation = (\n            f"Hard stop {round_to_tick(stop, analysis.instrument.tick_size)} on intrabar touch/cross; "\n            "independent structural invalidation is loss of the 15m higher-low structure"\n        )\n    else:\n        stop = max(\n            max(bar.high for bar in recent),\n            reference_entry + 1.2 * analysis.atr_5m,\n        )\n        if fresh_breakout_geometry:\n            entry_low, entry_high = fresh_entry_zone(\n                current_price=current, atr_5m=analysis.atr_5m, side=side\n            )\n        else:\n            entry_low = trigger_price - 0.15 * analysis.atr_5m\n            entry_high = trigger_price\n        structure_invalidation = {\n            "timeframe": "15m",\n            "condition": "reclaim/loss of lower-high structure",\n            "requires_candle_close": True,\n        }\n        invalidation = (\n            f"Hard stop {round_to_tick(stop, analysis.instrument.tick_size)} on intrabar touch/cross; "\n            "independent structural invalidation is reclaim/loss of the 15m lower-high structure"\n        )\n\n    if sweep_trigger is not None:\n        trigger_price = float(sweep_trigger["candidate_entry"])\n        reference_entry = trigger_price\n        stop = float(sweep_trigger["candidate_invalidation"])\n        entry_low = trigger_price\n        entry_high = trigger_price\n        distance_atr = abs(trigger_price - current) / max(analysis.atr_5m, 1e-12)\n        invalidation = (\n            f"Hard stop at sweep extreme {round_to_tick(stop, analysis.instrument.tick_size)} "\n            "on intrabar touch/cross"\n        )\n\n    entry = reference_entry\n    hard_stop = hard_stop_contract(stop_price=stop, side=side)\n',
)
replace_once(
    day,
    '    strict_execution = analysis.instrument.tradeable and (\n        side == "long" or analysis.shortable\n    )\n    strict_scores = (\n        score >= DAY_MIN_SETUP_SCORE\n        and analysis.expansion_score >= DAY_MIN_EXPANSION_SCORE\n        and side_direction >= DAY_MIN_DIRECTION_SCORE\n        and analysis.quality_score >= DAY_MIN_QUALITY_SCORE\n        and expected_rr + 1e-9 >= DAY_MIN_RR\n    )\n    strict = strict_execution and strict_scores\n\n    if strict and triggered:\n        state = "TRIGGERED"\n        decision = "TRADE"\n    elif strict and distance_atr <= 0.35:\n        state = "ARMED"\n        decision = "WAIT"\n    elif strict:\n        state = "WATCH"\n        decision = "WAIT"\n    elif score >= 55:\n        state = "WATCH"\n        decision = "NO_TRADE"\n    else:\n        state = "NO_TRADE"\n        decision = "NO_TRADE"\n',
    '    strict_execution = analysis.instrument.tradeable and (\n        side == "long" or analysis.shortable\n    )\n    technical_setup = technical_setup_valid(\n        setup_score=score,\n        expansion_score=analysis.expansion_score,\n        side_direction_score=side_direction,\n        quality_score=analysis.quality_score,\n        minimum_setup_score=DAY_MIN_SETUP_SCORE,\n        minimum_expansion_score=DAY_MIN_EXPANSION_SCORE,\n        minimum_direction_score=DAY_MIN_DIRECTION_SCORE,\n        minimum_quality_score=DAY_MIN_QUALITY_SCORE,\n    )\n    rr_valid = bool(expected_rr + 1e-9 >= DAY_MIN_RR and target_path_valid)\n\n    if strategy_version == DAY_STRATEGY_VERSION:\n        entry_state = classify_entry_state(\n            setup_valid=technical_setup,\n            execution_valid=strict_execution,\n            rr_valid=rr_valid,\n            target_path_valid=target_path_valid,\n            barrier_blocked=barrier_before_tp2 and not target_path_valid,\n            confirmed_trigger=triggered,\n            persistent_breakout_context=persistent_breakout_context,\n            extension_atr=distance_atr,\n            max_provisional_extension_atr=DAY_MAX_PROVISIONAL_EXTENSION_ATR,\n        )\n        setup_state = setup_state_from_validity(technical_setup)\n        strict = strict_execution and technical_setup and rr_valid\n        if entry_state == "ENTRY_CONFIRMED":\n            state = "TRIGGERED"\n            decision = "TRADE"\n        elif entry_state in {"ENTRY_PROVISIONAL", "WAIT_TRIGGER"}:\n            state = "ARMED"\n            decision = "WAIT"\n        elif entry_state in {"ENTRY_TOO_EXTENDED", "BLOCKED_BY_BARRIER", "RR_NOT_READY"}:\n            state = "WATCH"\n            decision = "WAIT"\n        elif entry_state == "EXECUTION_BLOCKED":\n            state = "WATCH" if technical_setup else "NO_TRADE"\n            decision = "NO_TRADE"\n        else:\n            state = "WATCH" if score >= 55 else "NO_TRADE"\n            decision = "NO_TRADE"\n    else:\n        # Historical v0.7.3-v0.7.5 decision semantics remain unchanged.\n        strict_scores = bool(technical_setup and expected_rr + 1e-9 >= DAY_MIN_RR)\n        strict = strict_execution and strict_scores\n        setup_state = "VALID" if strict else "INVALID"\n        entry_state = "ENTRY_CONFIRMED" if strict and triggered else "WAIT_TRIGGER"\n        if strict and triggered:\n            state = "TRIGGERED"\n            decision = "TRADE"\n        elif strict and distance_atr <= 0.35:\n            state = "ARMED"\n            decision = "WAIT"\n        elif strict:\n            state = "WATCH"\n            decision = "WAIT"\n        elif score >= 55:\n            state = "WATCH"\n            decision = "NO_TRADE"\n        else:\n            state = "NO_TRADE"\n            decision = "NO_TRADE"\n',
)
replace_once(
    day,
    '    category = "STRICT" if strict else "WATCH_ONLY"\n    technical_grade = setup_grade(score)\n    displayed_grade = technical_grade if strict else (\n        "WATCH" if score >= 55 else "NO_TRADE"\n    )\n    candidate_watch_bucket = (\n        "STRICT"\n        if strict\n        else watch_bucket(\n            analysis.instrument.tradeable,\n            analysis.shortable,\n            side,\n            conflict_4h,\n            expected_rr,\n            score,\n        )\n    )\n    if category == "WATCH_ONLY":\n        decision = "NO_TRADE"\n',
    '    category = "STRICT" if strict else "WATCH_ONLY"\n    technical_grade = setup_grade(score)\n    displayed_grade = (\n        technical_grade\n        if strict or (strategy_version == DAY_STRATEGY_VERSION and technical_setup)\n        else ("WATCH" if score >= 55 else "NO_TRADE")\n    )\n    if strict:\n        candidate_watch_bucket = "STRICT"\n    elif strategy_version == DAY_STRATEGY_VERSION and technical_setup:\n        candidate_watch_bucket = {\n            "ENTRY_PROVISIONAL": "ENTRY_PROVISIONAL",\n            "WAIT_TRIGGER": "VALID_SETUP_WAIT",\n            "ENTRY_TOO_EXTENDED": "ENTRY_TOO_EXTENDED",\n            "BLOCKED_BY_BARRIER": "BARRIER_BLOCKED_VALID_SETUP",\n            "RR_NOT_READY": "RR_BLOCKED_VALID_SETUP",\n            "EXECUTION_BLOCKED": "LIQUIDITY_OR_BORROW_BLOCKED",\n        }.get(entry_state, "VALID_SETUP_WAIT")\n    else:\n        candidate_watch_bucket = watch_bucket(\n            analysis.instrument.tradeable,\n            analysis.shortable,\n            side,\n            conflict_4h,\n            expected_rr,\n            score,\n        )\n    if category == "WATCH_ONLY" and not (\n        strategy_version == DAY_STRATEGY_VERSION and technical_setup\n    ):\n        decision = "NO_TRADE"\n',
)
replace_once(
    day,
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        trigger_condition = (\n            f"Closed 5m breakout above the anchored prior 12-bar high near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}; remains active "\n            f"through the immediate next closed 5m bar while the boundary holds"\n            if side == "long"\n            else f"Closed 5m breakdown below the anchored prior 12-bar low near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}; remains active "\n            f"through the immediate next closed 5m bar while the boundary holds"\n        )\n    else:\n        trigger_condition = "No closed 5m live trigger confirmed"\n',
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        if strategy_version == DAY_STRATEGY_VERSION:\n            trigger_condition = (\n                f"Closed 5m origin breakout above the prior 12-bar high near "\n                f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}; setup context persists "\n                "without a fixed 5m-bar TTL while every later closed bar holds the original boundary"\n                if side == "long"\n                else f"Closed 5m origin breakdown below the prior 12-bar low near "\n                f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}; setup context persists "\n                "without a fixed 5m-bar TTL while every later closed bar holds the original boundary"\n            )\n        else:\n            trigger_condition = (\n                f"Closed 5m breakout above the anchored prior 12-bar high near "\n                f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}; remains active "\n                f"through the immediate next closed 5m bar while the boundary holds"\n                if side == "long"\n                else f"Closed 5m breakdown below the anchored prior 12-bar low near "\n                f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}; remains active "\n                f"through the immediate next closed 5m bar while the boundary holds"\n            )\n    elif strategy_version == DAY_STRATEGY_VERSION and persistent_breakout_context:\n        trigger_condition = (\n            f"Original 5m breakout boundary {round_to_tick(trigger_price, analysis.instrument.tick_size)} "\n            "remains structurally held; fresh entry geometry is recalculated now. "\n            "Intrabar/provisional acceptance is research-only and does not require waiting for a new full 5m close."\n        )\n    else:\n        trigger_condition = "No live entry trigger confirmed"\n',
)
replace_once(
    day,
    '    weakest = (\n        liquidity_reasons[0]\n        if liquidity_reasons\n        else (\n            "Trigger not confirmed"\n            if not triggered\n            else "The setup has not been prospectively backtested"\n        )\n    )\n',
    '    if strategy_version == DAY_STRATEGY_VERSION and technical_setup:\n        weakest = {\n            "BLOCKED_BY_BARRIER": "Valid setup, but current entry path is blocked by structural barrier",\n            "RR_NOT_READY": "Valid setup, but fresh current entry does not meet net-R requirements",\n            "ENTRY_TOO_EXTENDED": "Valid setup, but current price is too extended; wait for retest/pullback",\n            "ENTRY_PROVISIONAL": "Valid setup with provisional entry only; intrabar acceptance is not yet production-promoted",\n            "WAIT_TRIGGER": "Valid setup; waiting for a usable entry trigger",\n            "EXECUTION_BLOCKED": liquidity_reasons[0] if liquidity_reasons else "Execution side is not valid on Bybit EU",\n        }.get(entry_state, "The setup has not been prospectively validated for this entry state")\n    else:\n        weakest = (\n            liquidity_reasons[0]\n            if liquidity_reasons\n            else (\n                "Trigger not confirmed"\n                if not triggered\n                else "The setup has not been prospectively backtested"\n            )\n        )\n',
)
replace_once(
    day,
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        age_bars = int((breakout_event or {}).get("age_bars", 0))\n        why_now.append(\n            "Latest closed 5m bar crossed the prior 12-bar range boundary"\n            if age_bars == 0\n            else "Prior breakout remains executable on the immediate next closed 5m bar; original boundary is still held"\n        )\n',
    '    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT" or (\n        strategy_version == DAY_STRATEGY_VERSION and persistent_breakout_context\n    ):\n        age_bars = int((breakout_event or {}).get("age_bars", 0))\n        if strategy_version == DAY_STRATEGY_VERSION:\n            why_now.append(\n                "Latest closed 5m bar created the breakout origin"\n                if age_bars == 0\n                else f"Breakout setup remains structurally valid {age_bars} closed 5m bars later; no fixed TTL applies"\n            )\n            why_now.append("Entry/stop/targets/RR are recalculated from the current reference price, not the stale breakout origin")\n        else:\n            why_now.append(\n                "Latest closed 5m bar crossed the prior 12-bar range boundary"\n                if age_bars == 0\n                else "Prior breakout remains executable on the immediate next closed 5m bar; original boundary is still held"\n            )\n',
)
replace_once(
    day,
    '        "setup_type": setup_type,\n        "last_price": round_to_tick(current, analysis.instrument.tick_size),\n',
    '        "setup_type": setup_type,\n        "setup_state": setup_state,\n        "entry_state": entry_state,\n        "execution_valid": strict_execution,\n        "rr_valid": rr_valid,\n        "reference_entry": round_to_tick(entry, analysis.instrument.tick_size),\n        "breakout_context": breakout_event if strategy_version == DAY_STRATEGY_VERSION else None,\n        "hard_stop": {\n            **hard_stop,\n            "price": round_to_tick(float(hard_stop["price"]), analysis.instrument.tick_size),\n        },\n        "structure_invalidation": structure_invalidation,\n        "last_price": round_to_tick(current, analysis.instrument.tick_size),\n',
)
replace_once(
    day,
    '            "requires_close": True,\n',
    '            "requires_close": not (\n                strategy_version == DAY_STRATEGY_VERSION\n                and persistent_breakout_context\n                and not triggered\n            ),\n',
)
replace_once(
    day,
    '            "route": trigger_route,\n            "model": (\n',
    '            "route": (\n                trigger_route\n                if trigger_route != "NONE"\n                else "STRUCTURAL_BREAKOUT_CONTEXT"\n                if strategy_version == DAY_STRATEGY_VERSION and persistent_breakout_context\n                else "NONE"\n            ),\n            "model": (\n',
)
replace_once(
    day,
    '                else "CLOSED_5M_12_BAR_RANGE_BREAKOUT"\n                if trigger_route == "CLOSED_5M_RANGE_BREAKOUT"\n                else "NONE"\n            ),\n',
    '                else "CLOSED_5M_12_BAR_RANGE_BREAKOUT"\n                if trigger_route == "CLOSED_5M_RANGE_BREAKOUT"\n                else "STRUCTURE_PERSISTENT_5M_12_BAR_RANGE_BREAKOUT"\n                if strategy_version == DAY_STRATEGY_VERSION and persistent_breakout_context\n                else "NONE"\n            ),\n',
)
replace_once(
    day,
    '            "validity_bars": (\n                None if breakout_event is None else int(breakout_event["validity_bars"])\n            ),\n',
    '            "validity_bars": (\n                None\n                if breakout_event is None or breakout_event.get("validity_bars") is None\n                else int(breakout_event["validity_bars"])\n            ),\n',
)
replace_once(
    day,
    '            "distance_to_trigger_atr_5m": round(distance_atr, 3),\n',
    '            "distance_to_trigger_atr_5m": round(distance_atr, 3),\n            "setup_valid": technical_setup,\n            "entry_state": entry_state,\n            "reference_entry": round_to_tick(entry, analysis.instrument.tick_size),\n            "breakout_origin_price": round_to_tick(trigger_price, analysis.instrument.tick_size),\n            "entry_geometry_mode": (\n                "FRESH_CURRENT_REFERENCE" if fresh_breakout_geometry else "ORIGIN_TRIGGER_REFERENCE"\n            ),\n',
)
replace_once(
    day,
    '        "bullish_scenario": (\n            "Closed 5m breakout/reclaim holds and 15m structure continues higher."\n        ),\n        "bearish_scenario": (\n            "Closed 5m breakdown/rejection holds and 15m structure continues lower."\n        ),\n',
    '        "bullish_scenario": (\n            "Bullish setup structure remains valid; use fresh entry geometry and do not chase an extended move."\n        ),\n        "bearish_scenario": (\n            "Bearish setup structure remains valid; use fresh entry geometry and do not chase an extended move."\n        ),\n',
)
replace_once(
    day,
    '            "Day-trade v0.7.4 uses 4H/1H as context; live trigger routes are a closed 5m 12-bar range breakout or the closed 5m sweep/reclaim/structure sequence; 15m confirmation applies to the sweep route.",\n',
    '            "Day-trade v0.7.6 separates setup validity from entry readiness: breakout setup context has no fixed 5m-bar TTL while its boundary remains structurally held; closed 5m remains an authoritative confirmation route, not a universal prerequisite for setup existence.",\n',
)
replace_once(
    day,
    '                "trigger_timeframe": "5m closed 12-bar range breakout OR sweep/reclaim/structure confirmation",\n',
    '                "trigger_timeframe": "v0.7.6 setup context persists structurally; executable confirmation remains closed-5m breakout/sweep while intrabar provisional acceptance is research-only",\n',
)
replace_once(
    day,
    '                "barrier_model": "CONFIRMED_15M_PIVOT_EXCLUDING_TRIGGER_WINDOW",\n',
    '                "barrier_model": "CONFIRMED_15M_PIVOT_EXCLUDING_TRIGGER_WINDOW_RECOMPUTED_FROM_FRESH_ENTRY",\n                "hard_stop_activation": "INTRABAR_TOUCH_OR_CROSS_NO_5M_CLOSE_REQUIRED",\n                "setup_entry_state_separated": True,\n',
)
replace_once(
    day,
    '                "Prospective journal records are version-separated; v0.7.4 creates no historical backfill into earlier strategy cohorts.",\n',
    '                "Prospective journal records are version-separated; v0.7.6 creates no historical backfill into v0.7.3-v0.7.5 cohorts.",\n',
)

# API models expose the new state instead of silently dropping it.
replace_once(
    models,
    '    decision: Literal["TRADE", "WAIT", "NO_TRADE"]\n    setup_type: str\n    last_price: float\n',
    '    decision: Literal["TRADE", "WAIT", "NO_TRADE"]\n    setup_type: str\n    setup_state: str | None = None\n    entry_state: str | None = None\n    execution_valid: bool | None = None\n    rr_valid: bool | None = None\n    reference_entry: float | None = None\n    breakout_context: dict[str, Any] | None = None\n    hard_stop: dict[str, Any] | None = None\n    structure_invalidation: dict[str, Any] | None = None\n    last_price: float\n',
)
replace_once(
    models,
    'class DayTradeAuditTrigger(BaseModel):\n    timeframe: str\n    condition: str\n    price: float\n    requires_close: bool\n    volume_confirmation: str\n    triggered: bool\n',
    'class DayTradeAuditTrigger(BaseModel):\n    timeframe: str\n    condition: str\n    price: float\n    requires_close: bool\n    volume_confirmation: str\n    triggered: bool\n    route: str | None = None\n    model: str | None = None\n    event_bar_time: str | None = None\n    age_bars: int | None = None\n    validity_bars: int | None = None\n    boundary_held: bool | None = None\n',
)
replace_once(
    models,
    '    watch_bucket: str | None = None\n    tradeable: bool\n',
    '    watch_bucket: str | None = None\n    setup_state: str | None = None\n    entry_state: str | None = None\n    execution_valid: bool | None = None\n    rr_valid: bool | None = None\n    reference_entry: float | None = None\n    hard_stop: dict[str, Any] | None = None\n    structure_invalidation: dict[str, Any] | None = None\n    tradeable: bool\n',
)

# Current cached/journal cohort becomes v0.7.6. Historical replay remains v0.7.5.
replace_once(repo, 'CURRENT_DAY_STRATEGY_VERSION = "0.7.5"\n', 'CURRENT_DAY_STRATEGY_VERSION = "0.7.6"\n')
replace_once(
    repo,
    '        "NEAR_STRICT": 4,\n        "LOW_CONVICTION": 3,\n        "POOR_RR": 2,\n        "TIMEFRAME_CONFLICT": 1,\n        "LIQUIDITY_OR_BORROW_BLOCKED": 0,\n',
    '        "ENTRY_PROVISIONAL": 8,\n        "VALID_SETUP_WAIT": 7,\n        "ENTRY_TOO_EXTENDED": 6,\n        "NEAR_STRICT": 5,\n        "BARRIER_BLOCKED_VALID_SETUP": 4,\n        "RR_BLOCKED_VALID_SETUP": 3,\n        "LOW_CONVICTION": 2,\n        "POOR_RR": 1,\n        "TIMEFRAME_CONFLICT": 1,\n        "LIQUIDITY_OR_BORROW_BLOCKED": 0,\n',
)
replace_once(
    repo,
    '    execution_ok = candidate.tradeable and (\n        candidate.side == "long" or candidate.shortable\n    ) and not candidate.timeframe_conflict\n',
    '    execution_ok = candidate.tradeable and (\n        candidate.side == "long" or candidate.shortable\n    )\n',
)
replace_once(
    repo,
    '    return (\n        candidate.category == "WATCH_ONLY"\n        and candidate.tradeable\n        and (candidate.side == "long" or candidate.shortable)\n        and not candidate.timeframe_conflict\n        and candidate.side_direction_score > 0\n        and bool(metrics.get("target_path_valid", False))\n        and candidate.expected_rr >= 1.0\n        and candidate.setup_score >= 55.0\n    )\n',
    '    setup_valid = bool(candidate.setup_state == "VALID" or metrics.get("setup_valid", False))\n    if setup_valid:\n        return (\n            candidate.category == "WATCH_ONLY"\n            and candidate.tradeable\n            and (candidate.side == "long" or candidate.shortable)\n            and candidate.side_direction_score > 0\n            and candidate.setup_score >= 55.0\n        )\n    return (\n        candidate.category == "WATCH_ONLY"\n        and candidate.tradeable\n        and (candidate.side == "long" or candidate.shortable)\n        and candidate.side_direction_score > 0\n        and bool(metrics.get("target_path_valid", False))\n        and candidate.expected_rr >= 1.0\n        and candidate.setup_score >= 55.0\n    )\n',
)
replace_once(
    repo,
    '            "Top watchlists exclude timeframe-conflict, blocked, invalid-target-path and expected-RR<1.0 items.",\n',
    '            "v0.7.6 keeps technically VALID setups visible even when current entry RR/barrier is blocked; 4H timeframe conflict is context-only and cannot hide a day setup.",\n',
)

replace_once(main, '    version="0.7.5",\n', '    version="0.7.6",\n')
replace_once(
    main,
    '    description="Read-only cached USDC swing/day scanner; day-trade strategy v0.7.5 with context-only derivatives Flow feature v0.7.2.2.",\n',
    '    description="Read-only cached USDC swing/day scanner; day-trade strategy v0.7.6 with separated setup/entry state and context-only derivatives Flow feature v0.7.2.2.",\n',
)
# Backtest service itself intentionally remains v0.7.5; make the call explicit so
# changing the live default cannot contaminate its historical cohort.
replace_once(
    backtest,
    '            candidate = build_day_candidate(analysis, side, _dt_from_ms(evaluation_time_ms))\n',
    '            candidate = build_day_candidate(\n                analysis,\n                side,\n                _dt_from_ms(evaluation_time_ms),\n                strategy_version=STRATEGY_VERSION,\n            )\n',
)
replace_once(journal, 'STRATEGY_VERSION = "0.7.5"\n', 'STRATEGY_VERSION = "0.7.6"\n')
replace_all_checked(flow_context, 'v0.7.5', 'v0.7.6', minimum=1)

print("v0.7.6 backend patch applied")
