from datetime import datetime, timezone

from research.sector_rotation_shadow import (
    MIN_ROTATION_GROUP_SIZE,
    build_functional_tag_index,
    build_snapshot,
    resolve_symbols,
    spec,
)


def _relative_snapshot():
    return {
        "research_only": True,
        "captured_at": "2026-08-18T00:41:00+00:00",
        "spec": {"version": "relative-strength-shadow-v1"},
        "symbols": [
            {
                "symbol": "BTCUSDC",
                "rs_score": 40.0,
                "return_7d_pct": 1.0,
                "return_30d_pct": 2.0,
                "return_90d_pct": 3.0,
                "rotation_delta_7d_vs_30d": 0.0,
                "rotation_context": "STABLE",
                "state": "NEUTRAL",
            },
            {
                "symbol": "ETHUSDC",
                "rs_score": 80.0,
                "return_7d_pct": 5.0,
                "return_30d_pct": 8.0,
                "return_90d_pct": 12.0,
                "rotation_delta_7d_vs_30d": 25.0,
                "rotation_context": "ACCELERATING",
                "state": "LEADER",
            },
            {
                "symbol": "SOLUSDC",
                "rs_score": 70.0,
                "return_7d_pct": 4.0,
                "return_30d_pct": 6.0,
                "return_90d_pct": 10.0,
                "rotation_delta_7d_vs_30d": -22.0,
                "rotation_context": "DECELERATING",
                "state": "OUTPERFORMER",
            },
        ],
    }


def test_spec_uses_sourced_multilabel_taxonomy_without_hand_labels():
    payload = spec()
    assert payload["research_only"] is True
    assert payload["label_free"] is True
    assert payload["context_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["execution_proof"] is False
    assert payload["promotion_allowed"] is False
    assert payload["taxonomy"]["provider"] == "CoinPaprika"
    assert payload["taxonomy"]["source_type"] == "provider_functional_tags"
    assert payload["taxonomy"]["multi_label"] is True
    assert payload["taxonomy"]["hand_labels_allowed"] is False
    assert payload["rotation"]["minimum_group_size"] == MIN_ROTATION_GROUP_SIZE
    assert "bull_bear_score" in payload["forbidden"]


def test_symbol_resolution_preserves_collision_and_uses_best_positive_rank():
    tickers = [
        {"id": "btc-bitcoin", "name": "Bitcoin", "symbol": "BTC", "rank": 1},
        {"id": "eth-ethereum", "name": "Ethereum", "symbol": "ETH", "rank": 2},
        {"id": "eth-other", "name": "Other ETH", "symbol": "ETH", "rank": 900},
    ]
    result = resolve_symbols(["BTCUSDC", "ETHUSDC", "NOPEUSDC"], tickers)
    assert result["BTCUSDC"]["status"] == "RESOLVED_UNIQUE"
    assert result["BTCUSDC"]["provider_coin_id"] == "btc-bitcoin"
    assert result["ETHUSDC"]["status"] == "RESOLVED_BY_BEST_RANK"
    assert result["ETHUSDC"]["provider_coin_id"] == "eth-ethereum"
    assert result["ETHUSDC"]["candidate_count"] == 2
    assert result["ETHUSDC"]["ambiguous"] is True
    assert result["NOPEUSDC"]["status"] == "UNRESOLVED"


def test_functional_tag_index_rejects_technical_tags_and_keeps_overlap():
    coin_tags, meta = build_functional_tag_index(
        [
            {
                "id": "platform",
                "name": "Platform",
                "type": "functional",
                "coin_counter": 3,
                "coins": ["eth-ethereum", "sol-solana"],
            },
            {
                "id": "defi",
                "name": "DeFi",
                "type": "functional",
                "coin_counter": 2,
                "coins": ["eth-ethereum", "sol-solana"],
            },
            {
                "id": "proof-of-stake",
                "name": "Proof of Stake",
                "type": "technical",
                "coin_counter": 2,
                "coins": ["eth-ethereum", "sol-solana"],
            },
        ]
    )
    assert set(meta) == {"platform", "defi"}
    assert {item["id"] for item in coin_tags["eth-ethereum"]} == {"platform", "defi"}
    assert "proof-of-stake" not in meta


def test_snapshot_builds_overlapping_rotation_groups_and_coverage():
    resolutions = {
        "BTCUSDC": {
            "symbol": "BTCUSDC",
            "base": "BTC",
            "status": "RESOLVED_UNIQUE",
            "provider_coin_id": "btc-bitcoin",
            "provider_coin_name": "Bitcoin",
            "provider_rank": 1,
            "candidate_count": 1,
            "ambiguous": False,
            "candidates": [],
        },
        "ETHUSDC": {
            "symbol": "ETHUSDC",
            "base": "ETH",
            "status": "RESOLVED_UNIQUE",
            "provider_coin_id": "eth-ethereum",
            "provider_coin_name": "Ethereum",
            "provider_rank": 2,
            "candidate_count": 1,
            "ambiguous": False,
            "candidates": [],
        },
        "SOLUSDC": {
            "symbol": "SOLUSDC",
            "base": "SOL",
            "status": "RESOLVED_BY_BEST_RANK",
            "provider_coin_id": "sol-solana",
            "provider_coin_name": "Solana",
            "provider_rank": 6,
            "candidate_count": 2,
            "ambiguous": True,
            "candidates": [],
        },
    }
    coin_tags = {
        "btc-bitcoin": [{"id": "currency", "name": "Currency"}],
        "eth-ethereum": [
            {"id": "platform", "name": "Platform"},
            {"id": "smart-contracts", "name": "Smart Contracts"},
        ],
        "sol-solana": [
            {"id": "platform", "name": "Platform"},
            {"id": "smart-contracts", "name": "Smart Contracts"},
        ],
    }
    tag_meta = {
        "currency": {"id": "currency", "name": "Currency", "type": "functional"},
        "platform": {"id": "platform", "name": "Platform", "type": "functional"},
        "smart-contracts": {"id": "smart-contracts", "name": "Smart Contracts", "type": "functional"},
    }
    snapshot = build_snapshot(
        relative_strength_snapshot=_relative_snapshot(),
        resolutions=resolutions,
        coin_tags=coin_tags,
        tag_meta=tag_meta,
        captured_at=datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc),
    )
    assert snapshot["research_only"] is True
    assert snapshot["promotion_allowed"] is False
    assert snapshot["universe_size"] == 3
    assert snapshot["resolved_symbol_count"] == 3
    assert snapshot["taxonomy_mapped_symbol_count"] == 3
    assert snapshot["ambiguous_resolution_count"] == 1
    assert snapshot["sector_rotation_available"] is True
    assert snapshot["rotation_ranked_group_count"] == 2
    by_tag = {item["tag_id"]: item for item in snapshot["sector_groups"]}
    assert by_tag["currency"]["rotation_rank_eligible"] is False
    assert by_tag["platform"]["constituent_count"] == 2
    assert by_tag["platform"]["mean_rs_score"] == 75.0
    assert by_tag["platform"]["accelerating_count"] == 1
    assert by_tag["platform"]["decelerating_count"] == 1
    assert by_tag["platform"]["ambiguous_resolution_count"] == 1
    assert by_tag["platform"]["relative_strength_rank"] is not None
