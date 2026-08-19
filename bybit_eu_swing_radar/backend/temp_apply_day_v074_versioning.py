"""Temporary patch helper for PR #253. Self-deletes before merge."""
from __future__ import annotations

from pathlib import Path
import subprocess

BRANCH = "fix/btc-impulse-breakout-coverage"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def replace_at_least(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count < 1:
        raise RuntimeError(f"{label}: expected >=1 match, found 0")
    return text.replace(old, new)


def patch(path: Path, transforms) -> None:
    text = path.read_text(encoding="utf-8")
    for transform in transforms:
        text = transform(text)
    path.write_text(text, encoding="utf-8")


def ro(old: str, new: str, label: str):
    return lambda text: replace_once(text, old, new, label)


def ra(old: str, new: str, label: str):
    return lambda text: replace_at_least(text, old, new, label)


def run(*args: str, cwd: Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True)


def main() -> None:
    backend = Path(__file__).resolve().parent
    root = backend.parent
    repo = root.parent

    # Work on the real PR head, never the synthetic pull-request merge ref.
    run("git", "fetch", "origin", BRANCH, cwd=repo)
    run("git", "checkout", "-B", BRANCH, f"origin/{BRANCH}", cwd=repo)

    day = backend / "day_worker.py"
    resolver = '''\n\ndef resolve_day_trigger_policy(\n    strategy_version: str,\n    *,\n    range_breakout_triggered: bool,\n    sweep_triggered: bool,\n) -> tuple[bool, str]:\n    """Resolve versioned day-trigger semantics without rewriting history.\n\n    v0.7.3 remains sweep-only for historical reproducibility. v0.7.4 adds a\n    closed-5m 12-bar range-breakout route while preserving the full sweep route.\n    """\n    if strategy_version == LEGACY_DAY_STRATEGY_VERSION:\n        return (\n            sweep_triggered,\n            "LIQUIDITY_SWEEP_RECLAIM" if sweep_triggered else "NONE",\n        )\n    if strategy_version == DAY_STRATEGY_VERSION:\n        if sweep_triggered:\n            return True, "LIQUIDITY_SWEEP_RECLAIM"\n        if range_breakout_triggered:\n            return True, "CLOSED_5M_RANGE_BREAKOUT"\n        return False, "NONE"\n    raise ValueError(f"Unsupported day strategy version: {strategy_version}")\n'''
    patch(day, [
        ro("day-trade worker v0.7.3", "day-trade worker v0.7.4", "day doc version"),
        ro('DAY_STRATEGY_VERSION = "0.7.3"', 'LEGACY_DAY_STRATEGY_VERSION = "0.7.3"\nDAY_STRATEGY_VERSION = "0.7.4"', "day version constant"),
        ro('PROSPECTIVE_FUNNEL_SPEC_VERSION = "v073-prospective-funnel-v1"', 'PROSPECTIVE_FUNNEL_SPEC_VERSION = "v073-prospective-funnel-v1"\nPROSPECTIVE_FUNNEL_STRATEGY_VERSION = "0.7.3"', "funnel pinned version"),
        ro('\ndef build_day_candidate(\n', resolver + '\n\ndef build_day_candidate(\n', "trigger resolver insert"),
        ro('''def build_day_candidate(\n    analysis: DayAnalysis,\n    side: str,\n    now: datetime,\n) -> dict[str, Any] | None:\n''', '''def build_day_candidate(\n    analysis: DayAnalysis,\n    side: str,\n    now: datetime,\n    strategy_version: str = DAY_STRATEGY_VERSION,\n) -> dict[str, Any] | None:\n''', "candidate strategy arg"),
        ro('''    sweep_triggered = sweep_trigger is not None\n    triggered = range_breakout_triggered or sweep_triggered\n    trigger_route = (\n        "LIQUIDITY_SWEEP_RECLAIM"\n        if sweep_triggered\n        else "CLOSED_5M_RANGE_BREAKOUT"\n        if range_breakout_triggered\n        else "NONE"\n    )\n''', '''    sweep_triggered = sweep_trigger is not None\n    triggered, trigger_route = resolve_day_trigger_policy(\n        strategy_version,\n        range_breakout_triggered=range_breakout_triggered,\n        sweep_triggered=sweep_triggered,\n    )\n''', "versioned trigger resolution"),
        ro('''    if sweep_triggered:\n        setup_type = "LIQUIDITY_SWEEP_RECLAIM"\n    elif range_breakout_triggered:\n        setup_type = "IMPULSE_BREAKOUT"\n''', '''    if trigger_route == "LIQUIDITY_SWEEP_RECLAIM":\n        setup_type = "LIQUIDITY_SWEEP_RECLAIM"\n    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        setup_type = "IMPULSE_BREAKOUT"\n''', "versioned setup type"),
        ro('''    if sweep_triggered:\n        trigger_condition = (\n''', '''    if trigger_route == "LIQUIDITY_SWEEP_RECLAIM":\n        trigger_condition = (\n''', "route trigger condition"),
        ro('''        )\n    else:\n        trigger_condition = (\n            f"Closed 5m candle crosses above the prior 12-bar high near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n            if side == "long"\n            else f"Closed 5m candle crosses below the prior 12-bar low near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n        )\n\n    derivatives = analysis.derivatives or {}\n''', '''        )\n    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        trigger_condition = (\n            f"Closed 5m candle crosses above the prior 12-bar high near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n            if side == "long"\n            else f"Closed 5m candle crosses below the prior 12-bar low near "\n            f"{round_to_tick(trigger_price, analysis.instrument.tick_size)}"\n        )\n    else:\n        trigger_condition = "No closed 5m live trigger confirmed"\n\n    derivatives = analysis.derivatives or {}\n''', "none trigger condition"),
        ro('''    if sweep_triggered:\n        why_now.append("Latest closed 5m bar completed the sweep/reclaim/structure confirmation sequence")\n    elif range_breakout_triggered:\n        why_now.append("Latest closed 5m bar crossed the prior 12-bar range boundary")\n''', '''    if trigger_route == "LIQUIDITY_SWEEP_RECLAIM":\n        why_now.append("Latest closed 5m bar completed the sweep/reclaim/structure confirmation sequence")\n    elif trigger_route == "CLOSED_5M_RANGE_BREAKOUT":\n        why_now.append("Latest closed 5m bar crossed the prior 12-bar range boundary")\n''', "why route"),
        ro('''    if conflict_4h:\n        why_now.append("4H structure conflicts with the side but is context-only in v0.7.3")\n''', '''    if conflict_4h:\n        why_now.append(f"4H structure conflicts with the side but is context-only in v{strategy_version}")\n''', "dynamic conflict note"),
        ro('''        "strategy_mode": "DAY_TRADE",\n        "side": side,\n''', '''        "strategy_mode": "DAY_TRADE",\n        "strategy_version": strategy_version,\n        "side": side,\n''', "candidate version payload"),
        ro('''                f">={DAY_TRIGGER_VOLUME_RATIO:.1f}x prior 20-bar mean volume on confirmation"\n                if sweep_triggered\n                else "No standalone volume hard gate; existing STRICT expansion/quality gates still apply"\n''', '''                f">={DAY_TRIGGER_VOLUME_RATIO:.1f}x prior 20-bar mean volume on confirmation"\n                if trigger_route == "LIQUIDITY_SWEEP_RECLAIM"\n                else "No standalone volume hard gate; existing STRICT expansion/quality gates still apply"\n                if trigger_route == "CLOSED_5M_RANGE_BREAKOUT"\n                else "Not triggered"\n''', "volume route"),
        ro('''            "model": (\n                "LIQUIDITY_SWEEP_RECLAIM_5M_STRUCTURE_15M_CONFIRMATION"\n                if sweep_triggered\n                else "CLOSED_5M_12_BAR_RANGE_BREAKOUT"\n            ),\n''', '''            "model": (\n                "LIQUIDITY_SWEEP_RECLAIM_5M_STRUCTURE_15M_CONFIRMATION"\n                if trigger_route == "LIQUIDITY_SWEEP_RECLAIM"\n                else "CLOSED_5M_12_BAR_RANGE_BREAKOUT"\n                if trigger_route == "CLOSED_5M_RANGE_BREAKOUT"\n                else "NONE"\n            ),\n''', "trigger model none"),
        ro('''            "Model RR uses a configurable cost assumption, not the account's exact fee tier",\n            "Only backtests and journal records matching strategy v0.7.3 are comparable with this live engine",\n''', '''            "Model RR uses a configurable cost assumption, not the account's exact fee tier",\n            f"Only backtests and journal records matching strategy v{strategy_version} are comparable with this candidate",\n''', "dynamic risk version"),
        ro('''            "Day-trade v0.7.3 uses 4H/1H as context; live trigger routes are a closed 5m 12-bar range breakout or the closed 5m sweep/reclaim/structure sequence; 15m confirmation applies to the sweep route.",\n''', '''            "Day-trade v0.7.4 uses 4H/1H as context; live trigger routes are a closed 5m 12-bar range breakout or the closed 5m sweep/reclaim/structure sequence; 15m confirmation applies to the sweep route.",\n''', "regime note version"),
        ro('''                "spec_version": PROSPECTIVE_FUNNEL_SPEC_VERSION,\n                "strategy_version": DAY_STRATEGY_VERSION,\n''', '''                "spec_version": PROSPECTIVE_FUNNEL_SPEC_VERSION,\n                "strategy_version": PROSPECTIVE_FUNNEL_STRATEGY_VERSION,\n                "live_strategy_version": DAY_STRATEGY_VERSION,\n''', "funnel version pin"),
        ro('''                "Prospective journal records are version-separated; v0.7.3 creates no historical backfill.",\n''', '''                "Prospective journal records are version-separated; v0.7.4 creates no historical backfill into earlier strategy cohorts.",\n''', "scan note version"),
    ])

    # Current journal/replay/API surfaces move to a fresh v0.7.4 cohort.
    patch(backend / "journal_core.py", [
        ro('STRATEGY_VERSION = "0.7.3"', 'STRATEGY_VERSION = "0.7.4"', "journal version"),
    ])
    patch(backend / "backtest.py", [
        ra("v0.7.3", "v0.7.4", "backtest prose version"),
        ro('STRATEGY_VERSION = "0.7.3"', 'STRATEGY_VERSION = "0.7.4"', "backtest constant"),
        ro('"v073-90d-netrr-structural-barrier"', '"v074-90d-netrr-structural-barrier"', "backtest job"),
    ])
    patch(backend / "app" / "repository.py", [
        ro('CURRENT_DAY_STRATEGY_VERSION = "0.7.3"', 'CURRENT_DAY_STRATEGY_VERSION = "0.7.4"', "repository version"),
    ])
    patch(backend / "flow_context.py", [
        ra("v0.7.3 STRICT gates", "v0.7.4 STRICT gates", "flow prose"),
        ra('"strategy_version": "0.7.3"', '"strategy_version": "0.7.4"', "flow payload version"),
    ])
    patch(backend / "app" / "main.py", [
        ro('version="0.7.3"', 'version="0.7.4"', "FastAPI version"),
        ra("day-trade strategy v0.7.3", "day-trade strategy v0.7.4", "API description"),
        ra("v0.7.3 backtest batch", "v0.7.4 backtest batch", "API backtest log"),
    ])

    # Historical v0.7.3 semantics remain executable through the explicit policy argument.
    sweep_test = backend / "test_day_sweep_v073.py"
    patch(sweep_test, [
        ro('''    candidate = day_worker.build_day_candidate(\n        analysis,\n        "long",\n        datetime.now(timezone.utc),\n    )\n    assert candidate is not None\n    assert candidate["category"] == "STRICT"\n    assert candidate["trigger"]["triggered"] is False\n''', '''    candidate = day_worker.build_day_candidate(\n        analysis,\n        "long",\n        datetime.now(timezone.utc),\n        strategy_version="0.7.3",\n    )\n    assert candidate is not None\n    assert candidate["category"] == "STRICT"\n    assert candidate["trigger"]["triggered"] is False\n''', "legacy explicit policy test"),
        ro('''def test_v073_strategy_versions_are_separated():\n    assert day_worker.DAY_STRATEGY_VERSION == "0.7.3"\n    assert journal.STRATEGY_VERSION == "0.7.3"\n    assert backtest.STRATEGY_VERSION == "0.7.3"\n''', '''def test_v073_history_is_frozen_while_current_strategy_is_v074():\n    assert day_worker.LEGACY_DAY_STRATEGY_VERSION == "0.7.3"\n    assert day_worker.DAY_STRATEGY_VERSION == "0.7.4"\n    assert journal.STRATEGY_VERSION == "0.7.4"\n    assert backtest.STRATEGY_VERSION == "0.7.4"\n    assert day_worker.resolve_day_trigger_policy(\n        "0.7.3", range_breakout_triggered=True, sweep_triggered=False\n    ) == (False, "NONE")\n''', "version test"),
    ])

    impulse_test = backend / "test_day_impulse_breakout_trigger.py"
    text = impulse_test.read_text(encoding="utf-8")
    if "test_v073_policy_keeps_direct_breakout_non_executable" not in text:
        text += '''\n\ndef test_v073_policy_keeps_direct_breakout_non_executable(monkeypatch):\n    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)\n    c = build_day_candidate(\n        _analysis(),\n        "long",\n        datetime(2026, 8, 19, tzinfo=timezone.utc),\n        strategy_version="0.7.3",\n    )\n    assert c is not None\n    assert c["strategy_version"] == "0.7.3"\n    assert c["trigger"]["triggered"] is False\n    assert c["trigger"]["route"] == "NONE"\n    assert c["trigger"]["model"] == "NONE"\n    assert c["decision"] != "TRADE"\n'''
        impulse_test.write_text(text, encoding="utf-8")

    # Release/API contract tests now validate v0.7.4 as current while keeping v0.7.3 historical artifacts.
    patch(backend / "test_v073_version_isolation.py", [
        ra("v073", "v074", "version isolation names"),
        ra('"0.7.3"', '"0.7.4"', "version isolation current values"),
        ra("v0.7.3 STRICT gates", "v0.7.4 STRICT gates", "version isolation flow note"),
    ])
    patch(backend / "test_v073_contract_alignment.py", [
        ra("parent_strategy_is_073", "parent_strategy_is_074", "contract test name"),
        ra('== "0.7.3"', '== "0.7.4"', "flow parent version assertion"),
        ra("v0.7.3 STRICT gates", "v0.7.4 STRICT gates", "flow note assertion"),
        ra("declares_073", "declares_074", "release test name"),
        ra('version="0.7.3"', 'version="0.7.4"', "release version assertion"),
        ra("day-trade strategy v0.7.3", "day-trade strategy v0.7.4", "release description assertion"),
        ra("describes_v073_day_trigger", "describes_v074_day_trigger", "openapi test name"),
        ra("version: 0.7.3", "version: 0.7.4", "openapi version assertion"),
        ra("closed 5m sweep/reclaim/structure confirmation", "closed 5m 12-bar range breakout OR sweep/reclaim/structure confirmation", "openapi trigger assertion"),
        ra("does not change v0.7.3 STRICT gates", "does not change v0.7.4 STRICT gates", "openapi flow assertion"),
        ra("day_v073_rules", "day_v074_rules", "agent test name"),
        ra("## Day-trade v0.7.3 külön szabályok", "## Day-trade v0.7.4 külön szabályok", "agent heading assertion"),
        ra("day_v073_annex", "day_v074_annex", "spec test name"),
        ra("## 13. Day-trade v0.7.3 kiegészítés", "## 14. Day-trade v0.7.4 kiegészítés", "spec heading assertion"),
        ra("Journal és historical replay `strategy_version=0.7.3`", "Journal és historical replay `strategy_version=0.7.4`", "spec version assertion"),
    ])

    openapi = root / "action" / "openapi.yaml"
    patch(openapi, [
        ro("  version: 0.7.3", "  version: 0.7.4", "openapi info version"),
        ra("day-trade strategy v0.7.3", "day-trade strategy v0.7.4", "openapi description version"),
        ra("closed 5m sweep/reclaim/structure confirmation", "closed 5m 12-bar range breakout OR sweep/reclaim/structure confirmation", "openapi trigger text"),
        ra("does not change v0.7.3 STRICT gates", "does not change v0.7.4 STRICT gates", "openapi flow text"),
    ])

    agent = root / "agent" / "AGENT_INSTRUCTIONS_HU.md"
    patch(agent, [
        ro("## Day-trade v0.7.3 külön szabályok", "## Day-trade v0.7.4 külön szabályok", "agent day heading"),
        ro("- Day-trade-ben az authoritative TRADE trigger: lezárt 5m liquidity sweep -> reclaim -> 5m local structure shift, legalább a konfigurált relatív volumen-megerősítéssel, és a confirmation időpontjáig teljesen lezárt 15m struktúra nem lehet ellenirányú.", "- Day-trade-ben két authoritative, lezárt-5m triggerút van: (1) 12-bar range boundary direct impulse breakout/breakdown; vagy (2) liquidity sweep -> reclaim -> 5m local structure shift, a konfigurált relatív volumen-megerősítéssel és nem ellenirányú lezárt 15m struktúrával. Mindkettő csak a meglévő STRICT score/RR/execution gate-ek teljesülése mellett adhat TRADE döntést.", "agent trigger rule"),
        ra("v0.7.3-ban context-only", "v0.7.4-ben context-only", "agent context version"),
        ro("a day-trade stratégia verziója v0.7.3", "a day-trade stratégia verziója v0.7.4", "agent version line"),
    ])

    spec = root / "BACKEND_SPEC_HU.md"
    spec_text = spec.read_text(encoding="utf-8")
    annex = '''\n\n## 14. Day-trade v0.7.4 kiegészítés\nA v0.7.3 történeti szemantikája változatlanul sweep-only és reprodukálható marad. A v0.7.4 új, külön kohorsz; a score-, RR-, target-path- és execution gate-eket nem lazítja.\n\n- Stratégia verzió: `0.7.4`. A derivatives Flow feature verziója változatlanul `0.7.2.2`.\n- Authoritative live trigger két lezárt-5m útvonal egyike: (1) az előző 12 lezárt 5m gyertya range-boundaryjének közvetlen close-breakout/breakdownja (`CLOSED_5M_RANGE_BREAKOUT`); vagy (2) a v0.7.3-ból megtartott liquidity sweep -> reclaim -> 5m structure shift -> nem ellenirányú lezárt 15m struktúra -> volume confirmation (`LIQUIDITY_SWEEP_RECLAIM`).\n- Ha mindkét út egyszerre érvényes, a sweep útvonal prioritást kap, mert annak entry/invalidation geometriája specifikusabb.\n- A direct impulse breakout önmagában nem kerülheti meg a STRICT gate-eket: setup >= 70, expansion >= 55, side-direction >= 35, quality >= 65, költség utáni RR >= 1.8, valid target path, likviditás/spread, valamint execution gate kötelező.\n- Long végrehajtás továbbra is kizárólag Bybit EU USDC spot. Short kizárólag igazolt Bybit EU USDC spot-margin borrowability mellett.\n- OI/funding/Flow továbbra is context-only és nem hard gate.\n- Journal és historical replay `strategy_version=0.7.4`; a v0.7.3 sorok és backtest jobok változatlanul elkülönítve maradnak.\n- A v0.7.3 prospective sweep-funnel kutatási kohorsz változatlanul `v073-prospective-funnel-v1`; nem kerül visszamenőleg átértelmezésre v0.7.4-ként.\n'''
    if "## 14. Day-trade v0.7.4 kiegészítés" not in spec_text:
        spec.write_text(spec_text.rstrip() + annex + "\n", encoding="utf-8")

    # Restore normal CI permissions and remove this helper before committing.
    run("git", "fetch", "origin", "main", cwd=repo)
    run("git", "checkout", "origin/main", "--", ".github/workflows/backend-tests.yml", cwd=repo)
    helper = backend / "temp_apply_day_v074_versioning.py"
    helper.unlink()
    pycache = backend / "__pycache__"
    if pycache.exists():
        for child in pycache.glob("temp_apply_day_v074_versioning*.pyc"):
            child.unlink()

    run("git", "diff", "--check", cwd=repo)
    run("git", "config", "user.name", "github-actions[bot]", cwd=repo)
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=repo)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-m", "Version day impulse breakout policy as v0.7.4", cwd=repo)
    run("git", "push", "origin", f"HEAD:{BRANCH}", cwd=repo)


if __name__ == "__main__":
    main()
