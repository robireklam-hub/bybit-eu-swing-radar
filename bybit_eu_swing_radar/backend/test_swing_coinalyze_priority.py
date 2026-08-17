from __future__ import annotations

from types import SimpleNamespace

import pytest

import worker
from app.swing_priority import select_compact_priority_sections


def setup(
    symbol: str,
    side: str,
    score: float,
    *,
    expansion: float = 60.0,
    direction: float | None = None,
    quality: float = 65.0,
    shortable: bool = True,
) -> dict:
    if direction is None:
        direction = 40.0 if side == "long" else -40.0
    return {
        "symbol": symbol,
        "side": side,
        "setup_score": score,
        "expansion_score": expansion,
        "direction_score": direction,
        "quality_score": quality,
        "expected_rr": 2.5,
        "shortable": shortable,
    }


def analysis(symbol: str, score: float) -> SimpleNamespace:
    # Choose components so the generic selector score equals the supplied score.
    instrument = SimpleNamespace(symbol=symbol)
    return SimpleNamespace(
        instrument=instrument,
        expansion_score=score,
        direction_score=score,
        quality_score=score,
        shortable=False,
        max_borrowing_amount=0.0,
    )


def test_liquidity_blocked_watch_is_prioritized_over_higher_generic_score(monkeypatch):
    monkeypatch.setattr(worker, "DISCOVERY_SYMBOLS", set())
    monkeypatch.setattr(worker, "COINALYZE_ENRICH_LIMIT", 9)

    # LOWWATCH is intentionally a liquidity-blocked/watch candidate whose core
    # score is below unrelated analyses. getTopCandidates semantics still surface
    # it, so it must consume the upstream context budget first.
    compact = select_compact_priority_sections(
        longs=[setup("STRICTUSDC", "long", 82.0)],
        shorts=[],
        extended_watchlist=[],
        liquidity_blocked=[setup("LOWWATCHUSDC", "long", 61.0, expansion=40.0)],
        limit=3,
    )
    assert compact["priority_symbols"] == ["STRICTUSDC", "LOWWATCHUSDC"]

    analyses = [
        analysis("HIGHGENERICUSDC", 95.0),
        analysis("ANOTHERUSDC", 90.0),
        analysis("STRICTUSDC", 50.0),
        analysis("LOWWATCHUSDC", 20.0),
    ]
    before = [
        (item.instrument.symbol, item.expansion_score, item.direction_score, item.quality_score, item.shortable)
        for item in analyses
    ]
    targets = worker.select_coinalyze_targets(analyses, compact["priority_symbols"])

    assert [item.instrument.symbol for item in targets[:2]] == [
        "STRICTUSDC",
        "LOWWATCHUSDC",
    ]
    assert [
        (item.instrument.symbol, item.expansion_score, item.direction_score, item.quality_score, item.shortable)
        for item in analyses
    ] == before


def test_compact_priority_applies_shortability_before_short_selection():
    blocked_short = setup(
        "BLOCKEDUSDC",
        "short",
        90.0,
        shortable=False,
    )
    valid_short = setup("VALIDUSDC", "short", 80.0, shortable=True)
    compact = select_compact_priority_sections(
        longs=[],
        shorts=[blocked_short, valid_short],
        extended_watchlist=[blocked_short],
        liquidity_blocked=[],
        limit=3,
    )
    # The non-borrowable short is never strict. If it is present in the actual
    # compact watch output it remains priority context, but execution eligibility
    # is unchanged.
    assert [item["symbol"] for item in compact["strict_shorts"]] == ["VALIDUSDC"]
    assert "BLOCKEDUSDC" in compact["priority_symbols"]


def test_priority_over_budget_fails_transparently(monkeypatch):
    monkeypatch.setattr(worker, "COINALYZE_ENRICH_LIMIT", 9)
    analyses = [analysis(f"S{i}USDC", 50.0 + i) for i in range(10)]
    with pytest.raises(RuntimeError, match="exceeds safe rate budget"):
        worker.select_coinalyze_targets(
            analyses,
            [item.instrument.symbol for item in analyses],
        )


def test_default_compact_priority_maximum_can_exceed_budget_and_is_not_hidden():
    longs = [setup(f"L{i}USDC", "long", 90.0 - i) for i in range(3)]
    shorts = [setup(f"S{i}USDC", "short", 90.0 - i) for i in range(3)]
    watch_longs = [setup(f"WL{i}USDC", "long", 69.0 - i) for i in range(3)]
    watch_shorts = [setup(f"WS{i}USDC", "short", 69.0 - i) for i in range(3)]
    compact = select_compact_priority_sections(
        longs,
        shorts,
        [*watch_longs, *watch_shorts],
        [],
        limit=3,
    )
    assert len(compact["priority_symbols"]) == 12
