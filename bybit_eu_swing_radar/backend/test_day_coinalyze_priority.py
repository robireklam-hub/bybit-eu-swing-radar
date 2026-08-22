from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import day_worker
import worker
from day_worker import FastResult, select_day_coinalyze_targets, select_deep_universe


ROOT = Path(__file__).resolve().parent.parent
CORE = ("BTCUSDC", "ETHUSDC", "SOLUSDC")


def _analysis(symbol: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        instrument=SimpleNamespace(
            symbol=symbol,
            base=symbol.removesuffix("USDC"),
        ),
        expansion_score=score,
        direction_score=score,
        quality_score=score,
    )


def _fast(symbol: str, score: float) -> FastResult:
    return FastResult(
        instrument=SimpleNamespace(
            symbol=symbol,
            turnover_24h=score * 1_000_000,
        ),
        bars_5m=[],
        bars_15m=[],
        fast_score=score,
        fast_side="long",
        return_15m_pct=0.0,
        return_1h_pct=0.0,
        volume_ratio_5m=1.0,
        volume_ratio_15m=1.0,
        breakout_5m=False,
    )


def test_day_coinalyze_budget_always_reserves_available_core_symbols(monkeypatch) -> None:
    monkeypatch.setattr(worker, "COINALYZE_ENRICH_LIMIT", 9)
    analyses = [
        *(_analysis(f"ALT{index}USDC", 100.0 - index) for index in range(12)),
        _analysis("BTCUSDC", 5.0),
        _analysis("ETHUSDC", 4.0),
        _analysis("SOLUSDC", 3.0),
    ]

    targets, priority_symbols = select_day_coinalyze_targets(analyses)
    target_symbols = [item.instrument.symbol for item in targets]

    assert priority_symbols == list(CORE)
    assert target_symbols[:3] == list(CORE)
    assert len(target_symbols) == 9
    assert set(CORE).issubset(target_symbols)


def test_day_deep_universe_keeps_core_symbols_despite_mandatory_override(
    monkeypatch,
) -> None:
    monkeypatch.setattr(day_worker, "DAY_DEEP_LIMIT", 15)
    monkeypatch.setattr(
        day_worker,
        "DAY_MANDATORY_SYMBOLS",
        {f"ALT{index}USDC" for index in range(20)},
    )
    results = [
        *(_fast(f"ALT{index}USDC", 100.0 - index) for index in range(20)),
        _fast("BTCUSDC", 3.0),
        _fast("ETHUSDC", 2.0),
        _fast("SOLUSDC", 1.0),
    ]

    selected = select_deep_universe(results)
    symbols = [item.instrument.symbol for item in selected]

    assert len(symbols) == 15
    assert len(symbols) == len(set(symbols))
    assert symbols[:3] == list(CORE)


def test_agent_contract_requires_setup_then_flow_fallback() -> None:
    instructions = (ROOT / "agent" / "AGENT_INSTRUCTIONS_HU.md").read_text(
        encoding="utf-8"
    )
    openapi = (ROOT / "action" / "openapi.yaml").read_text(encoding="utf-8")

    assert "getDayTradeSetup(symbol)" in instructions
    assert "getDayTradeFlowContext(symbol)" in instructions
    assert "üres, hiányos vagy degraded" in instructions
    assert "mindkét forrás ténylegesen üres vagy degraded" in instructions
    assert "soha nem strict gate" in instructions
    assert "use this after getDayTradeSetup" in openapi
