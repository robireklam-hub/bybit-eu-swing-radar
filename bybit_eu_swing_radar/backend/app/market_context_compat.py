"""Compatibility bridge for GPT Actions that still use an older response schema.

The canonical market-context payload remains ``market_context_alerts``. This
module mirrors mandatory ELEVATED/HIGH warnings into already-established text
fields such as ``why_now``, ``risks`` and ``notes`` so an older Action schema
cannot silently discard the warning. It also attaches the non-executing day-trade
barrier-clear watch before legacy mirroring, so a strong setup blocked only by a
nearby structural barrier is reported as a conditional watch rather than silently
collapsing to plain NO_TRADE.

All transformations operate on copied HTTP responses only; strategy scores, gates,
cached records and execution state are untouched.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.day_barrier_clear_watch import enrich_barrier_clear_watch

_CANDIDATE_COLLECTION_KEYS = (
    "strict_longs",
    "strict_shorts",
    "watch_only_longs",
    "watch_only_shorts",
    "longs",
    "shorts",
    "extended_watchlist",
    "liquidity_blocked",
    "items",
)


def _append_unique(container: dict[str, Any], field: str, text: str) -> None:
    values = container.get(field)
    if not isinstance(values, list):
        return
    if text not in values:
        values.append(text)


def _warning_text(alerts: Mapping[str, Any]) -> str:
    impulse = alerts.get("market_impulse") or {}
    geopolitical = alerts.get("geopolitical") or {}
    macro = alerts.get("macro_liquidity") or {}
    ratio = impulse.get("max_relative_volume_ratio_5m_15m")
    ratio_text = "n/a" if ratio is None else f"{float(ratio):.3f}x"
    return (
        f"[MARKET_CONTEXT_WARNING:{alerts.get('warning_level', 'UNKNOWN')}] "
        f"{alerts.get('headline') or 'Elevated market context.'} "
        f"market_impulse={impulse.get('state', 'UNKNOWN')}({ratio_text}); "
        f"geopolitical={geopolitical.get('state', 'UNAVAILABLE')}; "
        f"macro_liquidity={macro.get('state', 'UNAVAILABLE')}; "
        f"causal_attribution={alerts.get('causal_attribution', 'UNCONFIRMED')}; "
        "context_only=true; hard_gate=false."
    )


def mirror_mandatory_warning(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Mirror mandatory market context into fields known by older Action schemas."""
    copied = deepcopy(dict(payload))
    alerts = copied.get("market_context_alerts")
    if not isinstance(alerts, Mapping):
        return copied
    level = str(alerts.get("warning_level") or "UNKNOWN")
    mandatory = alerts.get("mandatory_user_warning") is True
    if level not in {"ELEVATED", "HIGH"} and not mandatory:
        return copied

    warning = _warning_text(alerts)
    _append_unique(copied, "why_now", warning)
    _append_unique(copied, "risks", warning)
    _append_unique(copied, "notes", warning)

    market_regime = copied.get("market_regime")
    if isinstance(market_regime, dict):
        _append_unique(market_regime, "notes", warning)

    for key in _CANDIDATE_COLLECTION_KEYS:
        values = copied.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            _append_unique(item, "why_now", warning)
            _append_unique(item, "risks", warning)

    return copied


def install_market_context_compatibility_bridge(market_context_module: Any) -> None:
    """Wrap the canonical response enricher once, before FastAPI routes register."""
    if getattr(market_context_module, "_legacy_field_bridge_installed", False):
        return
    original = market_context_module.enrich_market_response

    async def compatible_enricher(result: Any) -> Any:
        enriched = await original(result)
        if not isinstance(enriched, Mapping):
            return enriched
        barrier_enriched = enrich_barrier_clear_watch(enriched)
        return mirror_mandatory_warning(barrier_enriched)

    market_context_module.enrich_market_response = compatible_enricher
    market_context_module._legacy_field_bridge_installed = True
