"""Preregistered sourced sector-taxonomy / rotation shadow v1."""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

SPEC_VERSION = "sector-rotation-shadow-v1"
PROVIDER = "CoinPaprika"
TAG_TYPE = "functional"
MIN_ROTATION_GROUP_SIZE = 2


def spec() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "universe": "current Bybit EU relative-strength USDC spot universe",
        "taxonomy": {
            "provider": PROVIDER,
            "source_type": "provider_functional_tags",
            "multi_label": True,
            "hand_labels_allowed": False,
            "tag_type": TAG_TYPE,
            "symbol_resolution": "active_ticker_symbol_then_best_positive_rank",
            "ambiguity_preserved": True,
        },
        "rotation": {
            "input": "relative-strength-shadow-v1 symbol metrics",
            "minimum_group_size": MIN_ROTATION_GROUP_SIZE,
            "singletons_reported_but_not_rotation_ranked": True,
            "metrics": [
                "mean_rs_score",
                "median_rs_score",
                "mean_7d_return_pct",
                "mean_30d_return_pct",
                "mean_90d_return_pct",
                "mean_rotation_delta_7d_vs_30d",
                "accelerating_count",
                "decelerating_count",
            ],
        },
        "forbidden": [
            "bull_bear_score",
            "directional_trade_signal",
            "eligibility_gate",
            "execution_proof",
            "outcome_labels",
            "threshold_search",
            "automatic_strategy_promotion",
        ],
    }


def _rank_value(value: Any) -> int:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return 10**9
    return rank if rank > 0 else 10**9


def resolve_symbols(
    symbols: list[str], tickers: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Resolve Bybit base symbols to provider IDs while preserving collisions."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for item in tickers:
        if not isinstance(item, dict):
            continue
        provider_symbol = str(item.get("symbol") or "").upper().strip()
        provider_id = str(item.get("id") or "").strip()
        if not provider_symbol or not provider_id:
            continue
        by_symbol.setdefault(provider_symbol, []).append(
            {
                "id": provider_id,
                "name": item.get("name"),
                "symbol": provider_symbol,
                "rank": item.get("rank"),
            }
        )

    output: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        normalized = symbol.upper().strip()
        base = normalized[:-4] if normalized.endswith("USDC") else normalized
        candidates = sorted(
            by_symbol.get(base, []),
            key=lambda item: (_rank_value(item.get("rank")), str(item.get("id"))),
        )
        if not candidates:
            output[normalized] = {
                "symbol": normalized,
                "base": base,
                "status": "UNRESOLVED",
                "provider_coin_id": None,
                "provider_coin_name": None,
                "provider_rank": None,
                "candidate_count": 0,
                "ambiguous": False,
                "candidates": [],
            }
            continue
        selected = candidates[0]
        output[normalized] = {
            "symbol": normalized,
            "base": base,
            "status": "RESOLVED_UNIQUE" if len(candidates) == 1 else "RESOLVED_BY_BEST_RANK",
            "provider_coin_id": selected["id"],
            "provider_coin_name": selected.get("name"),
            "provider_rank": selected.get("rank"),
            "candidate_count": len(candidates),
            "ambiguous": len(candidates) > 1,
            "candidates": candidates[:5],
        }
    return output


def build_functional_tag_index(
    tags: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, Any]]]:
    """Build provider-coin -> functional-tag membership without hand mapping."""
    coin_tags: dict[str, list[dict[str, str]]] = {}
    tag_meta: dict[str, dict[str, Any]] = {}
    for item in tags:
        if not isinstance(item, dict) or str(item.get("type") or "").lower() != TAG_TYPE:
            continue
        tag_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not tag_id or not name:
            continue
        members = item.get("coins")
        if not isinstance(members, list):
            members = []
        tag_meta[tag_id] = {
            "id": tag_id,
            "name": name,
            "type": TAG_TYPE,
            "provider_coin_counter": item.get("coin_counter"),
        }
        for coin_id in members:
            normalized_coin_id = str(coin_id or "").strip()
            if not normalized_coin_id:
                continue
            coin_tags.setdefault(normalized_coin_id, []).append(
                {"id": tag_id, "name": name}
            )
    for values in coin_tags.values():
        values.sort(key=lambda item: (item["name"], item["id"]))
    return coin_tags, tag_meta


def _fmean(rows: list[dict[str, Any]], field: str) -> float:
    return statistics.fmean(float(row[field]) for row in rows)


def _group_payload(
    *, tag_id: str, tag_name: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: str(row["symbol"]))
    rs_scores = [float(row["rs_score"]) for row in ordered]
    return {
        "tag_id": tag_id,
        "tag_name": tag_name,
        "tag_type": TAG_TYPE,
        "constituent_count": len(ordered),
        "symbols": [str(row["symbol"]) for row in ordered],
        "ambiguous_resolution_count": sum(
            1 for row in ordered if bool((row.get("taxonomy_resolution") or {}).get("ambiguous"))
        ),
        "mean_rs_score": statistics.fmean(rs_scores),
        "median_rs_score": statistics.median(rs_scores),
        "mean_7d_return_pct": _fmean(ordered, "return_7d_pct"),
        "mean_30d_return_pct": _fmean(ordered, "return_30d_pct"),
        "mean_90d_return_pct": _fmean(ordered, "return_90d_pct"),
        "mean_rotation_delta_7d_vs_30d": _fmean(
            ordered, "rotation_delta_7d_vs_30d"
        ),
        "accelerating_count": sum(
            1 for row in ordered if row.get("rotation_context") == "ACCELERATING"
        ),
        "decelerating_count": sum(
            1 for row in ordered if row.get("rotation_context") == "DECELERATING"
        ),
        "leader_or_outperformer_count": sum(
            1 for row in ordered if row.get("state") in {"LEADER", "OUTPERFORMER"}
        ),
        "rotation_rank_eligible": len(ordered) >= MIN_ROTATION_GROUP_SIZE,
    }


def build_snapshot(
    *,
    relative_strength_snapshot: dict[str, Any],
    resolutions: dict[str, dict[str, Any]],
    coin_tags: dict[str, list[dict[str, str]]],
    tag_meta: dict[str, dict[str, Any]],
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    if relative_strength_snapshot.get("research_only") is not True:
        raise ValueError("relative-strength dependency is not research-only")
    symbol_rows = relative_strength_snapshot.get("symbols")
    if not isinstance(symbol_rows, list) or not symbol_rows:
        raise ValueError("relative-strength dependency has no symbol rows")

    enriched: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    resolved_count = 0
    mapped_count = 0
    ambiguous_count = 0
    for raw in symbol_rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        symbol = str(row.get("symbol") or "").upper()
        resolution = dict(resolutions.get(symbol) or {
            "symbol": symbol,
            "status": "UNRESOLVED",
            "provider_coin_id": None,
            "ambiguous": False,
        })
        provider_coin_id = resolution.get("provider_coin_id")
        tags = list(coin_tags.get(str(provider_coin_id), [])) if provider_coin_id else []
        if provider_coin_id:
            resolved_count += 1
        if resolution.get("ambiguous"):
            ambiguous_count += 1
        if tags:
            mapped_count += 1
        row["taxonomy_resolution"] = resolution
        row["functional_tags"] = tags
        enriched.append(row)
        for tag in tags:
            groups.setdefault(tag["id"], []).append(row)

    sector_groups = [
        _group_payload(
            tag_id=tag_id,
            tag_name=str((tag_meta.get(tag_id) or {}).get("name") or tag_id),
            rows=rows,
        )
        for tag_id, rows in groups.items()
    ]
    sector_groups.sort(
        key=lambda item: (
            -int(item["constituent_count"]),
            -float(item["mean_rs_score"]),
            str(item["tag_name"]),
        )
    )
    ranked = sorted(
        [item for item in sector_groups if item["rotation_rank_eligible"]],
        key=lambda item: (-float(item["mean_rs_score"]), str(item["tag_name"])),
    )
    for rank, item in enumerate(ranked, start=1):
        item["relative_strength_rank"] = rank
    rank_by_tag = {item["tag_id"]: item["relative_strength_rank"] for item in ranked}
    for item in sector_groups:
        item["relative_strength_rank"] = rank_by_tag.get(item["tag_id"])

    now = captured_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    universe_size = len(enriched)
    return {
        "spec_version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "relative_strength_spec_version": (
            (relative_strength_snapshot.get("spec") or {}).get("version")
        ),
        "relative_strength_captured_at": relative_strength_snapshot.get("captured_at"),
        "taxonomy_provider": PROVIDER,
        "taxonomy_tag_type": TAG_TYPE,
        "taxonomy_multi_label": True,
        "universe_size": universe_size,
        "resolved_symbol_count": resolved_count,
        "taxonomy_mapped_symbol_count": mapped_count,
        "ambiguous_resolution_count": ambiguous_count,
        "resolution_coverage_pct": resolved_count / universe_size * 100.0 if universe_size else 0.0,
        "taxonomy_coverage_pct": mapped_count / universe_size * 100.0 if universe_size else 0.0,
        "sector_group_count": len(sector_groups),
        "rotation_ranked_group_count": len(ranked),
        "sector_rotation_available": bool(ranked),
        "symbols": enriched,
        "sector_groups": sector_groups,
        "top_relative_strength_groups": [
            {
                "tag_id": item["tag_id"],
                "tag_name": item["tag_name"],
                "constituent_count": item["constituent_count"],
                "mean_rs_score": item["mean_rs_score"],
                "mean_rotation_delta_7d_vs_30d": item[
                    "mean_rotation_delta_7d_vs_30d"
                ],
                "relative_strength_rank": item["relative_strength_rank"],
            }
            for item in ranked[:10]
        ],
        "spec": spec(),
    }
