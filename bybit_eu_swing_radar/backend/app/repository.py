import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import asyncpg
from asyncpg.exceptions import UndefinedTableError

from app.config import settings
from app.models import (
    DayTradeCandidate,
    DayTradeJournalSignal,
    DayTradeJournalSignalsResponse,
    DayTradeJournalSummaryResponse,
    DayTradeScanResponse,
    DayTradeTopCandidatesResponse,
    JournalAggregate,
    JournalGroupStats,
    MarketRegime,
    MomentumResponse,
    ScanResponse,
    Setup,
    TopCandidatesResponse,
    WatchlistResponse,
)


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


async def _get_day_trade_top_candidates(
    repository: RadarRepository,
    limit: int = 3,
    include_watchlist: bool = True,
) -> DayTradeTopCandidatesResponse | None:
    payload = await repository.get_cache("day_trade_scan")
    if payload is None:
        return None
    scan = DayTradeScanResponse.model_validate(payload)
    return DayTradeTopCandidatesResponse(
        data_as_of=scan.data_as_of,
        data_as_of_budapest=scan.data_as_of_budapest,
        data_quality=scan.data_quality,
        market_regime=scan.market_regime,
        requested_limit=limit,
        strict_long_count=len(scan.strict_longs),
        strict_short_count=len(scan.strict_shorts),
        strict_longs=scan.strict_longs[:limit],
        strict_shorts=scan.strict_shorts[:limit],
        watch_only_longs=scan.watch_only_longs[:limit] if include_watchlist else [],
        watch_only_shorts=scan.watch_only_shorts[:limit] if include_watchlist else [],
        coverage=scan.coverage,
        assumptions=scan.assumptions,
        notes=scan.notes + [
            "Do not fill missing strict slots with WATCH_ONLY items.",
            "Only decision=TRADE with state=TRIGGERED is an immediately actionable day-trade setup.",
        ],
    )


async def _get_day_trade_setup(
    repository: RadarRepository,
    symbol: str,
) -> DayTradeCandidate | None:
    payload = await repository.get_cache(f"day_trade_setup:{symbol.upper()}")
    return DayTradeCandidate.model_validate(payload) if payload is not None else None


async def _get_day_trade_status(
    repository: RadarRepository,
) -> dict | None:
    return await repository.get_cache("day_trade_status")


RadarRepository.get_day_trade_scan = _get_day_trade_scan
RadarRepository.get_day_trade_top_candidates = _get_day_trade_top_candidates
RadarRepository.get_day_trade_setup = _get_day_trade_setup
RadarRepository.get_day_trade_status = _get_day_trade_status



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
              AND ($2 = 'all' OR signal_class = $2)
            ORDER BY opened_at DESC
            """,
            days,
            signal_class,
        )
        strict_closed = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM day_trade_signal_journal
                WHERE signal_class = 'STRICT'
                  AND status = 'CLOSED'
                  AND opened_at >= NOW() - ($1::int * INTERVAL '1 day')
                """,
                days,
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
            ORDER BY run_at DESC
            LIMIT 1
            """
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
        strategy_version="0.6.0",
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
            WHERE ($1 = 'all' OR status = $1)
              AND ($2 = 'all' OR signal_class = $2)
              AND ($3::text IS NULL OR symbol = UPPER($3))
            ORDER BY opened_at DESC
            LIMIT $4
            """,
            status,
            signal_class,
            symbol,
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
