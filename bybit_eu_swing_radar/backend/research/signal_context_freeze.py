"""Research-only signal-time context freeze v1.

Freezes already-persisted, strictly pre-signal context for prospective day-trade
journal signals. This module never reads trade outcomes and never changes live
strategy, scoring, eligibility, execution, entries, stops, or targets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SPEC_VERSION = "signal-context-freeze-v1"
STRATEGY_VERSION = "0.7.3"
CROSS_LAYER_SPEC_VERSION = "cross-layer-context-shadow-v1"
MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION = "microstructure-forward-alignment-v1"
CROSS_LAYER_MAX_AGE_SECONDS = 2 * 3600
MIN_SAMPLE_TOTAL = 60
MIN_SAMPLE_PER_SIDE = 10
MIN_DISTINCT_UTC_DAYS = 10
MIN_CROSS_LAYER_COVERAGE_PCT = 95.0


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "execution_proof": False,
        "source_layers": {
            "cross_layer_context": CROSS_LAYER_SPEC_VERSION,
            "microstructure_alignment": MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
        },
        "temporal_contract": {
            "cross_layer_rule": "latest persisted cross-layer snapshot with captured_at <= signal opened_at",
            "cross_layer_max_age_seconds": CROSS_LAYER_MAX_AGE_SECONDS,
            "microstructure_rule": "existing preregistered alignment features use buckets with bucket_start < signal opened_at only",
            "freeze_may_run_after_signal": True,
            "source_payloads_are_copied_immutably_into_freeze_rows": True,
        },
        "future_effect_gate": {
            "minimum_total_signals": MIN_SAMPLE_TOTAL,
            "minimum_per_side": MIN_SAMPLE_PER_SIDE,
            "minimum_distinct_utc_days": MIN_DISTINCT_UTC_DAYS,
            "minimum_cross_layer_coverage_pct": MIN_CROSS_LAYER_COVERAGE_PCT,
            "microstructure_is_optional_and_only_expected_for_recorder_symbols": True,
        },
        "principles": [
            "no outcome/status/net-R/exit fields are selected from the journal",
            "future cross-layer snapshots are rejected rather than treated as neutral",
            "stale or missing context remains explicit",
            "microstructure is optional and strictly pre-signal",
            "no composite score or trade recommendation is emitted",
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
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _symbol_context(payload: Mapping[str, Any], symbol: str) -> dict[str, Any] | None:
    raw = payload.get("symbols")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping) and str(item.get("symbol") or "").upper() == symbol:
                return dict(item)
    elif isinstance(raw, Mapping):
        item = raw.get(symbol)
        if isinstance(item, Mapping):
            return dict(item)
    return None


def cross_layer_context(
    record: Mapping[str, Any] | None,
    *,
    opened_at: datetime,
    symbol: str,
) -> dict[str, Any]:
    if not record:
        return {
            "status": "MISSING",
            "captured_at": None,
            "age_seconds": None,
            "source_commit_sha": None,
            "data_quality": None,
            "symbol_context": None,
            "global_context": None,
        }
    captured_at = _dt(record.get("captured_at"))
    if captured_at is None:
        return {
            "status": "INVALID_TIMESTAMP",
            "captured_at": record.get("captured_at"),
            "age_seconds": None,
            "source_commit_sha": record.get("source_commit_sha"),
            "data_quality": None,
            "symbol_context": None,
            "global_context": None,
        }
    age = (opened_at - captured_at).total_seconds()
    if age < -1e-6:
        return {
            "status": "FUTURE_REJECTED",
            "captured_at": captured_at.isoformat(),
            "age_seconds": round(age, 3),
            "source_commit_sha": record.get("source_commit_sha"),
            "data_quality": None,
            "symbol_context": None,
            "global_context": None,
        }
    status = "FRESH" if age <= CROSS_LAYER_MAX_AGE_SECONDS else "STALE"
    payload = record.get("payload")
    payload = dict(payload) if isinstance(payload, Mapping) else {}
    return {
        "status": status,
        "captured_at": captured_at.isoformat(),
        "age_seconds": round(age, 3),
        "source_commit_sha": record.get("source_commit_sha"),
        "data_quality": payload.get("data_quality"),
        "layer_status": dict(payload.get("layers") or {}),
        "symbol_context": _symbol_context(payload, symbol),
        "global_context": dict(payload.get("global_context") or {}),
    }


def microstructure_context(
    feature: Mapping[str, Any] | None,
    *,
    opened_at: datetime,
    symbol: str,
    recorder_symbols: Iterable[str],
) -> dict[str, Any]:
    tracked = {str(item).upper() for item in recorder_symbols}
    if symbol not in tracked:
        return {
            "status": "NOT_TRACKED",
            "alignment_spec_version": MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
            "feature_cutoff_at": None,
            "features": None,
        }
    if not feature:
        return {
            "status": "NO_PRE_SIGNAL_BUCKETS",
            "alignment_spec_version": MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
            "feature_cutoff_at": opened_at.isoformat(),
            "features": None,
        }
    cutoff = _dt(feature.get("feature_cutoff_at"))
    if cutoff is None or cutoff > opened_at:
        return {
            "status": "TEMPORAL_REJECTED",
            "alignment_spec_version": MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
            "feature_cutoff_at": feature.get("feature_cutoff_at"),
            "features": None,
        }
    if feature.get("label_blind") is not True:
        return {
            "status": "CONTRACT_REJECTED",
            "alignment_spec_version": MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
            "feature_cutoff_at": cutoff.isoformat(),
            "features": None,
        }
    if str(feature.get("spec_version") or "") != MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION:
        return {
            "status": "CONTRACT_REJECTED",
            "alignment_spec_version": MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
            "feature_cutoff_at": cutoff.isoformat(),
            "features": None,
        }
    excluded = {
        "signal_id",
        "signal_key",
        "strategy_version",
        "signal_class",
        "symbol",
        "side",
        "opened_at",
        "setup_type",
        "spec_version",
        "label_blind",
    }
    features = {key: value for key, value in feature.items() if key not in excluded}
    return {
        "status": "ALIGNED",
        "alignment_spec_version": MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
        "feature_cutoff_at": cutoff.isoformat(),
        "features": features,
    }


def build_freeze_payload(
    signal: Mapping[str, Any],
    *,
    cross_layer_record: Mapping[str, Any] | None,
    microstructure_feature: Mapping[str, Any] | None,
    recorder_symbols: Iterable[str],
    frozen_at: datetime | None = None,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    opened_at = _dt(signal.get("opened_at"))
    if opened_at is None:
        raise ValueError("signal opened_at must be timezone-aware/parseable")
    symbol = str(signal.get("symbol") or "").upper()
    if not symbol.endswith("USDC"):
        raise ValueError("signal context freeze is USDC-only")
    side = str(signal.get("side") or "")
    if side not in {"long", "short"}:
        raise ValueError("signal side must be long or short")
    now = (frozen_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cross = cross_layer_context(cross_layer_record, opened_at=opened_at, symbol=symbol)
    micro = microstructure_context(
        microstructure_feature,
        opened_at=opened_at,
        symbol=symbol,
        recorder_symbols=recorder_symbols,
    )
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": str(signal.get("strategy_version") or STRATEGY_VERSION),
        "signal_id": int(signal.get("id") or signal.get("signal_id") or 0),
        "signal_key": str(signal.get("signal_key") or ""),
        "signal_class": str(signal.get("signal_class") or ""),
        "symbol": symbol,
        "side": side,
        "setup_type": str(signal.get("setup_type") or ""),
        "opened_at": opened_at.isoformat(),
        "frozen_at": now.isoformat(),
        "freeze_delay_seconds": round(max(0.0, (now - opened_at).total_seconds()), 3),
        "source_commit_sha": source_commit_sha,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "execution_proof": False,
        "cross_layer_context": cross,
        "microstructure": micro,
        "composite_score_emitted": False,
    }


def sample_gate(counts: Mapping[str, Any]) -> dict[str, Any]:
    total = int(counts.get("total") or 0)
    long_count = int(counts.get("long_count") or 0)
    short_count = int(counts.get("short_count") or 0)
    days = int(counts.get("distinct_utc_days") or 0)
    cross_covered = int(counts.get("cross_layer_covered") or 0)
    coverage_pct = (cross_covered / total * 100.0) if total else 0.0
    reasons: list[str] = []
    if total < MIN_SAMPLE_TOTAL:
        reasons.append("insufficient_total_signals")
    if long_count < MIN_SAMPLE_PER_SIDE:
        reasons.append("insufficient_long_signals")
    if short_count < MIN_SAMPLE_PER_SIDE:
        reasons.append("insufficient_short_signals")
    if days < MIN_DISTINCT_UTC_DAYS:
        reasons.append("insufficient_distinct_utc_days")
    if coverage_pct + 1e-9 < MIN_CROSS_LAYER_COVERAGE_PCT:
        reasons.append("insufficient_cross_layer_coverage")
    return {
        "ready_for_future_effect_test": not reasons,
        "reasons": reasons,
        "total": total,
        "long_count": long_count,
        "short_count": short_count,
        "distinct_utc_days": days,
        "cross_layer_coverage_pct": round(coverage_pct, 3),
        "minimum_total": MIN_SAMPLE_TOTAL,
        "minimum_per_side": MIN_SAMPLE_PER_SIDE,
        "minimum_distinct_utc_days": MIN_DISTINCT_UTC_DAYS,
        "minimum_cross_layer_coverage_pct": MIN_CROSS_LAYER_COVERAGE_PCT,
    }
