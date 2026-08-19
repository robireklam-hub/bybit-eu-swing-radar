"""Unified data-quality contracts for research-only source snapshots.

The contract layer standardizes freshness, completeness, lineage and severity
semantics without changing live strategy, scoring, eligibility or execution.
Raw capture time is never promoted to provider-availability time implicitly.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

CONTRACT_VERSION = "research-data-quality-contract-v1"
SEVERITIES = ("INFO", "WARNING", "RESEARCH_BLOCK", "PRODUCTION_BLOCK")
CORE_REQUIRED_FIELDS = (
    "captured_at",
    "research_only",
    "live_strategy_mutated",
    "promotion_allowed",
)

# These budgets are the existing Cross-Layer v2 freshness budgets, moved into
# one declarative source contract rather than reimplemented by each consumer.
_SOURCE_CONTRACTS: dict[str, dict[str, Any]] = {
    "market_regime": {
        "research_family": "market-regime",
        "spec_version": "market-regime-shadow-v1",
        "max_age_seconds": 3 * 3600,
        "coverage_semantics": "fresh snapshot plus explicit symbol-map coverage",
    },
    "derivatives_positioning": {
        "research_family": "derivatives-positioning",
        "spec_version": "derivatives-positioning-shadow-v1",
        "max_age_seconds": 3 * 3600,
        "coverage_semantics": "fresh snapshot plus explicit per-symbol flow/regime/liquidation coverage",
    },
    "relative_strength": {
        "research_family": "relative-strength",
        "spec_version": "relative-strength-shadow-v1",
        "max_age_seconds": 36 * 3600,
        "coverage_semantics": "fresh completed-daily snapshot over the observed USDC universe",
    },
    "sector_rotation": {
        "research_family": "sector-rotation",
        "spec_version": "sector-rotation-shadow-v1",
        "max_age_seconds": 36 * 3600,
        "coverage_semantics": "fresh completed-daily snapshot with explicit taxonomy resolution coverage",
    },
    "event_tokenomics": {
        "research_family": "event-tokenomics",
        "spec_version": "event-tokenomics-shadow-v1",
        "max_age_seconds": 8 * 3600,
        "coverage_semantics": "fresh event snapshot with missing/estimated event metadata kept explicit",
    },
    "btc_macro_cycle_etf": {
        "research_family": "btc-macro-cycle-etf",
        "spec_version": "btc-macro-cycle-etf-shadow-v1",
        "max_age_seconds": 8 * 3600,
        "coverage_semantics": "fresh macro/cycle/ETF context with provider-specific source status retained",
    },
    "btc_onchain": {
        "research_family": "btc-onchain",
        "spec_version": "btc-onchain-context-shadow-v1",
        "max_age_seconds": 8 * 3600,
        "coverage_semantics": "fresh on-chain snapshot with per-provider and per-metric availability retained",
    },
    "eth_onchain": {
        "research_family": "eth-onchain",
        "spec_version": "eth-onchain-context-shadow-v1",
        "max_age_seconds": 8 * 3600,
        "coverage_semantics": "fresh on-chain snapshot with per-provider and per-metric availability retained",
    },
}

for _name, _contract in _SOURCE_CONTRACTS.items():
    _contract.update({
        "source": _name,
        "contract_version": CONTRACT_VERSION,
        "required_payload_fields": list(CORE_REQUIRED_FIELDS),
        "research_freshness_required": True,
        "provider_availability_inference": "FORBIDDEN_FROM_CAPTURE_TIME_ALONE",
        "production_eligible": False,
        "production_block_reason": (
            "research-only context is not production/execution proof; provider-level availability "
            "must be independently verified before any future promotion"
        ),
    })


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def source_contract(source: str) -> dict[str, Any]:
    try:
        return deepcopy(_SOURCE_CONTRACTS[source])
    except KeyError as exc:
        raise ValueError(f"unregistered research data-quality source: {source}") from exc


def contract_manifest() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "research_only": True,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "provider_availability_inference": "FORBIDDEN_FROM_CAPTURE_TIME_ALONE",
        "severity_levels": list(SEVERITIES),
        "sources": {name: source_contract(name) for name in _SOURCE_CONTRACTS},
    }


def source_max_age_seconds(source: str) -> int:
    return int(source_contract(source)["max_age_seconds"])


def _lineage(payload: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    history = payload.get("immutable_history")
    warnings: list[str] = []
    blocks: list[str] = []
    if not isinstance(history, Mapping):
        warnings.append("immutable raw-history lineage metadata is absent or legacy")
        history = {}

    immutable = history.get("immutable") is True
    if history and not immutable:
        blocks.append("immutable_history.immutable must be true when lineage metadata is present")

    history_family = history.get("research_family")
    history_spec = history.get("spec_version")
    identity_matches = True
    if history_family not in (None, contract["research_family"]):
        identity_matches = False
        blocks.append("immutable-history research_family does not match the source contract")
    if history_spec not in (None, contract["spec_version"]):
        identity_matches = False
        blocks.append("immutable-history spec_version does not match the source contract")

    point_in_time_verified = history.get("point_in_time_verified") is True
    provider_availability_verified = payload.get("provider_availability_verified") is True
    if not provider_availability_verified:
        warnings.append(
            "provider availability time is unverified; captured_at is capture/ingestion evidence only"
        )

    return {
        "immutable_history_present": bool(history),
        "immutable": immutable if history else False,
        "identity_matches_contract": identity_matches,
        "research_family": history_family,
        "spec_version": history_spec,
        "payload_fingerprint": history.get("payload_fingerprint"),
        "point_in_time_verified": point_in_time_verified,
        "provider_availability_verified": provider_availability_verified,
        "availability_semantics": (
            "PROVIDER_AVAILABILITY_VERIFIED"
            if provider_availability_verified
            else "CAPTURE_TIME_ONLY_NOT_PROVIDER_AVAILABILITY"
        ),
    }, warnings, blocks


def evaluate_source_record(
    source: str,
    record: Mapping[str, Any] | None,
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    """Evaluate one source record without mutating or filtering its payload."""
    contract = source_contract(source)
    now = _dt(observed_at)
    if now is None:
        raise ValueError("observed_at must be timezone-aware")

    payload: Mapping[str, Any] = {}
    source_time: datetime | None = None
    age_seconds: float | None = None
    temporal_status = "MISSING"
    warnings: list[str] = []
    blocks: list[str] = []

    if record:
        source_time = _dt(record.get("captured_at"))
        raw_payload = record.get("payload")
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        if source_time is None:
            temporal_status = "INVALID_TIMESTAMP"
            blocks.append("source captured_at is missing, invalid or timezone-naive")
        else:
            age_seconds = (now - source_time).total_seconds()
            if age_seconds < -1e-6:
                temporal_status = "FUTURE_REJECTED"
                blocks.append("source snapshot timestamp is in the future relative to observation time")
            elif age_seconds > int(contract["max_age_seconds"]):
                temporal_status = "STALE"
                blocks.append("source snapshot exceeds the research freshness budget")
            else:
                temporal_status = "FRESH"
    else:
        blocks.append("source snapshot is missing")

    required = list(contract["required_payload_fields"])
    missing_required = [
        field for field in required
        if field not in payload or payload.get(field) is None
    ]
    if missing_required:
        blocks.append("missing required payload fields: " + ", ".join(missing_required))

    if payload:
        if payload.get("research_only") is not True:
            blocks.append("research_only must be true")
        if payload.get("live_strategy_mutated") is not False:
            blocks.append("live_strategy_mutated must be false")
        if payload.get("promotion_allowed") is not False:
            blocks.append("promotion_allowed must be false")

        payload_time = _dt(payload.get("captured_at"))
        if payload.get("captured_at") not in (None, "") and payload_time is None:
            blocks.append("payload captured_at is invalid or timezone-naive")
        elif source_time is not None and payload_time is not None:
            if abs((payload_time - source_time).total_seconds()) > 1e-6:
                blocks.append("payload captured_at does not match persisted source captured_at")

    lineage, lineage_warnings, lineage_blocks = _lineage(payload, contract)
    warnings.extend(lineage_warnings)
    blocks.extend(lineage_blocks)

    present_required = len(required) - len(missing_required)
    research_usable = not blocks and temporal_status == "FRESH"
    if blocks:
        severity = "RESEARCH_BLOCK"
        contract_status = "BLOCK"
    elif warnings:
        severity = "WARNING"
        contract_status = "WARN"
    else:
        severity = "INFO"
        contract_status = "PASS"

    return {
        "contract_version": CONTRACT_VERSION,
        "source": source,
        "research_family": contract["research_family"],
        "spec_version": contract["spec_version"],
        "status": temporal_status,
        "contract_status": contract_status,
        "severity": severity,
        "research_usable": research_usable,
        "production_usable": False,
        "production_severity": "PRODUCTION_BLOCK",
        "production_block_reason": contract["production_block_reason"],
        "captured_at": source_time.isoformat() if source_time is not None else (
            record.get("captured_at") if record else None
        ),
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "max_age_seconds": int(contract["max_age_seconds"]),
        "source_commit_sha": record.get("source_commit_sha") if record else None,
        "coverage_semantics": contract["coverage_semantics"],
        "completeness": {
            "required_fields": required,
            "present_required_fields": present_required,
            "required_field_count": len(required),
            "missing_required_fields": missing_required,
            "required_field_coverage": f"{present_required}/{len(required)}",
        },
        "lineage": lineage,
        "reasons": blocks + warnings,
        "blocking_reasons": blocks,
        "warning_reasons": warnings,
    }


def aggregate_contract_results(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(results.values())
    severity_counts = {severity: 0 for severity in SEVERITIES}
    for row in rows:
        severity = str(row.get("severity") or "RESEARCH_BLOCK")
        if severity in severity_counts:
            severity_counts[severity] += 1

    blocked = [str(row.get("source")) for row in rows if row.get("research_usable") is not True]
    warned = [str(row.get("source")) for row in rows if row.get("severity") == "WARNING"]
    provider_verified = [
        str(row.get("source"))
        for row in rows
        if (row.get("lineage") or {}).get("provider_availability_verified") is True
    ]
    pit_verified = [
        str(row.get("source"))
        for row in rows
        if (row.get("lineage") or {}).get("point_in_time_verified") is True
    ]

    if blocked:
        severity = "RESEARCH_BLOCK"
    elif warned:
        severity = "WARNING"
    else:
        severity = "INFO"
    return {
        "contract_version": CONTRACT_VERSION,
        "severity": severity,
        "research_gate": "BLOCK" if blocked else "PASS",
        "production_gate": "BLOCK",
        "production_severity": "PRODUCTION_BLOCK",
        "source_count": len(rows),
        "research_usable_source_count": len(rows) - len(blocked),
        "blocked_sources": blocked,
        "warning_sources": warned,
        "provider_availability_verified_sources": provider_verified,
        "point_in_time_verified_sources": pit_verified,
        "severity_counts": severity_counts,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "provider_availability_inferred_from_capture_time": False,
    }
