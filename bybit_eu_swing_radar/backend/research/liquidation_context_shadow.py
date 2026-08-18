"""Label-free liquidation context for the research intelligence stack.

This module is deliberately independent from live strategy/scoring/execution.
It selects derivatives markets only as contextual data sources and never as
Bybit EU execution instruments.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

SPEC_VERSION = "liquidation-context-shadow-v1"
LOOKBACK_HOURS = 24
INTERVAL = "4hour"
MAX_SYMBOLS = 8
MAX_MARKET_ATTEMPTS_PER_SYMBOL = 2
EXCHANGE_PRIORITY = {"bybit": 0, "binance": 1, "okx": 2, "deribit": 3}
QUOTE_PRIORITY = {"USDT": 0, "USDC": 1, "USD": 2}


def spec() -> dict[str, Any]:
    return {
        "research_only": True,
        "context_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "version": SPEC_VERSION,
        "lookback_hours": LOOKBACK_HOURS,
        "interval": INTERVAL,
        "max_symbols": MAX_SYMBOLS,
        "max_market_attempts_per_symbol": MAX_MARKET_ATTEMPTS_PER_SYMBOL,
        "max_liquidation_symbol_calls": MAX_SYMBOLS * MAX_MARKET_ATTEMPTS_PER_SYMBOL,
        "exchange_priority": list(EXCHANGE_PRIORITY),
        "quote_priority": ["USDT", "USDC", "USD"],
        "selection_rule": "Resolve Coinalyze exchange codes through /exchanges; prefer exchange quality before quote, then USDT before USDC because derivatives are context-only.",
        "missing_data_rule": "Missing liquidation data is explicit context unavailability and never an eligibility gate.",
    }


def normalize_exchange_names(exchanges: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in exchanges:
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if code and name:
            result[code] = name
    return result


def _exchange_rank(name: str) -> int:
    lowered = name.lower()
    return next((rank for key, rank in EXCHANGE_PRIORITY.items() if key in lowered), 9)


def select_market_candidates(
    markets: Iterable[Mapping[str, Any]],
    exchange_names: Mapping[str, str],
    bases: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    wanted = tuple(dict.fromkeys(str(base).upper() for base in bases if str(base).strip()))
    result: dict[str, list[dict[str, Any]]] = {}
    for base in wanted:
        candidates: list[tuple[int, int, str, dict[str, Any]]] = []
        for raw in markets:
            if str(raw.get("base_asset") or "").upper() != base:
                continue
            if not bool(raw.get("is_perpetual", False)):
                continue
            quote = str(raw.get("quote_asset") or "").upper()
            if quote not in QUOTE_PRIORITY:
                continue
            symbol = str(raw.get("symbol") or "").strip()
            exchange_code = str(raw.get("exchange") or "").strip()
            if not symbol or not exchange_code:
                continue
            exchange_name = str(exchange_names.get(exchange_code) or exchange_code)
            row = dict(raw)
            row["resolved_exchange_name"] = exchange_name
            row["exchange_code"] = exchange_code
            candidates.append(
                (_exchange_rank(exchange_name), QUOTE_PRIORITY[quote], symbol, row)
            )
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        result[base] = [item[3] for item in candidates]
    return result


def history_map(payload: Any) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(payload, list):
        return result
    for row in payload:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "")
        history = row.get("history")
        if symbol and isinstance(history, list):
            result[symbol] = [dict(item) for item in history if isinstance(item, Mapping)]
    return result


def build_symbol_context(
    spot_symbol: str,
    market: Mapping[str, Any] | None,
    history: Iterable[Mapping[str, Any]] | None,
    *,
    fallback_used: bool,
    attempted_markets: Iterable[str],
) -> dict[str, Any]:
    rows = list(history or [])
    long_usd = sum(float(row.get("l") or 0.0) for row in rows)
    short_usd = sum(float(row.get("s") or 0.0) for row in rows)
    total_usd = long_usd + short_usd
    covered = bool(rows)
    state = (
        "AVAILABLE_ACTIVITY"
        if covered and total_usd > 0
        else "AVAILABLE_ZERO_ACTIVITY"
        if covered
        else "UNAVAILABLE"
    )
    latest_t = max((int(row.get("t") or 0) for row in rows), default=0)
    return {
        "symbol": str(spot_symbol).upper(),
        "coverage": covered,
        "state": state,
        "market_symbol": str((market or {}).get("symbol") or "") or None,
        "exchange_code": str((market or {}).get("exchange_code") or (market or {}).get("exchange") or "") or None,
        "exchange": str((market or {}).get("resolved_exchange_name") or "") or None,
        "quote_asset": str((market or {}).get("quote_asset") or "") or None,
        "symbol_on_exchange": (market or {}).get("symbol_on_exchange"),
        "fallback_used": bool(fallback_used),
        "attempted_markets": list(attempted_markets),
        "history_row_count": len(rows),
        "latest_history_timestamp": latest_t or None,
        "long_liquidations_24h_usd": long_usd if covered else None,
        "short_liquidations_24h_usd": short_usd if covered else None,
        "total_liquidations_24h_usd": total_usd if covered else None,
        "liquidation_skew": ((long_usd - short_usd) / total_usd) if total_usd > 0 else None,
        "research_only": True,
        "context_only": True,
        "execution_proof": False,
    }


def build_snapshot(
    rows: Iterable[Mapping[str, Any]],
    *,
    captured_at: datetime,
    source_commit_sha: str | None,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    items = [dict(row) for row in rows]
    covered = sum(1 for row in items if bool(row.get("coverage")))
    activity = sum(1 for row in items if row.get("state") == "AVAILABLE_ACTIVITY")
    zero_activity = sum(1 for row in items if row.get("state") == "AVAILABLE_ZERO_ACTIVITY")
    fallback = sum(1 for row in items if bool(row.get("fallback_used")))
    return {
        "research_only": True,
        "context_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "spec": spec(),
        "captured_at": captured_at.isoformat(),
        "source_commit_sha": source_commit_sha,
        "symbol_count": len(items),
        "coverage": {
            "total": len(items),
            "available": covered,
            "unavailable": len(items) - covered,
            "activity": activity,
            "zero_activity": zero_activity,
            "fallback_used": fallback,
        },
        "metadata": dict(metadata),
        "symbols": items,
    }
