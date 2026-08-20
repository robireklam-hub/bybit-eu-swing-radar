"""Preregistered forward microstructure-to-signal alignment.

The feature extractor is intentionally label-blind: its database query does not
select journal outcome/status/net-R fields. It only uses microstructure buckets
strictly before a signal's opened_at timestamp, preventing post-entry leakage.
Live strategy/scoring/eligibility/execution are untouched.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping


class _AsyncpgProxy:
    """Load the DB driver only when the DB-backed loader actually needs it."""

    def __getattr__(self, name: str) -> Any:
        import asyncpg as module

        return getattr(module, name)


# Preserve the historical monkeypatch/public module surface without making
# label-blind feature-contract imports depend on the production DB driver.
asyncpg = _AsyncpgProxy()

SPEC_VERSION = "microstructure-forward-alignment-v1"
PREREGISTERED_STRATEGY_VERSION = "0.7.3"
LOOKBACK_SECONDS = 60
WINDOW_SECONDS = (5, 15, 60)
MIN_SIGNAL_SAMPLE_TOTAL = 60
MIN_SIGNAL_SAMPLE_PER_SYMBOL = 10

HYPOTHESES = (
    {
        "id": "H1_FLOW_BOOK_CONCORDANCE",
        "feature": "flow_book_concordance_60s",
        "expected_direction": "positive",
        "mechanism": "Side-adjusted aggressive taker flow and visible L10 book imbalance agreeing should be associated with better subsequent signal outcomes.",
    },
    {
        "id": "H2_MICROPRICE_DISPLACEMENT",
        "feature": "side_microprice_displacement_bps_15s",
        "expected_direction": "positive",
        "mechanism": "Microprice displaced in the signal direction immediately before entry should indicate near-touch pressure supportive of continuation.",
    },
    {
        "id": "H3_BOOK_CHURN_PRESSURE",
        "feature": "side_book_pressure_ratio_60s",
        "expected_direction": "positive",
        "mechanism": "Bid additions plus ask removals versus the opposite book churn, side-adjusted for signal direction, should capture visible liquidity pressure.",
    },
    {
        "id": "H4_SPREAD_COST",
        "feature": "spread_bps_mean_15s",
        "expected_direction": "negative",
        "mechanism": "Wider spot spread immediately before entry should be associated with worse net outcomes after costs.",
    },
)

# Deliberately excludes status/closed_at/exit_reason/gross_r/net_r/outcome columns.
# The v1 study was preregistered while the live day strategy was v0.7.3; keep
# this prospective cohort version-isolated even after later live strategy bumps.
ALIGNMENT_SQL = """
SELECT
    j.id AS signal_id,
    j.signal_key,
    j.strategy_version,
    j.signal_class,
    j.symbol,
    j.side,
    j.opened_at,
    j.setup_type,
    b.bucket_start,
    b.bucket_seconds,
    b.signed_quote_flow,
    b.total_quote_volume,
    b.spread_bps,
    b.mid,
    b.microprice,
    b.imbalance_10,
    b.imbalance_50,
    b.bid_added_quote,
    b.bid_removed_quote,
    b.ask_added_quote,
    b.ask_removed_quote,
    b.book_ready,
    b.book_message_count
FROM day_trade_signal_journal AS j
JOIN microstructure_buckets AS b
  ON b.symbol = j.symbol
 AND b.bucket_start >= j.opened_at - INTERVAL '60 seconds'
 AND b.bucket_start < j.opened_at
WHERE j.symbol = ANY($1::text[])
  AND j.opened_at >= $2
  AND j.opened_at < $3
  AND j.strategy_version = $4
ORDER BY j.id, b.bucket_start
"""


def alignment_spec() -> dict[str, Any]:
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "spec_version": SPEC_VERSION,
        "preregistered_strategy_version": PREREGISTERED_STRATEGY_VERSION,
        "strategy_version_isolated": True,
        "label_blind": True,
        "post_signal_data_used": False,
        "lookback_seconds": LOOKBACK_SECONDS,
        "windows_seconds": list(WINDOW_SECONDS),
        "minimum_signal_sample": {
            "total": MIN_SIGNAL_SAMPLE_TOTAL,
            "per_symbol": MIN_SIGNAL_SAMPLE_PER_SYMBOL,
        },
        "primary_future_label": "journal.net_r_after_costs",
        "analysis_rule": "No threshold search on the forward sample. Evaluate preregistered continuous feature directions first; any threshold/model discovered later requires a subsequent untouched validation period.",
        "hypotheses": list(HYPOTHESES),
    }


def _f(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _side_sign(side: str) -> float:
    if side == "long":
        return 1.0
    if side == "short":
        return -1.0
    raise ValueError(f"unsupported signal side: {side!r}")


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _window_features(rows: list[Mapping[str, Any]], side: str, window_seconds: int,
                     opened_at: datetime, bucket_seconds: int) -> dict[str, Any]:
    cutoff = opened_at.timestamp() - window_seconds
    selected = [
        row for row in rows
        if row["bucket_start"].timestamp() >= cutoff and row["bucket_start"] < opened_at
    ]
    expected = max(1, window_seconds // bucket_seconds)
    side_sign = _side_sign(side)
    signed_flow = sum(_f(row.get("signed_quote_flow")) for row in selected)
    total_volume = sum(_f(row.get("total_quote_volume")) for row in selected)
    flow_ratio = signed_flow / total_volume if total_volume > 0 else 0.0

    imbalance10 = [_f(row.get("imbalance_10")) for row in selected if row.get("imbalance_10") is not None]
    imbalance50 = [_f(row.get("imbalance_50")) for row in selected if row.get("imbalance_50") is not None]
    spreads = [_f(row.get("spread_bps")) for row in selected if row.get("spread_bps") is not None]
    microprice_displacements = []
    for row in selected:
        mid = _f(row.get("mid"))
        microprice = _f(row.get("microprice"))
        if mid > 0 and microprice > 0:
            microprice_displacements.append((microprice - mid) / mid * 10_000.0)

    bid_added = sum(_f(row.get("bid_added_quote")) for row in selected)
    bid_removed = sum(_f(row.get("bid_removed_quote")) for row in selected)
    ask_added = sum(_f(row.get("ask_added_quote")) for row in selected)
    ask_removed = sum(_f(row.get("ask_removed_quote")) for row in selected)
    raw_book_pressure = bid_added + ask_removed - bid_removed - ask_added
    book_churn = bid_added + bid_removed + ask_added + ask_removed
    book_pressure_ratio = raw_book_pressure / book_churn if book_churn > 0 else 0.0

    imbalance10_mean = _mean(imbalance10) or 0.0
    imbalance50_mean = _mean(imbalance50) or 0.0
    microprice_mean = _mean(microprice_displacements) or 0.0
    side_flow_ratio = side_sign * flow_ratio
    side_imbalance10 = side_sign * imbalance10_mean

    prefix = f"{window_seconds}s"
    return {
        f"bucket_count_{prefix}": len(selected),
        f"coverage_ratio_{prefix}": min(1.0, len(selected) / expected),
        f"book_ready_ratio_{prefix}": (
            sum(1 for row in selected if bool(row.get("book_ready"))) / len(selected)
            if selected else 0.0
        ),
        f"book_message_count_{prefix}": sum(int(row.get("book_message_count") or 0) for row in selected),
        f"signed_quote_flow_{prefix}": signed_flow,
        f"total_quote_volume_{prefix}": total_volume,
        f"flow_ratio_{prefix}": flow_ratio,
        f"side_flow_ratio_{prefix}": side_flow_ratio,
        f"imbalance_10_mean_{prefix}": imbalance10_mean,
        f"imbalance_50_mean_{prefix}": imbalance50_mean,
        f"side_imbalance_10_mean_{prefix}": side_imbalance10,
        f"spread_bps_mean_{prefix}": _mean(spreads),
        f"microprice_displacement_bps_mean_{prefix}": microprice_mean,
        f"side_microprice_displacement_bps_{prefix}": side_sign * microprice_mean,
        f"bid_added_quote_{prefix}": bid_added,
        f"bid_removed_quote_{prefix}": bid_removed,
        f"ask_added_quote_{prefix}": ask_added,
        f"ask_removed_quote_{prefix}": ask_removed,
        f"book_pressure_ratio_{prefix}": book_pressure_ratio,
        f"side_book_pressure_ratio_{prefix}": side_sign * book_pressure_ratio,
        f"flow_book_concordance_{prefix}": side_flow_ratio * side_imbalance10,
    }


def build_feature_rows(rows: Iterable[Mapping[str, Any]], bucket_seconds: int = 5) -> list[dict[str, Any]]:
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["signal_id"])].append(row)

    output: list[dict[str, Any]] = []
    for signal_id, signal_rows in sorted(grouped.items()):
        first = signal_rows[0]
        opened_at = first["opened_at"]
        side = str(first["side"])
        feature = {
            "signal_id": signal_id,
            "signal_key": first["signal_key"],
            "strategy_version": first["strategy_version"],
            "signal_class": first["signal_class"],
            "symbol": first["symbol"],
            "side": side,
            "opened_at": opened_at.isoformat(),
            "setup_type": first["setup_type"],
            "feature_cutoff_at": opened_at.isoformat(),
            "spec_version": SPEC_VERSION,
            "label_blind": True,
        }
        for window_seconds in WINDOW_SECONDS:
            feature.update(_window_features(signal_rows, side, window_seconds, opened_at, bucket_seconds))
        output.append(feature)
    return output


def sample_readiness(features: Iterable[Mapping[str, Any]], symbols: Iterable[str]) -> dict[str, Any]:
    rows = list(features)
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    counts = {symbol: 0 for symbol in wanted}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol in counts:
            counts[symbol] += 1
    reasons: list[str] = []
    if len(rows) < MIN_SIGNAL_SAMPLE_TOTAL:
        reasons.append("insufficient_total_signals")
    if any(counts[symbol] < MIN_SIGNAL_SAMPLE_PER_SYMBOL for symbol in wanted):
        reasons.append("insufficient_per_symbol_signals")
    return {
        "ready_for_preregistered_effect_test": not reasons,
        "reasons": reasons,
        "total_signals": len(rows),
        "per_symbol": counts,
        "minimum_total": MIN_SIGNAL_SAMPLE_TOTAL,
        "minimum_per_symbol": MIN_SIGNAL_SAMPLE_PER_SYMBOL,
    }


async def load_feature_rows(database_url: str, symbols: Iterable[str], since: datetime,
                            until: datetime, bucket_seconds: int = 5) -> list[dict[str, Any]]:
    """Load label-blind, strictly pre-signal feature rows for the fixed v0.7.3 study cohort."""
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    wanted = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(
            ALIGNMENT_SQL,
            list(wanted),
            since,
            until,
            PREREGISTERED_STRATEGY_VERSION,
        )
    finally:
        await connection.close()
    return build_feature_rows(rows, bucket_seconds=bucket_seconds)
