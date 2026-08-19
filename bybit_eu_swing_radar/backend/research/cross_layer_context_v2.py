"""Label-free Cross-Layer Context v2.

V2 preserves the v1 forward cohort and adds sourced sector rotation plus BTC/ETH
on-chain context. It remains descriptive only: no composite score, eligibility
mutation, execution proof, or trading instruction is emitted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from research.research_data_quality import (
    CONTRACT_VERSION as DATA_QUALITY_CONTRACT_VERSION,
    aggregate_contract_results,
    contract_manifest,
    evaluate_source_record,
    source_max_age_seconds,
)

SPEC_VERSION = "cross-layer-context-shadow-v2"
MAX_SYMBOLS = 24
LAYER_SOURCES = (
    "market_regime",
    "derivatives_positioning",
    "event_tokenomics",
    "btc_macro_cycle_etf",
    "relative_strength",
    "sector_rotation",
    "btc_onchain",
    "eth_onchain",
)
LAYER_MAX_AGE_SECONDS = {
    name: source_max_age_seconds(name)
    for name in LAYER_SOURCES
}


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "composite_score_emitted": False,
        "execution_proof": False,
        "max_symbols": MAX_SYMBOLS,
        "data_quality_contract_version": DATA_QUALITY_CONTRACT_VERSION,
        "data_quality_contract": contract_manifest(),
        "layer_max_age_seconds": dict(LAYER_MAX_AGE_SECONDS),
        "layers": list(LAYER_MAX_AGE_SECONDS),
        "new_vs_v1": ["sector_rotation", "btc_onchain", "eth_onchain"],
        "versioning": {
            "v1_preserved": True,
            "reason": "do not mix existing forward freezes with the expanded context schema",
        },
        "microstructure_join_policy": "SIGNAL_TIME_PRE_SIGNAL_ONLY_NOT_SNAPSHOT_JOINED",
        "principles": [
            "only snapshots captured at or before the context timestamp may be joined",
            "each layer keeps its own timestamp, age and provenance",
            "missing or stale layers remain explicit and never become zero/neutral evidence",
            "no cross-layer composite score, eligibility gate or execution instruction is emitted",
            "microstructure remains signal-time only because it requires strict pre-signal alignment",
        ],
    }


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _symbol_map(payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = (payload or {}).get("symbols")
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        for symbol, item in raw.items():
            if not isinstance(item, Mapping):
                continue
            normalized = str(symbol).upper()
            if normalized.endswith("USDC"):
                row = dict(item)
                row.setdefault("symbol", normalized)
                result[normalized] = row
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            symbol = str(item.get("symbol") or "").upper()
            if symbol.endswith("USDC"):
                result[symbol] = dict(item)
    return result


def _compact_regime(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    metrics = row.get("metrics") or {}
    return {
        "regime": row.get("regime"),
        "direction": row.get("direction"),
        "atr_ratio": metrics.get("atr_ratio"),
        "bb_width_percentile": metrics.get("bb_width_percentile"),
        "trend_efficiency_ratio": metrics.get("trend_efficiency_ratio"),
    }


def _compact_derivatives(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    liq = row.get("liquidations") or {}
    return {
        "positioning_state": row.get("positioning_state"),
        "funding_crowding": row.get("funding_crowding"),
        "funding_rate_decimal": row.get("funding_rate_decimal"),
        "oi_change_15m_pct": row.get("oi_change_15m_pct"),
        "oi_change_1h_pct": row.get("oi_change_1h_pct"),
        "oi_change_4h_pct": row.get("oi_change_4h_pct"),
        "liquidation_state": liq.get("state"),
        "liquidation_skew": liq.get("skew"),
        "regime_interaction": row.get("regime_interaction"),
        "coverage": dict(row.get("coverage") or {}),
        "execution_proof": False,
    }


def _compact_relative_strength(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "rank": row.get("rank"),
        "state": row.get("state"),
        "rotation_context": row.get("rotation_context"),
        "rs_score": row.get("rs_score"),
        "return_7d_pct": row.get("return_7d_pct"),
        "return_30d_pct": row.get("return_30d_pct"),
        "return_90d_pct": row.get("return_90d_pct"),
        "relative_to_btc_7d_pct": row.get("relative_to_btc_7d_pct"),
        "relative_to_btc_30d_pct": row.get("relative_to_btc_30d_pct"),
        "relative_to_btc_90d_pct": row.get("relative_to_btc_90d_pct"),
    }


def _compact_sector(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    resolution = row.get("taxonomy_resolution") or {}
    tags = [
        {"id": item.get("id"), "name": item.get("name")}
        for item in (row.get("functional_tags") or [])
        if isinstance(item, Mapping)
    ]
    return {
        "functional_tags": tags,
        "taxonomy_resolution_status": resolution.get("status"),
        "provider_coin_id": resolution.get("provider_coin_id"),
        "ambiguous_resolution": bool(resolution.get("ambiguous")),
    }


def _compact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "title": event.get("title"),
        "event_at": event.get("event_at"),
        "date_precision": event.get("date_precision"),
        "is_estimated": event.get("is_estimated"),
        "severity": event.get("severity"),
        "window": event.get("window"),
        "scope": event.get("scope"),
    }


def _compact_onchain(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    coin_metrics = payload.get("coin_metrics") or {}
    metrics = coin_metrics.get("metrics") or {}
    compact_metrics: dict[str, Any] = {}
    if isinstance(metrics, Mapping):
        for name, raw in metrics.items():
            if not isinstance(raw, Mapping):
                continue
            compact_metrics[str(name)] = {
                "available": raw.get("available"),
                "latest": raw.get("latest"),
                "latest_date": raw.get("latest_date"),
                "mean_7d": raw.get("mean_7d"),
                "mean_30d": raw.get("mean_30d"),
                "latest_vs_30d_mean_pct": raw.get("latest_vs_30d_mean_pct"),
                "change_30d_pct": raw.get("change_30d_pct"),
            }
    return {
        "data_quality": payload.get("data_quality"),
        "metrics": compact_metrics,
        "source_status": payload.get("source_status"),
    }


def _layer_meta(
    name: str,
    record: Mapping[str, Any] | None,
    captured_at: datetime,
) -> dict[str, Any]:
    # Preserve the existing temporal status keys while adding a common
    # completeness/lineage/severity contract. Evaluation never mutates or
    # filters source payloads and never grants production eligibility.
    return evaluate_source_record(name, record, observed_at=captured_at)


def build_context_snapshot(
    records: Mapping[str, Mapping[str, Any] | None],
    *,
    captured_at: datetime | None = None,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    now = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    layer_meta = {
        name: _layer_meta(name, records.get(name), now)
        for name in LAYER_MAX_AGE_SECONDS
    }
    data_quality_contract = aggregate_contract_results(layer_meta)
    payloads: dict[str, dict[str, Any]] = {}
    for name in LAYER_MAX_AGE_SECONDS:
        if layer_meta[name]["status"] in {"FUTURE_REJECTED", "INVALID_TIMESTAMP"}:
            payloads[name] = {}
            continue
        record = records.get(name)
        payload = (record or {}).get("payload") if record else None
        payloads[name] = dict(payload) if isinstance(payload, Mapping) else {}

    regime_map = _symbol_map(payloads["market_regime"])
    derivatives_map = _symbol_map(payloads["derivatives_positioning"])
    rs_map = _symbol_map(payloads["relative_strength"])
    sector_map = _symbol_map(payloads["sector_rotation"])

    events = [
        dict(item)
        for item in (payloads["event_tokenomics"].get("events") or [])
        if isinstance(item, Mapping)
    ]
    event_tracked = {
        str(symbol).upper()
        for symbol in (payloads["event_tokenomics"].get("tracked_symbols") or [])
        if str(symbol).upper().endswith("USDC")
    }
    rs_order = [
        str(item.get("symbol") or "").upper()
        for item in sorted(
            rs_map.values(),
            key=lambda item: (
                int(item.get("rank") or 999999),
                str(item.get("symbol") or ""),
            ),
        )
    ]
    union = set(regime_map) | set(derivatives_map) | set(rs_map) | set(sector_map) | event_tracked
    ordered = list(dict.fromkeys([*rs_order, *sorted(union)]))[:MAX_SYMBOLS]

    btc_onchain = _compact_onchain(payloads["btc_onchain"])
    eth_onchain = _compact_onchain(payloads["eth_onchain"])
    rows: list[dict[str, Any]] = []
    for symbol in ordered:
        symbol_events = [
            _compact_event(event)
            for event in events
            if symbol in {str(item).upper() for item in (event.get("symbols") or [])}
        ][:5]
        onchain = btc_onchain if symbol == "BTCUSDC" else eth_onchain if symbol == "ETHUSDC" else None
        rows.append(
            {
                "symbol": symbol,
                "market_regime": _compact_regime(regime_map.get(symbol)),
                "derivatives_positioning": _compact_derivatives(derivatives_map.get(symbol)),
                "relative_strength": _compact_relative_strength(rs_map.get(symbol)),
                "sector_rotation": _compact_sector(sector_map.get(symbol)),
                "onchain": onchain,
                "events": symbol_events,
                "coverage": {
                    "market_regime": symbol in regime_map,
                    "derivatives_positioning": symbol in derivatives_map,
                    "relative_strength": symbol in rs_map,
                    "sector_rotation": symbol in sector_map,
                    "onchain": onchain is not None,
                    "symbol_events": len(symbol_events),
                },
            }
        )

    global_events = [
        _compact_event(event)
        for event in events
        if not (event.get("symbols") or [])
    ][:10]
    fresh_count = sum(meta["status"] == "FRESH" for meta in layer_meta.values())
    if fresh_count == len(layer_meta):
        quality = "COMPLETE"
    elif fresh_count > 0:
        quality = "PARTIAL"
    else:
        quality = "MISSING"

    regime_payload = payloads["market_regime"]
    derivatives_payload = payloads["derivatives_positioning"]
    rs_payload = payloads["relative_strength"]
    sector_payload = payloads["sector_rotation"]
    macro_payload = payloads["btc_macro_cycle_etf"]
    return {
        "spec_version": SPEC_VERSION,
        "captured_at": now.isoformat(),
        "source_commit_sha": source_commit_sha,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "composite_score_emitted": False,
        "execution_proof": False,
        "data_quality": quality,
        "data_quality_contract": data_quality_contract,
        "layer_fresh_count": fresh_count,
        "layer_count": len(layer_meta),
        "layers": layer_meta,
        "symbol_count": len(rows),
        "symbols": rows,
        "global_context": {
            "market_regime": {
                "global_regime": regime_payload.get("global_regime"),
                "dominant_direction": regime_payload.get("dominant_direction"),
                "btc_anchor": regime_payload.get("btc_anchor"),
            },
            "relative_strength": {
                "breadth": rs_payload.get("breadth"),
                "leaders": rs_payload.get("leaders"),
                "laggards": rs_payload.get("laggards"),
                "state_counts": rs_payload.get("state_counts"),
                "rotation_counts": rs_payload.get("rotation_counts"),
            },
            "sector_rotation": {
                "taxonomy_provider": sector_payload.get("taxonomy_provider"),
                "resolution_coverage_pct": sector_payload.get("resolution_coverage_pct"),
                "taxonomy_coverage_pct": sector_payload.get("taxonomy_coverage_pct"),
                "rotation_ranked_group_count": sector_payload.get("rotation_ranked_group_count"),
                "top_relative_strength_groups": sector_payload.get("top_relative_strength_groups"),
            },
            "derivatives_positioning": {
                "positioning_counts": derivatives_payload.get("positioning_counts"),
                "crowding_counts": derivatives_payload.get("crowding_counts"),
                "interaction_counts": derivatives_payload.get("interaction_counts"),
                "coverage": derivatives_payload.get("coverage"),
            },
            "event_tokenomics": {
                "global_events": global_events,
                "event_count": payloads["event_tokenomics"].get("event_count"),
                "severity_counts": payloads["event_tokenomics"].get("severity_counts"),
                "window_counts": payloads["event_tokenomics"].get("window_counts"),
            },
            "btc_macro_cycle_etf": {
                "btc_price": macro_payload.get("btc_price"),
                "cycle": macro_payload.get("cycle"),
                "etf": macro_payload.get("etf"),
                "macro": macro_payload.get("macro"),
                "source_status": macro_payload.get("source_status") or (macro_payload.get("coverage") or {}).get("source_status"),
            },
            "btc_onchain": btc_onchain,
            "eth_onchain": eth_onchain,
        },
        "microstructure": {
            "joined": False,
            "policy": "SIGNAL_TIME_PRE_SIGNAL_ONLY_NOT_SNAPSHOT_JOINED",
        },
        "spec": spec(),
    }
