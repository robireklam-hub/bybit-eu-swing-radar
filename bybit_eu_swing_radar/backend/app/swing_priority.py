from __future__ import annotations

from typing import Any, Iterable


DEFAULT_COMPACT_LIMIT = 3


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _setup_score(item: Any) -> float:
    try:
        return float(_get(item, "setup_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def is_strict_swing_candidate(item: Any) -> bool:
    side = _get(item, "side")
    if side not in {"long", "short"}:
        return False
    if _setup_score(item) < 70.0:
        return False
    if float(_get(item, "expansion_score", 0.0) or 0.0) < 55.0:
        return False
    if abs(float(_get(item, "direction_score", 0.0) or 0.0)) < 35.0:
        return False
    if float(_get(item, "quality_score", 0.0) or 0.0) < 60.0:
        return False
    expected_rr = _get(item, "expected_rr")
    if expected_rr is None or float(expected_rr) < 2.0:
        return False
    if side == "short" and not bool(_get(item, "shortable", False)):
        return False
    return True


def select_compact_priority_sections(
    longs: Iterable[Any],
    shorts: Iterable[Any],
    extended_watchlist: Iterable[Any],
    liquidity_blocked: Iterable[Any],
    *,
    limit: int = DEFAULT_COMPACT_LIMIT,
) -> dict[str, Any]:
    """Select the exact symbol set surfaced by swing getTopCandidates semantics.

    This mirrors the repository contract: strict candidates are thresholded and
    ranked by setup_score; watch candidates combine extended_watchlist with any
    additional liquidity_blocked items, exclude every strict symbol, split by
    side, and rank by setup_score. The returned ordered symbol list is unique and
    is suitable for upstream context-enrichment priority.
    """
    limit = max(int(limit), 0)
    strict_longs_all = sorted(
        [item for item in longs if is_strict_swing_candidate(item)],
        key=_setup_score,
        reverse=True,
    )
    strict_shorts_all = sorted(
        [item for item in shorts if is_strict_swing_candidate(item)],
        key=_setup_score,
        reverse=True,
    )
    strict_symbols = {
        str(_get(item, "symbol"))
        for item in [*strict_longs_all, *strict_shorts_all]
    }

    combined_watch = list(extended_watchlist)
    known = {str(_get(item, "symbol")) for item in combined_watch}
    for item in liquidity_blocked:
        symbol = str(_get(item, "symbol"))
        if symbol not in known:
            combined_watch.append(item)
            known.add(symbol)

    watch_longs = sorted(
        [
            item for item in combined_watch
            if _get(item, "side") == "long"
            and str(_get(item, "symbol")) not in strict_symbols
        ],
        key=_setup_score,
        reverse=True,
    )[:limit]
    watch_shorts = sorted(
        [
            item for item in combined_watch
            if _get(item, "side") == "short"
            and str(_get(item, "symbol")) not in strict_symbols
        ],
        key=_setup_score,
        reverse=True,
    )[:limit]

    sections = {
        "strict_longs": strict_longs_all[:limit],
        "strict_shorts": strict_shorts_all[:limit],
        "watch_only_longs": watch_longs,
        "watch_only_shorts": watch_shorts,
    }
    ordered_symbols: list[str] = []
    seen: set[str] = set()
    for section in (
        "strict_longs",
        "strict_shorts",
        "watch_only_longs",
        "watch_only_shorts",
    ):
        for item in sections[section]:
            symbol = str(_get(item, "symbol"))
            if symbol and symbol not in seen:
                ordered_symbols.append(symbol)
                seen.add(symbol)

    return {
        **sections,
        "strict_long_count": len(strict_longs_all),
        "strict_short_count": len(strict_shorts_all),
        "priority_symbols": ordered_symbols,
    }
