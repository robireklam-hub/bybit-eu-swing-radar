"""Prospective day-trade signal journal for the Bybit EU Trading Radar.

The journal is intentionally conservative:
- only closed 5m trigger events are recorded;
- STRICT and SHADOW samples are separated;
- duplicate signals are prevented by symbol/side/trigger-bar fingerprint;
- the primary outcome is TP2 versus stop within an 8-hour horizon;
- if stop and TP2 are touched in the same 5m candle, stop is assumed first;
- net R subtracts the configured round-trip cost model.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg


STRATEGY_VERSION = "0.7.5"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float environment variable {name}={raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer environment variable {name}={raw!r}") from exc


JOURNAL_ENABLED = _env_bool("DAY_JOURNAL_ENABLED", True)
JOURNAL_SHADOW_ENABLED = _env_bool("DAY_JOURNAL_SHADOW_ENABLED", True)
JOURNAL_HORIZON_HOURS = min(max(_env_int("DAY_JOURNAL_HORIZON_HOURS", 8), 1), 24)
JOURNAL_SHADOW_MIN_SETUP = _env_float("DAY_JOURNAL_SHADOW_MIN_SETUP", 65.0)
JOURNAL_SHADOW_MIN_RR = _env_float("DAY_JOURNAL_SHADOW_MIN_RR", 1.2)
JOURNAL_COST_BPS = _env_float("DAY_JOURNAL_COST_BPS", 20.0)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS day_trade_signal_journal (
    id BIGSERIAL PRIMARY KEY,
    signal_key TEXT NOT NULL UNIQUE,
    strategy_version TEXT NOT NULL,
    signal_class TEXT NOT NULL CHECK (signal_class IN ('STRICT', 'SHADOW')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
    opened_at TIMESTAMPTZ NOT NULL,
    signal_bar_start TIMESTAMPTZ NOT NULL,
    last_evaluated_bar_start TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    setup_type TEXT NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    trigger_price DOUBLE PRECISION NOT NULL,
    entry_zone_low DOUBLE PRECISION NOT NULL,
    entry_zone_high DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION NOT NULL,
    tp1 DOUBLE PRECISION NOT NULL,
    tp2 DOUBLE PRECISION NOT NULL,
    tp3 DOUBLE PRECISION NOT NULL,
    risk_per_unit DOUBLE PRECISION NOT NULL,
    modeled_tp2_r DOUBLE PRECISION NOT NULL,
    entry_deviation_bps DOUBLE PRECISION NOT NULL,
    entry_within_zone BOOLEAN NOT NULL,
    expected_rr DOUBLE PRECISION NOT NULL,
    expansion_score DOUBLE PRECISION NOT NULL,
    direction_score DOUBLE PRECISION NOT NULL,
    side_direction_score DOUBLE PRECISION NOT NULL,
    quality_score DOUBLE PRECISION NOT NULL,
    setup_score DOUBLE PRECISION NOT NULL,
    spread_bps DOUBLE PRECISION,
    turnover_24h_usdc DOUBLE PRECISION,
    volume_ratio_5m DOUBLE PRECISION,
    volume_ratio_15m DOUBLE PRECISION,
    atr_5m DOUBLE PRECISION,
    atr_15m DOUBLE PRECISION,
    timeframe_conflict BOOLEAN NOT NULL DEFAULT FALSE,
    data_quality TEXT NOT NULL,
    cost_bps DOUBLE PRECISION NOT NULL,
    cost_r DOUBLE PRECISION NOT NULL,
    market_regime JSONB NOT NULL DEFAULT '{}'::jsonb,
    derivatives JSONB NOT NULL DEFAULT '{}'::jsonb,
    candidate_payload JSONB NOT NULL,
    bars_observed INTEGER NOT NULL DEFAULT 0,
    mfe_r DOUBLE PRECISION NOT NULL DEFAULT 0,
    mae_r DOUBLE PRECISION NOT NULL DEFAULT 0,
    tp1_hit_at TIMESTAMPTZ,
    tp2_hit_at TIMESTAMPTZ,
    tp3_hit_at TIMESTAMPTZ,
    stop_hit_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    exit_price DOUBLE PRECISION,
    exit_reason TEXT,
    gross_r DOUBLE PRECISION,
    net_r DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_day_journal_open
    ON day_trade_signal_journal (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_day_journal_opened
    ON day_trade_signal_journal (opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_day_journal_symbol_side
    ON day_trade_signal_journal (symbol, side, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_day_journal_class
    ON day_trade_signal_journal (signal_class, opened_at DESC);

CREATE TABLE IF NOT EXISTS day_trade_journal_runs (
    id BIGSERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ NOT NULL,
    strategy_version TEXT NOT NULL,
    data_quality TEXT NOT NULL,
    coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
    strict_long_count INTEGER NOT NULL DEFAULT 0,
    strict_short_count INTEGER NOT NULL DEFAULT 0,
    triggered_trade_count INTEGER NOT NULL DEFAULT 0,
    shadow_trigger_count INTEGER NOT NULL DEFAULT 0,
    new_signal_count INTEGER NOT NULL DEFAULT 0,
    evaluated_signal_count INTEGER NOT NULL DEFAULT 0,
    closed_signal_count INTEGER NOT NULL DEFAULT 0,
    active_signal_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_day_journal_runs_run_at
    ON day_trade_journal_runs (run_at DESC);
"""


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bar_start(bar: Any) -> datetime:
    return datetime.fromtimestamp(int(bar.start_ms) / 1000.0, tz=timezone.utc)


def _signal_class(candidate: dict[str, Any]) -> str | None:
    trigger = candidate.get("trigger") or {}
    if not bool(trigger.get("triggered")):
        return None

    if (
        candidate.get("category") == "STRICT"
        and candidate.get("state") == "TRIGGERED"
        and candidate.get("decision") == "TRADE"
    ):
        return "STRICT"

    if not JOURNAL_SHADOW_ENABLED:
        return None

    side = candidate.get("side")
    executable_side = bool(candidate.get("tradeable")) and (
        side == "long" or bool(candidate.get("shortable"))
    )
    if (
        candidate.get("category") == "WATCH_ONLY"
        and candidate.get("watch_bucket") == "NEAR_STRICT"
        and executable_side
        and _as_float(candidate.get("setup_score")) >= JOURNAL_SHADOW_MIN_SETUP
        and _as_float(candidate.get("expected_rr")) >= JOURNAL_SHADOW_MIN_RR
    ):
        return "SHADOW"
    return None


def _signal_key(
    symbol: str,
    side: str,
    signal_bar_start: datetime,
) -> str:
    raw = (
        f"{STRATEGY_VERSION}|{symbol.upper()}|{side}|"
        f"{signal_bar_start.isoformat()}"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"day:{digest}"


def _valid_geometry(
    side: str,
    entry: float,
    stop: float,
    tp2: float,
) -> bool:
    if side == "long":
        return stop < entry < tp2
    return tp2 < entry < stop


def build_signal_record(
    candidate: dict[str, Any],
    bars_5m: list[Any],
    market_regime: dict[str, Any],
) -> dict[str, Any] | None:
    signal_class = _signal_class(candidate)
    if signal_class is None or not bars_5m:
        return None

    latest_bar = bars_5m[-1]
    signal_bar_start = _bar_start(latest_bar)
    opened_at = signal_bar_start + timedelta(minutes=5)
    expires_at = opened_at + timedelta(hours=JOURNAL_HORIZON_HOURS)

    side = str(candidate.get("side"))
    entry = _as_float(latest_bar.close)
    stop = _as_float(candidate.get("stop"))
    targets = candidate.get("targets") or []
    if len(targets) < 3:
        return None
    tp1, tp2, tp3 = (_as_float(targets[0]), _as_float(targets[1]), _as_float(targets[2]))
    if entry <= 0 or stop <= 0 or not _valid_geometry(side, entry, stop, tp2):
        return None

    risk = abs(entry - stop)
    if risk <= 0:
        return None

    trigger = candidate.get("trigger") or {}
    trigger_event_start_ms = int(_as_float(trigger.get("event_bar_start_ms"), 0.0))
    signal_key_bar_start = (
        datetime.fromtimestamp(trigger_event_start_ms / 1000.0, tz=timezone.utc)
        if trigger_event_start_ms > 0
        else signal_bar_start
    )
    entry_zone = candidate.get("entry_zone") or {}
    metrics = candidate.get("metrics") or {}
    cost_price = entry * JOURNAL_COST_BPS / 10_000.0
    cost_r = cost_price / risk
    trigger_price = _as_float(trigger.get("price"), entry)
    zone_low = _as_float(entry_zone.get("low"), entry)
    zone_high = _as_float(entry_zone.get("high"), entry)
    entry_deviation_bps = (
        abs(entry - trigger_price) / trigger_price * 10_000.0
        if trigger_price > 0
        else 0.0
    )

    return {
        "signal_key": _signal_key(str(candidate["symbol"]), side, signal_key_bar_start),
        "strategy_version": STRATEGY_VERSION,
        "signal_class": signal_class,
        "symbol": str(candidate["symbol"]).upper(),
        "side": side,
        "opened_at": opened_at,
        "signal_bar_start": signal_bar_start,
        "expires_at": expires_at,
        "setup_type": str(candidate.get("setup_type", "UNKNOWN")),
        "entry_price": entry,
        "trigger_price": trigger_price,
        "entry_zone_low": zone_low,
        "entry_zone_high": zone_high,
        "stop_price": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_per_unit": risk,
        "modeled_tp2_r": abs(tp2 - entry) / risk,
        "entry_deviation_bps": entry_deviation_bps,
        "entry_within_zone": zone_low <= entry <= zone_high,
        "expected_rr": _as_float(candidate.get("expected_rr")),
        "expansion_score": _as_float(candidate.get("expansion_score")),
        "direction_score": _as_float(candidate.get("direction_score")),
        "side_direction_score": _as_float(candidate.get("side_direction_score")),
        "quality_score": _as_float(candidate.get("quality_score")),
        "setup_score": _as_float(candidate.get("setup_score")),
        "spread_bps": _as_float(metrics.get("spread_bps")),
        "turnover_24h_usdc": _as_float(metrics.get("turnover_24h_usdc")),
        "volume_ratio_5m": _as_float(metrics.get("volume_ratio_5m")),
        "volume_ratio_15m": _as_float(metrics.get("volume_ratio_15m")),
        "atr_5m": _as_float(metrics.get("atr_5m")),
        "atr_15m": _as_float(metrics.get("atr_15m")),
        "timeframe_conflict": bool(candidate.get("timeframe_conflict")),
        "data_quality": str(candidate.get("data_quality", "PARTIAL")),
        "cost_bps": JOURNAL_COST_BPS,
        "cost_r": cost_r,
        "market_regime": market_regime,
        "derivatives": candidate.get("derivatives") or {},
        "candidate_payload": candidate,
    }


async def ensure_journal_schema(connection: asyncpg.Connection) -> None:
    await connection.execute(SCHEMA_SQL)


async def insert_signal(
    connection: asyncpg.Connection,
    record: dict[str, Any],
) -> bool:
    row = await connection.fetchrow(
        """
        INSERT INTO day_trade_signal_journal (
            signal_key, strategy_version, signal_class, symbol, side,
            opened_at, signal_bar_start, expires_at, setup_type,
            entry_price, trigger_price, entry_zone_low, entry_zone_high,
            stop_price, tp1, tp2, tp3, risk_per_unit,
            modeled_tp2_r, entry_deviation_bps, entry_within_zone, expected_rr,
            expansion_score, direction_score, side_direction_score,
            quality_score, setup_score, spread_bps, turnover_24h_usdc,
            volume_ratio_5m, volume_ratio_15m, atr_5m, atr_15m,
            timeframe_conflict, data_quality, cost_bps, cost_r,
            market_regime, derivatives, candidate_payload
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8, $9,
            $10, $11, $12, $13,
            $14, $15, $16, $17, $18,
            $19, $20, $21, $22,
            $23, $24, $25,
            $26, $27, $28, $29,
            $30, $31, $32, $33,
            $34, $35, $36, $37,
            $38::jsonb, $39::jsonb, $40::jsonb
        )
        ON CONFLICT (signal_key) DO NOTHING
        RETURNING id
        """,
        record["signal_key"],
        record["strategy_version"],
        record["signal_class"],
        record["symbol"],
        record["side"],
        record["opened_at"],
        record["signal_bar_start"],
        record["expires_at"],
        record["setup_type"],
        record["entry_price"],
        record["trigger_price"],
        record["entry_zone_low"],
        record["entry_zone_high"],
        record["stop_price"],
        record["tp1"],
        record["tp2"],
        record["tp3"],
        record["risk_per_unit"],
        record["modeled_tp2_r"],
        record["entry_deviation_bps"],
        record["entry_within_zone"],
        record["expected_rr"],
        record["expansion_score"],
        record["direction_score"],
        record["side_direction_score"],
        record["quality_score"],
        record["setup_score"],
        record["spread_bps"],
        record["turnover_24h_usdc"],
        record["volume_ratio_5m"],
        record["volume_ratio_15m"],
        record["atr_5m"],
        record["atr_15m"],
        record["timeframe_conflict"],
        record["data_quality"],
        record["cost_bps"],
        record["cost_r"],
        json.dumps(record["market_regime"], ensure_ascii=False),
        json.dumps(record["derivatives"], ensure_ascii=False),
        json.dumps(record["candidate_payload"], ensure_ascii=False, default=str),
    )
    return row is not None


def _gross_r(side: str, entry: float, exit_price: float, risk: float) -> float:
    multiplier = 1.0 if side == "long" else -1.0
    return multiplier * (exit_price - entry) / risk


def evaluate_signal_row(
    signal: dict[str, Any],
    bars_5m: list[Any],
    now: datetime,
) -> dict[str, Any] | None:
    """Evaluate one open signal using only bars after the trigger-bar close."""
    if not bars_5m:
        return None

    side = str(signal["side"])
    entry = _as_float(signal["entry_price"])
    stop = _as_float(signal["stop_price"])
    tp1 = _as_float(signal["tp1"])
    tp2 = _as_float(signal["tp2"])
    tp3 = _as_float(signal["tp3"])
    risk = _as_float(signal["risk_per_unit"])
    cost_r = _as_float(signal["cost_r"])
    expires_at = signal["expires_at"]
    signal_bar_start = signal["signal_bar_start"]
    last_evaluated = signal.get("last_evaluated_bar_start") or signal_bar_start

    mfe = _as_float(signal.get("mfe_r"))
    mae = _as_float(signal.get("mae_r"))
    bars_observed = int(signal.get("bars_observed") or 0)
    tp1_hit_at = signal.get("tp1_hit_at")
    tp2_hit_at = signal.get("tp2_hit_at")
    tp3_hit_at = signal.get("tp3_hit_at")
    stop_hit_at = signal.get("stop_hit_at")

    new_bars = [
        bar
        for bar in bars_5m
        if _bar_start(bar) > last_evaluated and _bar_start(bar) < expires_at
    ]
    new_bars.sort(key=lambda bar: bar.start_ms)

    closed_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    last_processed: datetime | None = None

    for bar in new_bars:
        bar_time = _bar_start(bar)
        last_processed = bar_time
        bars_observed += 1

        if side == "long":
            favorable_r = max(0.0, (_as_float(bar.high) - entry) / risk)
            adverse_r = max(0.0, (entry - _as_float(bar.low)) / risk)
            stop_hit = _as_float(bar.low) <= stop
            tp1_hit = _as_float(bar.high) >= tp1
            tp2_hit = _as_float(bar.high) >= tp2
            tp3_hit = _as_float(bar.high) >= tp3
        else:
            favorable_r = max(0.0, (entry - _as_float(bar.low)) / risk)
            adverse_r = max(0.0, (_as_float(bar.high) - entry) / risk)
            stop_hit = _as_float(bar.high) >= stop
            tp1_hit = _as_float(bar.low) <= tp1
            tp2_hit = _as_float(bar.low) <= tp2
            tp3_hit = _as_float(bar.low) <= tp3

        if stop_hit:
            mae = max(mae, 1.0)
            stop_hit_at = bar_time
            closed_at = bar_time + timedelta(minutes=5)
            exit_price = stop
            exit_reason = (
                "AMBIGUOUS_STOP_FIRST"
                if tp1_hit or tp2_hit or tp3_hit
                else "STOP"
            )
            break

        mae = max(mae, adverse_r)
        if tp1_hit and tp1_hit_at is None:
            tp1_hit_at = bar_time
        if tp2_hit and tp2_hit_at is None:
            tp2_hit_at = bar_time
        if tp3_hit and tp3_hit_at is None:
            tp3_hit_at = bar_time

        if tp2_hit:
            mfe = max(mfe, abs(tp2 - entry) / risk)
            closed_at = bar_time + timedelta(minutes=5)
            exit_price = tp2
            exit_reason = "TP2"
            break

        mfe = max(mfe, favorable_r)

    # Close at the last available candle inside the horizon once the horizon passed.
    if exit_reason is None and now >= expires_at:
        eligible = [
            bar
            for bar in bars_5m
            if signal_bar_start < _bar_start(bar) < expires_at
        ]
        if eligible:
            final_bar = max(eligible, key=lambda bar: bar.start_ms)
            final_time = _bar_start(final_bar)
            if last_processed is None or final_time > last_processed:
                last_processed = final_time
            exit_price = _as_float(final_bar.close)
            closed_at = expires_at
            exit_reason = "TIME_EXIT"

    if not new_bars and exit_reason is None:
        return None

    gross_r = None
    net_r = None
    status = "OPEN"
    if exit_reason is not None and exit_price is not None:
        gross_r = _gross_r(side, entry, exit_price, risk)
        net_r = gross_r - cost_r
        status = "CLOSED"

    return {
        "status": status,
        "last_evaluated_bar_start": last_processed or last_evaluated,
        "bars_observed": bars_observed,
        "mfe_r": mfe,
        "mae_r": mae,
        "tp1_hit_at": tp1_hit_at,
        "tp2_hit_at": tp2_hit_at,
        "tp3_hit_at": tp3_hit_at,
        "stop_hit_at": stop_hit_at,
        "closed_at": closed_at,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "gross_r": gross_r,
        "net_r": net_r,
    }


async def update_signal(
    connection: asyncpg.Connection,
    signal_id: int,
    update: dict[str, Any],
) -> None:
    await connection.execute(
        """
        UPDATE day_trade_signal_journal
        SET status = $2,
            last_evaluated_bar_start = $3,
            bars_observed = $4,
            mfe_r = $5,
            mae_r = $6,
            tp1_hit_at = $7,
            tp2_hit_at = $8,
            tp3_hit_at = $9,
            stop_hit_at = $10,
            closed_at = $11,
            exit_price = $12,
            exit_reason = $13,
            gross_r = $14,
            net_r = $15,
            updated_at = NOW()
        WHERE id = $1
        """,
        signal_id,
        update["status"],
        update["last_evaluated_bar_start"],
        update["bars_observed"],
        update["mfe_r"],
        update["mae_r"],
        update["tp1_hit_at"],
        update["tp2_hit_at"],
        update["tp3_hit_at"],
        update["stop_hit_at"],
        update["closed_at"],
        update["exit_price"],
        update["exit_reason"],
        update["gross_r"],
        update["net_r"],
    )


async def persist_day_journal(
    connection: asyncpg.Connection,
    candidates: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[Any]],
    scan: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    if not JOURNAL_ENABLED:
        return {
            "enabled": False,
            "strategy_version": STRATEGY_VERSION,
            "new_signals": 0,
            "evaluated_signals": 0,
            "closed_signals": 0,
            "active_signals": 0,
        }

    await ensure_journal_schema(connection)
    now = datetime.now(timezone.utc)
    market_regime = scan.get("market_regime") or {}

    new_signals = 0
    strict_triggered = 0
    shadow_triggered = 0
    for candidate in candidates:
        record = build_signal_record(
            candidate,
            bars_by_symbol.get(str(candidate.get("symbol", "")).upper(), []),
            market_regime,
        )
        if record is None:
            continue
        if record["signal_class"] == "STRICT":
            strict_triggered += 1
        else:
            shadow_triggered += 1
        if await insert_signal(connection, record):
            new_signals += 1

    open_rows = await connection.fetch(
        """
        SELECT *
        FROM day_trade_signal_journal
        WHERE status = 'OPEN' AND strategy_version = $1
        ORDER BY opened_at ASC
        """,
        STRATEGY_VERSION,
    )

    evaluated = 0
    closed = 0
    for row in open_rows:
        signal = dict(row)
        bars = bars_by_symbol.get(str(signal["symbol"]).upper(), [])
        update = evaluate_signal_row(signal, bars, now)
        if update is None:
            continue
        await update_signal(connection, int(signal["id"]), update)
        evaluated += 1
        if update["status"] == "CLOSED":
            closed += 1

    active = int(
        await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM day_trade_signal_journal
            WHERE status = 'OPEN' AND strategy_version = $1
            """,
            STRATEGY_VERSION,
        )
        or 0
    )

    coverage = scan.get("coverage") or {}
    worker = status.get("worker") or {}
    await connection.execute(
        """
        INSERT INTO day_trade_journal_runs (
            run_at, strategy_version, data_quality, coverage,
            strict_long_count, strict_short_count,
            triggered_trade_count, shadow_trigger_count,
            new_signal_count, evaluated_signal_count,
            closed_signal_count, active_signal_count
        )
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
        now,
        STRATEGY_VERSION,
        str(scan.get("data_quality", "PARTIAL")),
        json.dumps(coverage, ensure_ascii=False, default=str),
        int(worker.get("strict_long_candidates", 0)),
        int(worker.get("strict_short_candidates", 0)),
        strict_triggered,
        shadow_triggered,
        new_signals,
        evaluated,
        closed,
        active,
    )

    return {
        "enabled": True,
        "strategy_version": STRATEGY_VERSION,
        "horizon_hours": JOURNAL_HORIZON_HOURS,
        "cost_bps": JOURNAL_COST_BPS,
        "strict_triggered": strict_triggered,
        "shadow_triggered": shadow_triggered,
        "new_signals": new_signals,
        "evaluated_signals": evaluated,
        "closed_signals": closed,
        "active_signals": active,
        "evaluation_policy": "TP2 vs STOP, same-candle ambiguity=STOP_FIRST, expiry=TIME_EXIT",
    }