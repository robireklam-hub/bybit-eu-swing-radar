from __future__ import annotations

from research import swing_liquidity_shadow as shadow
from research.swing_liquidity_shadow import (
    book_cost_metrics,
    dedupe_candidates,
    persist_snapshot,
    spread_tier,
    turnover_tier,
)


def test_preregistered_turnover_tiers_are_fixed_at_boundaries():
    assert turnover_tier(24_999.0) == "LT_25K"
    assert turnover_tier(25_000.0) == "25K_50K"
    assert turnover_tier(50_000.0) == "50K_100K"
    assert turnover_tier(99_999.0) == "50K_100K"
    assert turnover_tier(100_000.0) == "100K_250K"
    assert turnover_tier(1_000_000.0) == "GE_1M"


def test_preregistered_spread_tiers_keep_wide_names_separate():
    assert spread_tier(10.0) == "LE_10"
    assert spread_tier(20.0) == "10_20"
    assert spread_tier(35.0) == "20_35"
    assert spread_tier(50.0) == "35_50"
    assert spread_tier(50.001) == "GT_50"


def test_book_cost_uses_depth_and_fails_closed_when_notional_cannot_fill():
    book = {
        "b": [["99", "10"], ["98", "10"]],
        "a": [["101", "5"], ["102", "10"]],
    }
    small = book_cost_metrics(book, 100.0)
    assert small["complete_fill"] is True
    assert small["immediate_round_trip_cost_bps"] > 0

    too_large = book_cost_metrics(book, 100_000.0)
    assert too_large["complete_fill"] is False


def test_scan_dedup_preserves_liquidity_blocked_candidate_when_no_executable_setup():
    scan = {
        "longs": [],
        "shorts": [],
        "extended_watchlist": [
            {
                "symbol": "ALTUSDC",
                "side": "long",
                "setup_score": 70,
                "expansion_score": 60,
                "direction_score": 40,
                "quality_score": 65,
                "shortable": False,
                "metrics": {"turnover_24h_usdc": 75_000, "spread_bps": 18, "tradeable": False},
            }
        ],
        "liquidity_blocked": [
            {
                "symbol": "ALTUSDC",
                "side": "long",
                "setup_score": 70,
                "expansion_score": 60,
                "direction_score": 40,
                "quality_score": 65,
                "shortable": False,
                "metrics": {
                    "turnover_24h_usdc": 75_000,
                    "spread_bps": 18,
                    "tradeable": False,
                    "execution_status": "LIQUIDITY_BLOCKED",
                    "liquidity_reasons": ["24h USDC turnover below minimum"],
                },
            }
        ],
    }
    items = dedupe_candidates(scan)
    assert len(items) == 1
    item = items[0]
    assert item["source_section"] == "liquidity_blocked"
    assert item["turnover_tier"] == "50K_100K"
    assert item["spread_tier"] == "10_20"
    assert item["current_tradeable"] is False


def test_persist_snapshot_requires_research_only_fail_closed_response(monkeypatch):
    snapshot = {"candidate_count": 2}

    def good_post(url, payload, headers, timeout=30.0):
        assert url.endswith("/v1/research/swing-liquidity/forward-snapshot")
        assert headers["X-Radar-Key"] == "secret"
        assert payload is snapshot
        return {
            "research_only": True,
            "live_strategy_mutated": False,
            "promotion_allowed": False,
            "candidate_count": 2,
            "inserted": True,
        }

    monkeypatch.setattr(shadow, "_post_json", good_post)
    assert persist_snapshot("https://example.test", "secret", snapshot)["inserted"] is True

    monkeypatch.setattr(
        shadow,
        "_post_json",
        lambda *args, **kwargs: {
            "research_only": True,
            "live_strategy_mutated": False,
            "promotion_allowed": True,
            "candidate_count": 2,
        },
    )
    try:
        persist_snapshot("https://example.test", "secret", snapshot)
    except RuntimeError as exc:
        assert "unexpectedly allows promotion" in str(exc)
    else:
        raise AssertionError("promotion_allowed=true must fail closed")
