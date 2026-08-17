from __future__ import annotations

from typing import Any

from app.models import ScanResponse, Setup


RESEARCH_SCAN_USER_AGENT_PREFIX = "swing-liquidity-shadow/"
COMPACT_METRIC_KEYS = (
    "tradeable",
    "execution_status",
    "turnover_24h_usdc",
    "spread_bps",
    "volume_ratio_4h",
    "liquidity_reasons",
)


def is_research_full_scan_request(user_agent: str | None) -> bool:
    """Preserve the full /v1/scan contract for the forward liquidity collector.

    The prospective swing-liquidity collector has always sent a dedicated
    ``swing-liquidity-shadow/*`` User-Agent. Keeping that contract explicit lets
    the agent-facing scan become compact without changing, pausing, or rewriting
    the running research collection path.
    """
    return bool(
        isinstance(user_agent, str)
        and user_agent.lower().startswith(RESEARCH_SCAN_USER_AGENT_PREFIX)
    )


def _compact_setup(setup: Setup) -> Setup:
    item = setup.model_copy(deep=True)
    metrics = setup.metrics or {}
    item.metrics = {
        key: metrics[key]
        for key in COMPACT_METRIC_KEYS
        if key in metrics
    }
    # Detailed narrative/context is available from getSymbolSetup and candidate
    # derivatives are available from getTopCandidates. Removing them here keeps
    # getSwingScan safely below tool-response limits without changing ranking.
    item.thesis = []
    item.risks = []
    item.bullish_scenario = None
    item.bearish_scenario = None
    return item


def compact_swing_scan(scan: ScanResponse) -> ScanResponse:
    """Return an agent-safe view while leaving the cached full scan untouched."""
    result = scan.model_copy(deep=True)
    result.longs = [_compact_setup(item) for item in scan.longs]
    result.shorts = [_compact_setup(item) for item in scan.shorts]
    result.extended_watchlist = []
    result.liquidity_blocked = []
    result.momentum_radar = []
    result.exclusions = []
    return result


def _is_executable(setup: Setup) -> bool:
    execution_status = str((setup.metrics or {}).get("execution_status") or "")
    return execution_status not in {"WATCH_ONLY", "LIQUIDITY_BLOCKED"}


def select_fresh_symbol_setup(
    scan_payload: dict[str, Any] | None,
    symbol: str,
) -> Setup | None:
    """Select one symbol setup exclusively from the current latest_scan snapshot.

    This mirrors the worker's existing per-symbol preference rule (executable
    before watch-only, then higher setup score) but removes the stale independent
    ``setup:{symbol}`` cache read path. No worker, score, eligibility, or research
    write behavior is changed.
    """
    if not scan_payload:
        return None
    normalized = symbol.strip().upper()
    scan = ScanResponse.model_validate(scan_payload)
    candidates = [
        item
        for item in [
            *scan.longs,
            *scan.shorts,
            *scan.extended_watchlist,
            *scan.liquidity_blocked,
        ]
        if item.symbol.upper() == normalized
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            1 if _is_executable(item) else 0,
            float(item.setup_score),
        ),
    )
