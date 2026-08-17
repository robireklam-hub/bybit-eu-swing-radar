#!/usr/bin/env python3
"""Prospective, label-blind swing liquidity snapshot collector.

Research only. Reads the production swing scan and contemporaneous Bybit EU L50
spot order books, then writes a JSON artifact. It never changes swing scores,
eligibility, tradeability, shortability, orders, positions, or production state.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

NOTIONALS_USDC = (100.0, 250.0, 500.0, 1000.0)
TURNOVER_TIERS = (
    (25_000.0, "LT_25K"),
    (50_000.0, "25K_50K"),
    (100_000.0, "50K_100K"),
    (250_000.0, "100K_250K"),
    (1_000_000.0, "250K_1M"),
    (math.inf, "GE_1M"),
)
SPREAD_TIERS = (
    (10.0, "LE_10"),
    (20.0, "10_20"),
    (35.0, "20_35"),
    (50.0, "35_50"),
    (math.inf, "GT_50"),
)
SECTIONS = ("longs", "shorts", "extended_watchlist", "liquidity_blocked")


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def turnover_tier(turnover: float | None) -> str:
    if turnover is None or turnover < 0:
        return "UNKNOWN"
    for upper, label in TURNOVER_TIERS:
        if turnover < upper:
            return label
    return "GE_1M"


def spread_tier(spread_bps: float | None) -> str:
    if spread_bps is None or spread_bps < 0:
        return "UNKNOWN"
    for upper, label in SPREAD_TIERS:
        if spread_bps <= upper:
            return label
    return "GT_50"


def _levels(raw: Any) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    if not isinstance(raw, list):
        return result
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price = _as_float(row[0])
        qty = _as_float(row[1])
        if price and qty and price > 0 and qty > 0:
            result.append((price, qty))
    return result


def _buy_vwap_for_quote(asks: list[tuple[float, float]], quote_notional: float) -> tuple[float | None, bool]:
    remaining = quote_notional
    base_bought = 0.0
    quote_spent = 0.0
    for price, qty in asks:
        level_quote = price * qty
        take_quote = min(remaining, level_quote)
        base_bought += take_quote / price
        quote_spent += take_quote
        remaining -= take_quote
        if remaining <= 1e-9:
            break
    if remaining > 1e-6 or base_bought <= 0:
        return None, False
    return quote_spent / base_bought, True


def _sell_vwap_for_base(bids: list[tuple[float, float]], base_qty: float) -> tuple[float | None, bool]:
    remaining = base_qty
    quote_received = 0.0
    base_sold = 0.0
    for price, qty in bids:
        take_base = min(remaining, qty)
        quote_received += take_base * price
        base_sold += take_base
        remaining -= take_base
        if remaining <= 1e-12:
            break
    if remaining > 1e-9 or base_sold <= 0:
        return None, False
    return quote_received / base_sold, True


def book_cost_metrics(orderbook_result: dict[str, Any], quote_notional: float) -> dict[str, Any]:
    bids = _levels(orderbook_result.get("b"))
    asks = _levels(orderbook_result.get("a"))
    if not bids or not asks or quote_notional <= 0:
        return {"notional_usdc": quote_notional, "complete_fill": False}
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid <= 0 or best_ask <= best_bid:
        return {"notional_usdc": quote_notional, "complete_fill": False}
    mid = (best_bid + best_ask) / 2.0
    buy_vwap, buy_ok = _buy_vwap_for_quote(asks, quote_notional)
    base_qty = quote_notional / mid
    sell_vwap, sell_ok = _sell_vwap_for_base(bids, base_qty)
    if not buy_ok or not sell_ok or buy_vwap is None or sell_vwap is None:
        return {
            "notional_usdc": quote_notional,
            "complete_fill": False,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid,
        }
    buy_cost_bps = max(0.0, (buy_vwap / mid - 1.0) * 10_000.0)
    sell_cost_bps = max(0.0, (1.0 - sell_vwap / mid) * 10_000.0)
    return {
        "notional_usdc": quote_notional,
        "complete_fill": True,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "buy_vwap": buy_vwap,
        "sell_vwap": sell_vwap,
        "buy_cost_bps": round(buy_cost_bps, 6),
        "sell_cost_bps": round(sell_cost_bps, 6),
        "immediate_round_trip_cost_bps": round(buy_cost_bps + sell_cost_bps, 6),
    }


def compact_candidate(candidate: dict[str, Any], source_section: str) -> dict[str, Any] | None:
    symbol = str(candidate.get("symbol") or "").upper()
    side = str(candidate.get("side") or "").lower()
    if not symbol.endswith("USDC") or side not in {"long", "short"}:
        return None
    metrics = candidate.get("metrics") or {}
    turnover = _as_float(metrics.get("turnover_24h_usdc"))
    spread = _as_float(metrics.get("spread_bps"))
    return {
        "symbol": symbol,
        "side": side,
        "source_section": source_section,
        "state": candidate.get("state"),
        "grade": candidate.get("grade"),
        "data_as_of": candidate.get("data_as_of"),
        "last_price": _as_float(candidate.get("last_price")),
        "shortable": bool(candidate.get("shortable", False)),
        "execution_modes": candidate.get("execution_modes") or [],
        "setup_score": _as_float(candidate.get("setup_score")),
        "expansion_score": _as_float(candidate.get("expansion_score")),
        "direction_score": _as_float(candidate.get("direction_score")),
        "quality_score": _as_float(candidate.get("quality_score")),
        "trigger": candidate.get("trigger"),
        "entry_zone": candidate.get("entry_zone"),
        "stop": _as_float(candidate.get("stop")),
        "targets": candidate.get("targets") or [],
        "expected_rr": _as_float(candidate.get("expected_rr")),
        "turnover_24h_usdc": turnover,
        "turnover_tier": turnover_tier(turnover),
        "spread_bps": spread,
        "spread_tier": spread_tier(spread),
        "current_tradeable": bool(metrics.get("tradeable", source_section in {"longs", "shorts"})),
        "execution_status": metrics.get("execution_status"),
        "liquidity_reasons": metrics.get("liquidity_reasons") or [],
        "derivatives": metrics.get("derivatives") or {},
        "derivatives_context_only": True,
    }


def dedupe_candidates(scan: dict[str, Any]) -> list[dict[str, Any]]:
    priority = {"longs": 4, "shorts": 4, "liquidity_blocked": 3, "extended_watchlist": 2}
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for section in SECTIONS:
        rows = scan.get(section) or []
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            candidate = compact_candidate(raw, section)
            if candidate is None:
                continue
            key = (candidate["symbol"], candidate["side"])
            existing = chosen.get(key)
            if existing is None or priority[section] > priority.get(existing["source_section"], 0):
                chosen[key] = candidate
    return sorted(chosen.values(), key=lambda x: (x["symbol"], x["side"]))


def _get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    request = Request(url, headers=headers or {"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("JSON response is not an object")
    return payload


def collect_snapshot(base_url: str, api_key: str, bybit_base_url: str = "https://api.bybit.eu") -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc).isoformat()
    scan = _get_json(
        f"{base_url.rstrip('/')}/v1/scan?direction=both&limit=10&min_score=0",
        headers={"Accept": "application/json", "X-Radar-Key": api_key, "User-Agent": "swing-liquidity-shadow/1"},
    )
    candidates = dedupe_candidates(scan)
    books: dict[str, Any] = {}
    book_errors: dict[str, str] = {}
    protected_headers = {
        "Accept": "application/json",
        "X-Radar-Key": api_key,
        "User-Agent": "swing-liquidity-shadow/2",
    }
    for symbol in sorted({item["symbol"] for item in candidates}):
        try:
            payload = _get_json(
                f"{base_url.rstrip('/')}/v1/research/swing-liquidity/orderbook/{symbol}",
                headers=protected_headers,
            )
            if payload.get("research_only") is not True or payload.get("live_strategy_mutated") is not False:
                raise RuntimeError("research orderbook proxy invariant failed")
            result = {"b": payload.get("bids") or [], "a": payload.get("asks") or []}
            books[symbol] = {
                "ts": payload.get("upstream_time_ms"),
                "data_as_of": payload.get("data_as_of"),
                "update_id": payload.get("update_id"),
                "seq": payload.get("seq"),
                "bids": result["b"],
                "asks": result["a"],
                "costs": [book_cost_metrics(result, notional) for notional in NOTIONALS_USDC],
            }
        except Exception as exc:  # fail-open research coverage; never alter live state
            book_errors[symbol] = f"{type(exc).__name__}: {exc}"

    for candidate in candidates:
        turnover = candidate.get("turnover_24h_usdc")
        book = books.get(candidate["symbol"]) or {}
        candidate["book_costs"] = book.get("costs") or []
        candidate["participation_sensitivity"] = [
            {
                "notional_usdc": notional,
                "notional_over_turnover_pct": (
                    round(notional / turnover * 100.0, 8)
                    if isinstance(turnover, (int, float)) and turnover > 0
                    else None
                ),
            }
            for notional in NOTIONALS_USDC
        ]

    return {
        "study": "swing-liquidity-validation-v1",
        "research_only": True,
        "label_blind": True,
        "live_gate_unchanged": True,
        "captured_at": captured_at,
        "scan_data_as_of": scan.get("data_as_of"),
        "scan_data_quality": scan.get("data_quality"),
        "current_gate_reference": {"min_turnover_usdc": 100000.0, "max_spread_bps": 50.0},
        "standardized_notional_sensitivity_usdc": list(NOTIONALS_USDC),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "orderbooks": books,
        "orderbook_errors": book_errors,
        "methodology": {
            "future_labels_present": False,
            "primary_notional_usdc": 500.0,
            "pre_trigger_max_snapshot_age_minutes": 90,
            "preregistration": "backend/research/SWING_LIQUIDITY_VALIDATION_V1.md",
        },
    }


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "")
    bybit_base = os.getenv("BYBIT_EU_PUBLIC_BASE_URL", "https://api.bybit.eu").strip()
    output = Path(os.getenv("SWING_LIQUIDITY_SHADOW_OUTPUT", "swing-liquidity-shadow.json"))
    if not base_url or not api_key:
        raise SystemExit("required production API configuration is missing")
    snapshot = collect_snapshot(base_url, api_key, bybit_base)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
    safe = {
        "captured_at": snapshot["captured_at"],
        "scan_data_as_of": snapshot["scan_data_as_of"],
        "candidate_count": snapshot["candidate_count"],
        "orderbook_count": len(snapshot["orderbooks"]),
        "orderbook_error_count": len(snapshot["orderbook_errors"]),
        "turnover_tiers": sorted({item["turnover_tier"] for item in snapshot["candidates"]}),
        "spread_tiers": sorted({item["spread_tier"] for item in snapshot["candidates"]}),
    }
    print("SWING_LIQUIDITY_SHADOW_CAPTURED=" + json.dumps(safe, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
