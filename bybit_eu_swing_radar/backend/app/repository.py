import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import asyncpg
from asyncpg.exceptions import UndefinedTableError

from app.config import settings
from app.flow_freshness import apply_flow_freshness, summarize_flow_payloads
from app.models import (
    BacktestAggregate,
    BacktestGroupStats,
    DayTradeBacktestSignal,
    DayTradeBacktestSignalsResponse,
    DayTradeBacktestStatusResponse,
    DayTradeBacktestSummaryResponse,
    DayTradeDiagnosticStatusResponse,
    DayTradeEdgeDiagnosticsResponse,
    DayTradeGateWaterfallResponse,
    DayTradeFlowContextResponse,
    DiagnosticCohortStats,
    DiagnosticCountGroup,
    DiagnosticGateStep,
    DiagnosticSegment,
    DiagnosticSensitivityStats,
    ExcursionThreshold,
    DayTradeCandidate,
    DayTradeJournalSignal,
    DayTradeJournalSignalsResponse,
    DayTradeJournalSummaryResponse,
    DayTradeScanResponse,
    DayTradeTopCandidatesResponse,
    DayTradeSymbolAuditResponse,
    JournalAggregate,
    JournalGroupStats,
    MarketRegime,
    MomentumResponse,
    ScanResponse,
    Setup,
    TopCandidatesResponse,
    WatchlistResponse,
)

CURRENT_DAY_STRATEGY_VERSION = "0.7.2"


class RadarRepository:
    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(settings.database_url)

    async def get_cache(self, cache_key: str) -> dict | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT payload
                FROM radar_cache
                WHERE cache_key = $1
                LIMIT 1
                """,
                cache_key,
            )
        finally:
            await conn.close()
        if not row:
            return None
        payload = row["payload"]
        return json.loads(payload) if isinstance(payload, str) else payload

    async def get_latest_scan(self, direction: str, limit: int, min_score: float) -> ScanResponse | None:
        payload = await self.get_cache("latest_scan")
        if payload is None:
            return None
        response = ScanResponse.model_validate(payload)
        if direction == "long":
            response.shorts = []
        elif direction == "short":
            response.longs = []
        response.longs = [x for x in response.longs if x.setup_score >= min_score][:limit]
        response.shorts = [x for x in response.shorts if x.setup_score >= min_score][:limit]
        return response

    async def get_setup(self, symbol: str) -> Setup | None:
        payload = await self.get_cache(f"setup:{symbol.upper()}")
        return Setup.model_validate(payload) if payload is not None else None

    async def get_regime(self) -> MarketRegime | None:
        payload = await self.get_cache("market_regime")
        return MarketRegime.model_validate(payload) if payload is not None else None

    async def get_watchlist(self, limit: int = 20) -> WatchlistResponse | None:
        payload = await self.get_cache("watchlist")
        if payload is None:
            return None
        response = WatchlistResponse.model_validate(payload)
        response.items = response.items[:limit]
        return response

    async def get_data_status(self) -> dict | None:
        return await self.get_cache("data_status")


    async def get_momentum_radar(
        self, direction: str = "both", limit: int = 10, min_score: float = 50.0
    ) -> MomentumResponse | None:
        payload = await self.get_cache("momentum_radar")
        if payload is None:
            return None
        response = MomentumResponse.model_validate(payload)
        if direction in {"long", "short"}:
            response.items = [item for item in response.items if item.side == direction]
        response.items = [item for item in response.items if item.momentum_score >= min_score][:limit]
        return response



def _strict_candidate(setup: Setup) -> bool:
    """Apply the documented executable swing minimums, not score alone."""
    if setup.side not in {"long", "short"}:
        return False
    if setup.setup_score < 70:
        return False
    if setup.expansion_score < 55:
        return False
    if abs(setup.direction_score) < 35:
        return False
    if setup.quality_score < 60:
        return False
    if setup.expected_rr is None or setup.expected_rr < 2.0:
        return False
    if setup.side == "short" and not setup.shortable:
        return False
    return True


def _compact_candidate(setup: Setup, category: str) -> dict:
    metrics = setup.metrics or {}
    tradeable = bool(metrics.get("tradeable", category == "STRICT"))
    liquidity_reasons = list(metrics.get("liquidity_reasons", []))
    execution_status = str(
        metrics.get(
            "execution_status",
            "EXECUTION_ELIGIBLE" if category == "STRICT" else "WATCH_ONLY",
        )
    )
    volume_ratio = metrics.get("volume_ratio_4h")
    volume_confirmed = (
        isinstance(volume_ratio, (int, float)) and float(volume_ratio) >= 1.2
    )

    if category == "WATCH_ONLY":
        decision = "NO_TRADE"
    elif setup.state == "TRIGGERED" and volume_confirmed:
        decision = "TRADE"
    else:
        decision = "WAIT"

    # Defensive override: anything blocked or non-tradeable can never be WAIT/TRADE.
    if (
        not tradeable
        or "LIQUIDITY_BLOCKED" in execution_status
        or liquidity_reasons
        or (setup.side == "short" and not setup.shortable)
    ):
        decision = "NO_TRADE"

    return {
        "symbol": setup.symbol,
        "side": setup.side,
        "category": category,
        "state": setup.state,
        "grade": setup.grade,
        "decision": decision,
        "last_price": setup.last_price,
        "setup_score": setup.setup_score,
        "expansion_score": setup.expansion_score,
        "direction_score": setup.direction_score,
        "quality_score": setup.quality_score,
        "shortable": setup.shortable,
        "tradeable": tradeable,
        "execution_status": execution_status,
        "execution_modes": setup.execution_modes,
        "trigger": setup.trigger.model_dump() if setup.trigger else None,
        "entry_zone": setup.entry_zone.model_dump() if setup.entry_zone else None,
        "stop": setup.stop,
        "invalidation": setup.invalidation,
        "targets": setup.targets,
        "expected_rr": setup.expected_rr,
        "turnover_24h_usdc": metrics.get("turnover_24h_usdc"),
        "spread_bps": metrics.get("spread_bps"),
        "volume_ratio_4h": volume_ratio,
        "liquidity_reasons": liquidity_reasons,
        "weakest_point": setup.weakest_point,
        "data_quality": setup.data_quality,
        "missing_data": setup.missing_data,
    }


async def _get_top_candidates(
    repository: RadarRepository,
    limit: int = 3,
    include_watchlist: bool = True,
) -> TopCandidatesResponse | None:
    payload = await repository.get_cache("latest_scan")
    if payload is None:
        return None

    scan = ScanResponse.model_validate(payload)

    strict_longs_all = sorted(
        [item for item in scan.longs if _strict_candidate(item)],
        key=lambda item: item.setup_score,
        reverse=True,
    )
    strict_shorts_all = sorted(
        [item for item in scan.shorts if _strict_candidate(item)],
        key=lambda item: item.setup_score,
        reverse=True,
    )

    strict_symbols = {
        item.symbol for item in strict_longs_all + strict_shorts_all
    }

    watch_longs: list[Setup] = []
    watch_shorts: list[Setup] = []
    if include_watchlist:
        # Use the broad discovery list, but never merge it into strict results.
        combined_watch = list(scan.extended_watchlist)
        known = {item.symbol for item in combined_watch}
        for item in scan.liquidity_blocked:
            if item.symbol not in known:
                combined_watch.append(item)
                known.add(item.symbol)

        watch_longs = sorted(
            [
                item for item in combined_watch
                if item.side == "long" and item.symbol not in strict_symbols
            ],
            key=lambda item: item.setup_score,
            reverse=True,
        )[:limit]
        watch_shorts = sorted(
            [
                item for item in combined_watch
                if item.side == "short" and item.symbol not in strict_symbols
            ],
            key=lambda item: item.setup_score,
            reverse=True,
        )[:limit]

    regime = scan.market_regime
    compact_regime = {
        "btc_regime": regime.btc_regime,
        "btc_structure_1d": regime.btc_structure_1d,
        "btc_structure_4h": regime.btc_structure_4h,
        "alt_breadth": regime.alt_breadth,
        "volatility_regime": regime.volatility_regime,
        "preferred_side": regime.preferred_side,
        "source_quality": regime.source_quality,
    }

    return TopCandidatesResponse(
        data_as_of=scan.data_as_of,
        data_as_of_budapest=scan.data_as_of_budapest,
        data_quality=scan.data_quality,
        market_regime=compact_regime,
        requested_limit=limit,
        strict_long_count=len(strict_longs_all),
        strict_short_count=len(strict_shorts_all),
        strict_longs=[
            _compact_candidate(item, "STRICT")
            for item in strict_longs_all[:limit]
        ],
        strict_shorts=[
            _compact_candidate(item, "STRICT")
            for item in strict_shorts_all[:limit]
        ],
        watch_only_longs=[
            _compact_candidate(item, "WATCH_ONLY")
            for item in watch_longs
        ],
        watch_only_shorts=[
            _compact_candidate(item, "WATCH_ONLY")
            for item in watch_shorts
        ],
        notes=[
            "Strict lists enforce setup>=70, expansion>=55, abs(direction)>=35, quality>=60 and RR>=2.",
            "WATCH_ONLY lists are separate fallback context and must never be presented as executable candidates.",
            "Liquidity-blocked or non-shortable items always have decision=NO_TRADE.",
        ],
    )


# Bind as a repository method without changing existing database behavior.
RadarRepository.get_top_candidates = _get_top_candidates



async def _get_day_trade_scan(
    repository: RadarRepository,
    direction: str = "both",
    limit: int = 10,
    min_score: float = 0.0,
    include_watchlist: bool = True,
) -> DayTradeScanResponse | None:
    payload = await repository.get_cache("day_trade_scan")
    if payload is None:
        return None
    response = DayTradeScanResponse.model_validate(payload)

    def filter_items(items: list[DayTradeCandidate]) -> list[DayTradeCandidate]:
        return [item for item in items if item.setup_score >= min_score][:limit]

    if direction == "long":
        response.strict_shorts = []
        response.watch_only_shorts = []
    elif direction == "short":
        response.strict_longs = []
        response.watch_only_longs = []

    response.strict_longs = filter_items(response.strict_longs)
    response.strict_shorts = filter_items(response.strict_shorts)
    if include_watchlist:
        response.watch_only_longs = filter_items(response.watch_only_longs)
        response.watch_only_shorts = filter_items(response.watch_only_shorts)
    else:
        response.watch_only_longs = []
        response.watch_only_shorts = []
    return response


def _day_watch_rank(candidate: DayTradeCandidate) -> tuple:
    metrics = candidate.metrics or {}
    bucket_rank = {
        "NEAR_STRICT": 4,
        "LOW_CONVICTION": 3,
        "POOR_RR": 2,
        "TIMEFRAME_CONFLICT": 1,
        "LIQUIDITY_OR_BORROW_BLOCKED": 0,
    }
    target_path_valid = bool(metrics.get("target_path_valid", False))
    triggered = bool((candidate.trigger or {}).get("triggered"))
    execution_ok = candidate.tradeable and (
        candidate.side == "long" or candidate.shortable
    ) and not candidate.timeframe_conflict
    return (
        1 if execution_ok else 0,
        1 if target_path_valid else 0,
        bucket_rank.get(candidate.watch_bucket or "", -1),
        1 if candidate.side_direction_score > 0 else 0,
        1 if triggered else 0,
        candidate.expected_rr,
        candidate.side_direction_score,
        candidate.setup_score,
        candidate.quality_score,
    )


def _rankable_day_watch(candidate: DayTradeCandidate) -> bool:
    """Top-candidate watchlist is intentionally sparse; never fill weak slots."""
    metrics = candidate.metrics or {}
    return (
        candidate.category == "WATCH_ONLY"
        and candidate.tradeable
        and (candidate.side == "long" or candidate.shortable)
        and not candidate.timeframe_conflict
        and candidate.side_direction_score > 0
        and bool(metrics.get("target_path_valid", False))
        and candidate.expected_rr >= 1.0
        and candidate.setup_score >= 55.0
    )


def _dedupe_day_watchlists(
    longs: list[DayTradeCandidate],
    shorts: list[DayTradeCandidate],
    strict_symbols: set[str],
) -> tuple[list[DayTradeCandidate], list[DayTradeCandidate], list[str]]:
    by_symbol: dict[str, list[DayTradeCandidate]] = defaultdict(list)
    for candidate in [*longs, *shorts]:
        if candidate.symbol in strict_symbols or not _rankable_day_watch(candidate):
            continue
        by_symbol[candidate.symbol].append(candidate)

    kept_longs: list[DayTradeCandidate] = []
    kept_shorts: list[DayTradeCandidate] = []
    removed: list[str] = []
    for symbol, candidates in by_symbol.items():
        winner = max(candidates, key=_day_watch_rank)
        for candidate in candidates:
            if candidate is not winner:
                removed.append(f"{symbol}:{candidate.side}")
        if winner.side == "long":
            kept_longs.append(winner)
        else:
            kept_shorts.append(winner)

    kept_longs.sort(key=_day_watch_rank, reverse=True)
    kept_shorts.sort(key=_day_watch_rank, reverse=True)
    return kept_longs, kept_shorts, sorted(removed)


async def _get_day_trade_top_candidates(
    repository: RadarRepository,
    limit: int = 3,
    include_watchlist: bool = True,
) -> DayTradeTopCandidatesResponse | None:
    payload = await repository.get_cache("day_trade_scan")
    if payload is None:
        return None
    scan = DayTradeScanResponse.model_validate(payload)
    strict_longs = scan.strict_longs[:limit]
    strict_shorts = scan.strict_shorts[:limit]
    strict_symbols = {item.symbol for item in [*scan.strict_longs, *scan.strict_shorts]}
    watch_longs: list[DayTradeCandidate] = []
    watch_shorts: list[DayTradeCandidate] = []
    dedup_removed: list[str] = []
    if include_watchlist:
        watch_longs, watch_shorts, dedup_removed = _dedupe_day_watchlists(
            scan.watch_only_longs,
            scan.watch_only_shorts,
            strict_symbols,
        )
    return DayTradeTopCandidatesResponse(
        data_as_of=scan.data_as_of,
        data_as_of_budapest=scan.data_as_of_budapest,
        data_quality=scan.data_quality,
        market_regime=scan.market_regime,
        requested_limit=limit,
        strict_long_count=len(scan.strict_longs),
        strict_short_count=len(scan.strict_shorts),
        strict_longs=strict_longs,
        strict_shorts=strict_shorts,
        watch_only_longs=watch_longs[:limit],
        watch_only_shorts=watch_shorts[:limit],
        coverage=scan.coverage,
        assumptions=scan.assumptions,
        notes=scan.notes + [
            "Do not fill missing strict or watch slots with weaker fallback items.",
            "Top watchlists are cross-side deduplicated: one symbol can appear on only one dominant side.",
            "Top watchlists exclude timeframe-conflict, blocked, invalid-target-path and expected-RR<1.0 items.",
            (
                "Cross-side watch variants removed: " + ", ".join(dedup_removed)
                if dedup_removed
                else "No cross-side watch variants required removal."
            ),
            "Only decision=TRADE with state=TRIGGERED is an immediately actionable day-trade setup.",
        ],
    )


async def _get_day_trade_setup(
    repository: RadarRepository,
    symbol: str,
) -> DayTradeCandidate | None:
    payload = await repository.get_cache(f"day_trade_setup:{symbol.upper()}")
    return DayTradeCandidate.model_validate(payload) if payload is not None else None


async def _get_day_trade_audit(
    repository: RadarRepository,
    symbol: str,
) -> DayTradeSymbolAuditResponse | None:
    payload = await repository.get_cache(f"day_trade_audit:{symbol.upper()}")
    return (
        DayTradeSymbolAuditResponse.model_validate(payload)
        if payload is not None
        else None
    )


async def _get_day_trade_status(
    repository: RadarRepository,
) -> dict | None:
    return await repository.get_cache("day_trade_status")


async def _get_day_trade_flow(
    repository: RadarRepository,
    symbol: str,
) -> DayTradeFlowContextResponse | None:
    payload = await repository.get_cache(f"day_trade_flow:{symbol.upper()}")
    return (
        DayTradeFlowContextResponse.model_validate(apply_flow_freshness(payload))
        if payload is not None
        else None
    )


async def _get_day_trade_flow_status(
    repository: RadarRepository,
) -> dict | None:
    status = await repository.get_cache("day_trade_flow_status")
    if status is None:
        return None
    result = dict(status)
    symbols = result.get("symbols")
    flow_batch_id = result.get("flow_batch_id")
    if not isinstance(symbols, list) or not isinstance(flow_batch_id, str) or not flow_batch_id:
        # Legacy records cannot identify their batch reliably. Never retain a
        # legacy GOOD count, regardless of the aggregate timestamp.
        result["partial"] = int(result.get("partial", 0)) + int(result.get("good", 0))
        result["good"] = 0
        result["processed"] = (
            result["good"]
            + result["partial"]
            + int(result.get("no_derivative_match", 0))
        )
        return result

    payloads = []
    for symbol in symbols:
        payloads.append(await repository.get_cache(f"day_trade_flow:{str(symbol).upper()}"))
    counts = summarize_flow_payloads(payloads, flow_batch_id=flow_batch_id)
    result.update(processed=len(symbols), **counts)
    return result


RadarRepository.get_day_trade_scan = _get_day_trade_scan
RadarRepository.get_day_trade_top_candidates = _get_day_trade_top_candidates
RadarRepository.get_day_trade_setup = _get_day_trade_setup
RadarRepository.get_day_trade_audit = _get_day_trade_audit
RadarRepository.get_day_trade_status = _get_day_trade_status
RadarRepository.get_day_trade_flow = _get_day_trade_flow
RadarRepository.get_day_trade_flow_status = _get_day_trade_flow_status



def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _journal_aggregate(rows: list[dict[str, Any]]) -> JournalAggregate:
    closed = [row for row in rows if row.get("status") == "CLOSED"]
    net_values = [
        float(row["net_r"])
        for row in closed
        if row.get("net_r") is not None
    ]
    positive = [value for value in net_values if value > 0]
    negative = [value for value in net_values if value < 0]
    tp2_count = sum(row.get("exit_reason") == "TP2" for row in closed)
    stop_count = sum(row.get("exit_reason") == "STOP" for row in closed)
    ambiguous = sum(
        row.get("exit_reason") == "AMBIGUOUS_STOP_FIRST" for row in closed
    )
    time_exit = sum(row.get("exit_reason") == "TIME_EXIT" for row in closed)
    profit_factor = None
    if negative:
        profit_factor = sum(positive) / abs(sum(negative))
    elif positive:
        profit_factor = None

    return JournalAggregate(
        sample_size=len(rows),
        open_count=sum(row.get("status") == "OPEN" for row in rows),
        closed_count=len(closed),
        tp2_count=tp2_count,
        stop_count=stop_count,
        ambiguous_stop_count=ambiguous,
        time_exit_count=time_exit,
        positive_net_count=len(positive),
        target_hit_rate_pct=(
            round(tp2_count / len(closed) * 100.0, 2) if closed else None
        ),
        positive_net_rate_pct=(
            round(len(positive) / len(closed) * 100.0, 2) if closed else None
        ),
        average_net_r=(
            round(statistics.fmean(net_values), 4) if net_values else None
        ),
        median_net_r=(
            round(statistics.median(net_values), 4) if net_values else None
        ),
        profit_factor=(
            round(profit_factor, 4) if profit_factor is not None else None
        ),
        average_mfe_r=(
            round(
                statistics.fmean(float(row.get("mfe_r") or 0.0) for row in closed),
                4,
            )
            if closed
            else None
        ),
        average_mae_r=(
            round(
                statistics.fmean(float(row.get("mae_r") or 0.0) for row in closed),
                4,
            )
            if closed
            else None
        ),
    )


def _group_journal_rows(
    rows: list[dict[str, Any]],
    field: str,
) -> list[JournalGroupStats]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "UNKNOWN")].append(row)
    return [
        JournalGroupStats(key=key, stats=_journal_aggregate(values))
        for key, values in sorted(
            groups.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        )
    ]


def _evidence_status(strict_closed: int) -> str:
    if strict_closed < 30:
        return "INSUFFICIENT_SAMPLE"
    if strict_closed < 100:
        return "EARLY_SAMPLE"
    return "EVALUABLE_SAMPLE"


async def _get_day_trade_journal_summary(
    repository: RadarRepository,
    days: int = 30,
    signal_class: str = "all",
) -> DayTradeJournalSummaryResponse:
    conn = await repository._connect()
    try:
        rows_raw = await conn.fetch(
            """
            SELECT signal_class, symbol, side, status, setup_type,
                   exit_reason, net_r, mfe_r, mae_r
            FROM day_trade_signal_journal
            WHERE opened_at >= NOW() - ($1::int * INTERVAL '1 day')
              AND strategy_version = $3
              AND ($2 = 'all' OR signal_class = $2)
            ORDER BY opened_at DESC
            """,
            days,
            signal_class,
            CURRENT_DAY_STRATEGY_VERSION,
        )
        strict_closed = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM day_trade_signal_journal
                WHERE signal_class = 'STRICT'
                  AND strategy_version = $2
                  AND status = 'CLOSED'
                  AND opened_at >= NOW() - ($1::int * INTERVAL '1 day')
                """,
                days,
                CURRENT_DAY_STRATEGY_VERSION,
            )
            or 0
        )
        latest_run_raw = await conn.fetchrow(
            """
            SELECT run_at, strategy_version, data_quality, coverage,
                   strict_long_count, strict_short_count,
                   triggered_trade_count, shadow_trigger_count,
                   new_signal_count, evaluated_signal_count,
                   closed_signal_count, active_signal_count
            FROM day_trade_journal_runs
            WHERE strategy_version = $1
            ORDER BY run_at DESC
            LIMIT 1
            """,
            CURRENT_DAY_STRATEGY_VERSION,
        )
    except UndefinedTableError:
        rows_raw = []
        strict_closed = 0
        latest_run_raw = None
    finally:
        await conn.close()

    rows = [dict(row) for row in rows_raw]
    latest_run = dict(latest_run_raw) if latest_run_raw else {}
    if latest_run.get("coverage") is not None:
        latest_run["coverage"] = _json_value(latest_run["coverage"])

    warnings = []
    if strict_closed < 30:
        warnings.append(
            "Fewer than 30 closed STRICT signals: no reliable edge conclusion is possible."
        )
    elif strict_closed < 100:
        warnings.append(
            "The STRICT sample is still early; regime and selection bias can dominate results."
        )
    else:
        warnings.append(
            "A sample of 100+ signals is evaluable, but it is not proof of a stable future edge."
        )
    if not rows:
        warnings.append(
            "No journal signals exist in the selected window; the journal is prospective and has no backfill."
        )

    return DayTradeJournalSummaryResponse(
        strategy_version=CURRENT_DAY_STRATEGY_VERSION,
        generated_at=datetime.now(timezone.utc),
        window_days=days,
        requested_signal_class=signal_class,
        evidence_status=_evidence_status(strict_closed),
        strict_closed_sample=strict_closed,
        overall=_journal_aggregate(rows),
        by_signal_class=_group_journal_rows(rows, "signal_class"),
        by_side=_group_journal_rows(rows, "side"),
        by_setup_type=_group_journal_rows(rows, "setup_type"),
        latest_run=latest_run,
        methodology=[
            "Prospective records only; no historical backfill.",
            "STRICT and SHADOW signals are reported separately.",
            "Entry is modeled at the trigger candle close.",
            "Primary outcome is TP2 versus stop within 8 hours.",
            "If stop and TP2 occur in the same 5m candle, stop is assumed first.",
            "Net R subtracts the configured round-trip cost assumption.",
        ],
        warnings=warnings,
    )


async def _get_day_trade_journal_signals(
    repository: RadarRepository,
    status: str = "all",
    signal_class: str = "all",
    symbol: str | None = None,
    limit: int = 50,
) -> DayTradeJournalSignalsResponse:
    conn = await repository._connect()
    try:
        rows_raw = await conn.fetch(
            """
            SELECT id, signal_key, strategy_version, signal_class, symbol,
                   side, status, opened_at, expires_at, closed_at, setup_type,
                   entry_price, trigger_price, stop_price, tp1, tp2, tp3,
                   expected_rr, modeled_tp2_r, entry_deviation_bps,
                   entry_within_zone, setup_score, expansion_score, direction_score,
                   side_direction_score, quality_score, bars_observed, mfe_r,
                   mae_r, exit_price, exit_reason, gross_r, net_r, cost_bps
            FROM day_trade_signal_journal
            WHERE strategy_version = $4
              AND ($1 = 'all' OR status = $1)
              AND ($2 = 'all' OR signal_class = $2)
              AND ($3::text IS NULL OR symbol = UPPER($3))
            ORDER BY opened_at DESC
            LIMIT $5
            """,
            status,
            signal_class,
            symbol,
            CURRENT_DAY_STRATEGY_VERSION,
            limit,
        )
    except UndefinedTableError:
        rows_raw = []
    finally:
        await conn.close()

    items = [DayTradeJournalSignal.model_validate(dict(row)) for row in rows_raw]
    return DayTradeJournalSignalsResponse(
        generated_at=datetime.now(timezone.utc),
        count=len(items),
        items=items,
    )


RadarRepository.get_day_trade_journal_summary = _get_day_trade_journal_summary
RadarRepository.get_day_trade_journal_signals = _get_day_trade_journal_signals



def _backtest_aggregate(rows: list[dict[str, Any]]) -> BacktestAggregate:
    sample = len(rows)
    positives = [float(row.get("net_r") or 0.0) for row in rows if float(row.get("net_r") or 0.0) > 0]
    negatives = [float(row.get("net_r") or 0.0) for row in rows if float(row.get("net_r") or 0.0) < 0]
    gains = sum(positives)
    losses = abs(sum(negatives))
    net_values = [float(row.get("net_r") or 0.0) for row in rows]
    return BacktestAggregate(
        sample_size=sample,
        tp2_count=sum(1 for row in rows if row.get("exit_reason") == "TP2"),
        stop_count=sum(1 for row in rows if row.get("exit_reason") == "STOP"),
        ambiguous_stop_count=sum(1 for row in rows if row.get("exit_reason") == "AMBIGUOUS_STOP_FIRST"),
        time_exit_count=sum(1 for row in rows if row.get("exit_reason") == "TIME_EXIT"),
        positive_net_count=len(positives),
        target_hit_rate_pct=(round(sum(1 for row in rows if row.get("exit_reason") == "TP2") / sample * 100.0, 2) if sample else None),
        positive_net_rate_pct=(round(len(positives) / sample * 100.0, 2) if sample else None),
        average_net_r=(round(statistics.fmean(net_values), 4) if net_values else None),
        median_net_r=(round(statistics.median(net_values), 4) if net_values else None),
        profit_factor=(round(gains / losses, 4) if losses > 0 else None),
        average_mfe_r=(round(statistics.fmean(float(row.get("mfe_r") or 0.0) for row in rows), 4) if rows else None),
        average_mae_r=(round(statistics.fmean(float(row.get("mae_r") or 0.0) for row in rows), 4) if rows else None),
    )


def _backtest_groups(rows: list[dict[str, Any]], field: str) -> list[BacktestGroupStats]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "UNKNOWN")].append(row)
    return [
        BacktestGroupStats(key=key, stats=_backtest_aggregate(values))
        for key, values in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)
    ]


def _setup_score_band(score: float) -> str:
    if score >= 80:
        return "80+"
    if score >= 70:
        return "70-79.99"
    if score >= 65:
        return "65-69.99"
    return "<65"


async def _get_day_trade_backtest_status(repository: RadarRepository) -> DayTradeBacktestStatusResponse:
    conn = await repository._connect()
    try:
        job_raw = await conn.fetchrow("SELECT * FROM day_trade_backtest_jobs ORDER BY id DESC LIMIT 1")
        if not job_raw:
            return DayTradeBacktestStatusResponse(
                generated_at=datetime.now(timezone.utc), exists=False,
                job={}, progress_pct=0.0, symbol_status=[],
                warnings=["Backtest job has not been initialized."],
            )
        job = dict(job_raw)
        symbols_raw = await conn.fetch(
            """SELECT symbol,status,bars_fetched,evaluation_bars,signal_count,
                      primary_signal_count,last_error,started_at,completed_at
               FROM day_trade_backtest_symbols WHERE job_id=$1
               ORDER BY status,symbol""",
            int(job["id"]),
        )
    except UndefinedTableError:
        return DayTradeBacktestStatusResponse(
            generated_at=datetime.now(timezone.utc), exists=False,
            job={}, progress_pct=0.0, symbol_status=[],
            warnings=["Backtest tables do not exist yet."],
        )
    finally:
        await conn.close()
    total = int(job.get("total_symbols") or 0)
    completed = int(job.get("completed_symbols") or 0)
    failed = int(job.get("failed_symbols") or 0)
    progress = round((completed + failed) / total * 100.0, 2) if total else 0.0
    warnings = job.get("warnings") or []
    if isinstance(warnings, str): warnings = json.loads(warnings)
    parameters = job.get("parameters") or {}
    universe = job.get("universe") or []
    if isinstance(parameters, str): parameters = json.loads(parameters)
    if isinstance(universe, str): universe = json.loads(universe)
    job["parameters"] = parameters
    job["universe"] = universe
    job["warnings"] = warnings
    return DayTradeBacktestStatusResponse(
        generated_at=datetime.now(timezone.utc), exists=True, job=job,
        progress_pct=progress, symbol_status=[dict(row) for row in symbols_raw],
        warnings=list(warnings),
    )


async def _get_day_trade_backtest_summary(
    repository: RadarRepository,
    signal_class: str = "all",
    side: str = "both",
    primary_only: bool = True,
) -> DayTradeBacktestSummaryResponse:
    conn = await repository._connect()
    try:
        job_raw = await conn.fetchrow("SELECT * FROM day_trade_backtest_jobs ORDER BY id DESC LIMIT 1")
        if not job_raw:
            job = {"status": "NOT_INITIALIZED"}
            rows_raw = []
            strict_primary = 0
        else:
            job = dict(job_raw)
            rows_raw = await conn.fetch(
            """
            SELECT signal_class, execution_assumption, included_primary,
                   symbol, side, opened_at, setup_type, exit_reason,
                   net_r, mfe_r, mae_r, setup_score
            FROM day_trade_backtest_signals
            WHERE job_id=$1
              AND ($2='all' OR signal_class=$2)
              AND ($3='both' OR side=$3)
              AND (NOT $4 OR included_primary)
            ORDER BY opened_at
            """,
                int(job["id"]), signal_class, side, primary_only,
            )
            strict_primary = int(await conn.fetchval(
                """SELECT COUNT(*) FROM day_trade_backtest_signals
                   WHERE job_id=$1 AND signal_class='STRICT' AND included_primary""",
                int(job["id"]),
            ) or 0)
    except UndefinedTableError:
        job = {"status": "NOT_INITIALIZED"}
        rows_raw = []
        strict_primary = 0
    finally:
        await conn.close()
    rows = [dict(row) for row in rows_raw]
    for row in rows:
        opened = row.get("opened_at")
        row["month"] = opened.strftime("%Y-%m") if opened else "UNKNOWN"
        row["setup_score_band"] = _setup_score_band(float(row.get("setup_score") or 0.0))
    if strict_primary < 30:
        evidence = "INSUFFICIENT_SAMPLE"
    elif strict_primary < 100:
        evidence = "EARLY_SAMPLE"
    else:
        evidence = "EVALUABLE_SAMPLE"
    warnings = job.get("warnings") or []
    if isinstance(warnings, str): warnings = json.loads(warnings)
    params = job.get("parameters") or {}
    universe = job.get("universe") or []
    if isinstance(params, str): params = json.loads(params)
    if isinstance(universe, str): universe = json.loads(universe)
    job["parameters"] = params
    job["universe"] = universe
    job["warnings"] = warnings
    return DayTradeBacktestSummaryResponse(
        strategy_version=str(job.get("strategy_version", "0.7.2")),
        generated_at=datetime.now(timezone.utc), job=job,
        requested_signal_class=signal_class, requested_side=side,
        primary_only=primary_only, evidence_status=evidence,
        strict_primary_sample=strict_primary,
        overall=_backtest_aggregate(rows),
        by_signal_class=_backtest_groups(rows, "signal_class"),
        by_side=_backtest_groups(rows, "side"),
        by_setup_type=_backtest_groups(rows, "setup_type"),
        by_execution_assumption=_backtest_groups(rows, "execution_assumption"),
        by_month=_backtest_groups(rows, "month"),
        by_setup_score_band=_backtest_groups(rows, "setup_score_band"),
        methodology=[
            "Closed 5m bars only; 15m/1h/4h are locally aggregated and must be fully closed.",
            "Entry is the trigger-bar close; outcome is TP2 versus stop within the configured horizon.",
            "Same-candle stop/target ambiguity is conservatively stop-first.",
            "Primary metrics exclude overlapping same-symbol/same-side signals when configured.",
            "BACKTEST, prospective STRICT and prospective SHADOW samples must remain separate.",
        ],
        warnings=list(warnings),
    )


async def _get_day_trade_backtest_signals(
    repository: RadarRepository,
    signal_class: str = "all",
    side: str = "both",
    symbol: str | None = None,
    primary_only: bool = True,
    limit: int = 50,
) -> DayTradeBacktestSignalsResponse:
    conn = await repository._connect()
    try:
        job_id = await conn.fetchval("SELECT id FROM day_trade_backtest_jobs ORDER BY id DESC LIMIT 1")
        if job_id is None:
            rows_raw = []
        else:
            rows_raw = await conn.fetch(
                """
                SELECT id,job_id,signal_key,strategy_version,signal_class,
                       execution_assumption,included_primary,primary_exclusion_reason,
                       symbol,side,opened_at,closed_at,setup_type,entry_price,
                       trigger_price,stop_price,tp1,tp2,tp3,expected_rr,modeled_tp2_r,
                       expansion_score,direction_score,side_direction_score,
                       quality_score,setup_score,turnover_24h_usdc,modeled_spread_bps,
                       cost_bps,bars_observed,mfe_r,mae_r,exit_price,exit_reason,
                       gross_r,net_r,btc_structure_1h,btc_structure_4h,btc_volatility_regime
                FROM day_trade_backtest_signals
                WHERE job_id=$1
                  AND ($2='all' OR signal_class=$2)
                  AND ($3='both' OR side=$3)
                  AND ($4::text IS NULL OR symbol=$4)
                  AND (NOT $5 OR included_primary)
                ORDER BY opened_at DESC LIMIT $6
                """,
                int(job_id), signal_class, side,
                symbol.upper() if symbol else None, primary_only, limit,
            )
    except UndefinedTableError:
        rows_raw = []
    finally:
        await conn.close()
    items = [DayTradeBacktestSignal.model_validate(dict(row)) for row in rows_raw]
    return DayTradeBacktestSignalsResponse(
        generated_at=datetime.now(timezone.utc), count=len(items), items=items
    )


RadarRepository.get_day_trade_backtest_status = _get_day_trade_backtest_status
RadarRepository.get_day_trade_backtest_summary = _get_day_trade_backtest_summary
RadarRepository.get_day_trade_backtest_signals = _get_day_trade_backtest_signals


# ---------------------------------------------------------------------------
# v0.7.2 strict-gate and edge diagnostics
# ---------------------------------------------------------------------------


def _diag_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_diagnostic_job(job: dict[str, Any]) -> dict[str, Any]:
    for field in ("parameters", "universe", "warnings"):
        value = job.get(field) or ([] if field in {"universe", "warnings"} else {})
        if isinstance(value, str):
            value = json.loads(value)
        job[field] = value
    return job


async def _latest_diagnostic_job(
    repository: RadarRepository,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    conn = await repository._connect()
    try:
        job_raw = await conn.fetchrow(
            "SELECT * FROM day_trade_diagnostic_jobs ORDER BY id DESC LIMIT 1"
        )
        if not job_raw:
            return None, []
        job = _normalize_diagnostic_job(dict(job_raw))
        symbols_raw = await conn.fetch(
            """
            SELECT symbol,status,bars_fetched,evaluation_bars,event_count,
                   primary_event_count,strict_eligible_count,strict_trade_count,
                   last_error,started_at,completed_at
            FROM day_trade_diagnostic_symbols
            WHERE job_id=$1 ORDER BY status,symbol
            """,
            int(job["id"]),
        )
        return job, [dict(row) for row in symbols_raw]
    except UndefinedTableError:
        return None, []
    finally:
        await conn.close()


async def _get_day_trade_diagnostic_status(
    repository: RadarRepository,
) -> DayTradeDiagnosticStatusResponse:
    job, symbols = await _latest_diagnostic_job(repository)
    if job is None:
        return DayTradeDiagnosticStatusResponse(
            generated_at=datetime.now(timezone.utc),
            exists=False,
            job={},
            progress_pct=0.0,
            symbol_status=[],
            warnings=["Diagnostic job has not been initialized."],
        )
    total = int(job.get("total_symbols") or 0)
    completed = int(job.get("completed_symbols") or 0)
    failed = int(job.get("failed_symbols") or 0)
    progress = round((completed + failed) / total * 100.0, 2) if total else 0.0
    return DayTradeDiagnosticStatusResponse(
        generated_at=datetime.now(timezone.utc),
        exists=True,
        job=job,
        progress_pct=progress,
        symbol_status=symbols,
        warnings=list(job.get("warnings") or []),
    )


async def _fetch_diagnostic_events(
    repository: RadarRepository,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    conn = await repository._connect()
    try:
        job_raw = await conn.fetchrow(
            "SELECT * FROM day_trade_diagnostic_jobs ORDER BY id DESC LIMIT 1"
        )
        if not job_raw:
            return None, []
        job = _normalize_diagnostic_job(dict(job_raw))
        rows_raw = await conn.fetch(
            """
            SELECT id,job_id,event_key,strategy_version,symbol,side,opened_at,
                   dataset_split,universe_group,execution_assumption,
                   borrowability_status,included_primary,primary_exclusion_reason,
                   candidate_built,pass_tradeable,pass_side_execution_model,
                   pass_no_timeframe_conflict,pass_expansion,pass_direction,
                   pass_quality,pass_setup,pass_target_path,pass_rr,pass_volume_confirmation,
                   pass_score_gates,pass_strict_eligible,pass_strict_trade,
                   near_strict,first_failed_gate,setup_type,expected_rr,
                   expansion_score,direction_score,side_direction_score,
                   quality_score,setup_score,volume_ratio_5m,
                   turnover_24h_usdc,modeled_spread_bps,timeframe_conflict,
                   btc_structure_1h,btc_structure_4h,btc_volatility_regime,
                   base_horizon_hours,base_cost_bps,base_exit_reason,
                   base_gross_r,base_net_r,base_mfe_r,base_mae_r,sensitivity
            FROM day_trade_diagnostic_events
            WHERE job_id=$1 ORDER BY opened_at
            """,
            int(job["id"]),
        )
    except UndefinedTableError:
        return None, []
    finally:
        await conn.close()
    rows: list[dict[str, Any]] = []
    for raw in rows_raw:
        row = dict(raw)
        sensitivity = row.get("sensitivity") or {}
        if isinstance(sensitivity, str):
            sensitivity = json.loads(sensitivity)
        row["sensitivity"] = sensitivity
        rows.append(row)
    return job, rows


def _filter_diagnostic_rows(
    rows: list[dict[str, Any]],
    side: str,
    split: str,
    universe_group: str,
    primary_only: bool,
) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if (side == "both" or row.get("side") == side)
        and (split == "all" or row.get("dataset_split") == split)
        and (universe_group == "all" or row.get("universe_group") == universe_group)
        and (not primary_only or bool(row.get("included_primary")))
    ]


def _diagnostic_segment(key: str, rows: list[dict[str, Any]]) -> DiagnosticSegment:
    return DiagnosticSegment(
        key=key,
        trigger_count=len(rows),
        candidate_count=sum(1 for row in rows if row.get("candidate_built")),
        near_strict_count=sum(1 for row in rows if row.get("near_strict")),
        strict_eligible_count=sum(1 for row in rows if row.get("pass_strict_eligible")),
        strict_trade_count=sum(1 for row in rows if row.get("pass_strict_trade")),
    )


def _diagnostic_segments(
    rows: list[dict[str, Any]], field: str
) -> list[DiagnosticSegment]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "UNKNOWN")].append(row)
    return [
        _diagnostic_segment(key, values)
        for key, values in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)
    ]


async def _get_day_trade_gate_waterfall(
    repository: RadarRepository,
    side: str = "both",
    split: str = "all",
    universe_group: str = "all",
    primary_only: bool = False,
) -> DayTradeGateWaterfallResponse:
    job, all_rows = await _fetch_diagnostic_events(repository)
    rows = _filter_diagnostic_rows(
        all_rows, side, split, universe_group, primary_only
    )
    gate_fields = [
        ("TRIGGER_DETECTED", None),
        ("CANDIDATE_BUILT", "candidate_built"),
        ("LIQUIDITY_EXECUTION", "pass_tradeable"),
        ("SIDE_EXECUTION_MODEL", "pass_side_execution_model"),
        ("TIMEFRAME_ALIGNMENT", "pass_no_timeframe_conflict"),
        ("EXPANSION_55", "pass_expansion"),
        ("DIRECTION_35", "pass_direction"),
        ("QUALITY_65", "pass_quality"),
        ("SETUP_70", "pass_setup"),
        ("TARGET_PATH", "pass_target_path"),
        ("NET_RR_1_8", "pass_rr"),
        ("VOLUME_1_3X", "pass_volume_confirmation"),
        ("STRICT_TRADE", "pass_strict_trade"),
    ]
    active = list(rows)
    trigger_count = len(rows)
    waterfall: list[DiagnosticGateStep] = []
    for gate, field in gate_fields:
        reached = len(active)
        passed_rows = active if field is None else [row for row in active if bool(row.get(field))]
        passed = len(passed_rows)
        waterfall.append(DiagnosticGateStep(
            gate=gate,
            reached_count=reached,
            passed_count=passed,
            failed_count=reached - passed,
            pass_rate_from_reached_pct=(round(passed / reached * 100.0, 2) if reached else None),
            pass_rate_from_trigger_pct=(round(passed / trigger_count * 100.0, 2) if trigger_count else None),
        ))
        active = passed_rows

    failures: dict[str, int] = defaultdict(int)
    for row in rows:
        failures[str(row.get("first_failed_gate") or "UNKNOWN")] += 1
    first_failures = [
        DiagnosticCountGroup(
            key=key,
            count=count,
            pct_of_trigger=(round(count / trigger_count * 100.0, 2) if trigger_count else None),
        )
        for key, count in sorted(failures.items(), key=lambda item: item[1], reverse=True)
    ]
    warnings = [] if job is None else list(job.get("warnings") or [])
    return DayTradeGateWaterfallResponse(
        strategy_version=str((job or {}).get("strategy_version", "0.7.2")),
        generated_at=datetime.now(timezone.utc),
        job=job or {"status": "NOT_INITIALIZED"},
        requested_side=side,
        requested_split=split,
        requested_universe_group=universe_group,
        primary_only=primary_only,
        trigger_count=trigger_count,
        primary_count=sum(1 for row in rows if row.get("included_primary")),
        strict_eligible_count=sum(1 for row in rows if row.get("pass_strict_eligible")),
        strict_trade_count=sum(1 for row in rows if row.get("pass_strict_trade")),
        waterfall=waterfall,
        first_failures=first_failures,
        by_side=_diagnostic_segments(rows, "side"),
        by_split=_diagnostic_segments(rows, "dataset_split"),
        by_universe_group=_diagnostic_segments(rows, "universe_group"),
        methodology=[
            "The waterfall is sequential: each gate is evaluated only on events that passed every previous gate.",
            "STRICT_ELIGIBLE passes execution model, timeframe, score, structural target-path and net-RR gates; STRICT_TRADE additionally passes 5m volume confirmation.",
            "Technical short execution in technical_only mode is not historical borrowability evidence.",
            "Use all triggers for gate diagnosis; primary_only is optional and removes overlapping same-symbol/same-side triggers.",
        ],
        warnings=warnings,
    )


def _cohort_match(row: dict[str, Any], cohort: str) -> bool:
    if not row.get("candidate_built") or row.get("base_net_r") is None:
        return False
    if cohort == "ALL_VALID_CANDIDATES":
        return True
    if cohort == "LIQUID_EXECUTABLE":
        return bool(row.get("pass_tradeable") and row.get("pass_side_execution_model"))
    if cohort == "SCORE_GATES_PASS":
        return bool(row.get("pass_score_gates"))
    if cohort == "NEAR_STRICT":
        return bool(row.get("near_strict"))
    if cohort == "STRICT_ELIGIBLE":
        return bool(row.get("pass_strict_eligible"))
    if cohort == "STRICT_TRADE":
        return bool(row.get("pass_strict_trade"))
    return False


def _base_performance_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "exit_reason": row.get("base_exit_reason"),
            "net_r": row.get("base_net_r"),
            "mfe_r": row.get("base_mfe_r"),
            "mae_r": row.get("base_mae_r"),
        }
        for row in rows if row.get("base_net_r") is not None
    ]


def _sensitivity_performance_rows(
    rows: list[dict[str, Any]], horizon_hours: int, cost_bps: float
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    horizon_key = str(horizon_hours)
    cost_key = f"{cost_bps:g}"
    for row in rows:
        data = (row.get("sensitivity") or {}).get(horizon_key)
        if not data:
            continue
        net = (data.get("net_r_by_cost") or {}).get(cost_key)
        if net is None:
            continue
        output.append({
            "exit_reason": data.get("exit_reason"),
            "net_r": float(net),
            "mfe_r": _diag_float(data.get("mfe_r")),
            "mae_r": _diag_float(data.get("mae_r")),
        })
    return output


def _diagnostic_group_performance(
    rows: list[dict[str, Any]], field: str
) -> list[BacktestGroupStats]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "UNKNOWN")].append(row)
    return [
        BacktestGroupStats(key=key, stats=_backtest_aggregate(_base_performance_rows(values)))
        for key, values in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)
    ]


def _score_band(value: float, kind: str) -> str:
    if kind == "setup":
        if value >= 80: return "80+"
        if value >= 70: return "70-79.99"
        if value >= 65: return "65-69.99"
        if value >= 55: return "55-64.99"
        return "<55"
    if kind == "expansion":
        if value >= 70: return "70+"
        if value >= 55: return "55-69.99"
        if value >= 40: return "40-54.99"
        return "<40"
    if kind == "direction":
        if value >= 50: return "50+"
        if value >= 35: return "35-49.99"
        if value >= 20: return "20-34.99"
        return "<20"
    if kind == "quality":
        if value >= 80: return "80+"
        if value >= 65: return "65-79.99"
        if value >= 50: return "50-64.99"
        return "<50"
    return "UNKNOWN"


def _excursion_thresholds(
    rows: list[dict[str, Any]], field: str
) -> list[ExcursionThreshold]:
    values = [_diag_float(row.get(field)) for row in rows if row.get(field) is not None]
    sample = len(values)
    thresholds = [0.25, 0.5, 0.75, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5]
    return [
        ExcursionThreshold(
            threshold_r=threshold,
            reached_count=sum(1 for value in values if value >= threshold),
            reached_pct=(
                round(sum(1 for value in values if value >= threshold) / sample * 100.0, 2)
                if sample else None
            ),
        )
        for threshold in thresholds
    ]


async def _get_day_trade_edge_diagnostics(
    repository: RadarRepository,
    cohort: str = "NEAR_STRICT",
    side: str = "both",
    split: str = "all",
    universe_group: str = "all",
    primary_only: bool = True,
) -> DayTradeEdgeDiagnosticsResponse:
    job, all_rows = await _fetch_diagnostic_events(repository)
    filtered = _filter_diagnostic_rows(
        all_rows, side, split, universe_group, primary_only
    )
    valid = [row for row in filtered if row.get("candidate_built") and row.get("base_net_r") is not None]
    cohort_keys = [
        "ALL_VALID_CANDIDATES",
        "LIQUID_EXECUTABLE",
        "SCORE_GATES_PASS",
        "NEAR_STRICT",
        "STRICT_ELIGIBLE",
        "STRICT_TRADE",
    ]
    cohort_performance: list[DiagnosticCohortStats] = []
    cohort_rows: dict[str, list[dict[str, Any]]] = {}
    for key in cohort_keys:
        values = [row for row in valid if _cohort_match(row, key)]
        cohort_rows[key] = values
        cohort_performance.append(DiagnosticCohortStats(
            key=key,
            count=len(values),
            stats=_backtest_aggregate(_base_performance_rows(values)),
        ))
    selected = cohort_rows.get(cohort, [])
    parameters = (job or {}).get("parameters") or {}
    horizons = parameters.get("horizon_hours") or [2, 4, 8]
    costs = parameters.get("cost_bps") or [0, 10, 20, 30]
    sensitivity = [
        DiagnosticSensitivityStats(
            horizon_hours=int(hours),
            cost_bps=float(cost),
            stats=_backtest_aggregate(
                _sensitivity_performance_rows(selected, int(hours), float(cost))
            ),
        )
        for hours in horizons for cost in costs
    ]

    for row in selected:
        row["btc_regime_key"] = (
            f"1H:{row.get('btc_structure_1h') or 'UNKNOWN'}|"
            f"4H:{row.get('btc_structure_4h') or 'UNKNOWN'}|"
            f"VOL:{row.get('btc_volatility_regime') or 'UNKNOWN'}"
        )
        row["setup_score_band"] = _score_band(_diag_float(row.get("setup_score")), "setup")
        row["expansion_score_band"] = _score_band(_diag_float(row.get("expansion_score")), "expansion")
        row["direction_score_band"] = _score_band(_diag_float(row.get("side_direction_score")), "direction")
        row["quality_score_band"] = _score_band(_diag_float(row.get("quality_score")), "quality")

    warnings = [] if job is None else list(job.get("warnings") or [])
    base_horizon = int(parameters.get("base_horizon_hours") or 8)
    base_cost = float(parameters.get("base_cost_bps") or 20.0)
    return DayTradeEdgeDiagnosticsResponse(
        strategy_version=str((job or {}).get("strategy_version", "0.7.2")),
        generated_at=datetime.now(timezone.utc),
        job=job or {"status": "NOT_INITIALIZED"},
        selected_cohort=cohort,
        requested_side=side,
        requested_split=split,
        requested_universe_group=universe_group,
        primary_only=primary_only,
        base_horizon_hours=base_horizon,
        base_cost_bps=base_cost,
        selected_sample=len(selected),
        selected_performance=_backtest_aggregate(_base_performance_rows(selected)),
        cohort_performance=cohort_performance,
        sensitivity=sensitivity,
        by_side=_diagnostic_group_performance(selected, "side"),
        by_split=_diagnostic_group_performance(selected, "dataset_split"),
        by_universe_group=_diagnostic_group_performance(selected, "universe_group"),
        by_btc_regime=_diagnostic_group_performance(selected, "btc_regime_key"),
        by_setup_score_band=_diagnostic_group_performance(selected, "setup_score_band"),
        by_expansion_score_band=_diagnostic_group_performance(selected, "expansion_score_band"),
        by_direction_score_band=_diagnostic_group_performance(selected, "direction_score_band"),
        by_quality_score_band=_diagnostic_group_performance(selected, "quality_score_band"),
        mfe_thresholds=_excursion_thresholds(selected, "base_mfe_r"),
        mae_thresholds=_excursion_thresholds(selected, "base_mae_r"),
        methodology=[
            "Development and validation are chronological; validation must not be used to select rules.",
            "The base result uses the configured maximum horizon and base cost; sensitivity changes one horizon/cost assumption at a time.",
            "Cohort comparisons are diagnostic. The best in-sample cell must not be promoted without untouched validation and prospective confirmation.",
            "MFE/MAE thresholds describe excursion distributions; they do not by themselves define an optimal stop or target.",
        ],
        warnings=warnings,
    )


RadarRepository.get_day_trade_diagnostic_status = _get_day_trade_diagnostic_status
RadarRepository.get_day_trade_gate_waterfall = _get_day_trade_gate_waterfall
RadarRepository.get_day_trade_edge_diagnostics = _get_day_trade_edge_diagnostics
