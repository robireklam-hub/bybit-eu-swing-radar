"""Frozen, outcome-blind data-quality contract for swing liquidity validation.

Research only. This gate evaluates capture integrity and contemporaneous order-book
coverage. It never reads outcomes, tunes thresholds, or mutates live strategy,
eligibility, scoring, ranking, shortability, or execution.
"""
from __future__ import annotations

from typing import Any, Iterable

from research.research_governance import PIT_VERSION
from research.research_lifecycle_ledger import canonical_fingerprint

DATA_QUALITY_SPEC_VERSION = "swing-liquidity-data-quality-v1"
MIN_CONSECUTIVE_CAPTURES = 3
MAX_ORDERBOOK_ERRORS_PER_CAPTURE = 0
REQUIRE_FULL_ORDERBOOK_COVERAGE = True
REQUIRE_POSITIVE_CANDIDATE_COUNT = True


def spec() -> dict[str, Any]:
    return {
        "spec_version": DATA_QUALITY_SPEC_VERSION,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_used": False,
        "threshold_search_allowed": False,
        "min_consecutive_post_pit_captures": MIN_CONSECUTIVE_CAPTURES,
        "required_provenance_version": PIT_VERSION,
        "max_orderbook_errors_per_capture": MAX_ORDERBOOK_ERRORS_PER_CAPTURE,
        "require_full_orderbook_coverage": REQUIRE_FULL_ORDERBOOK_COVERAGE,
        "require_positive_candidate_count": REQUIRE_POSITIVE_CANDIDATE_COUNT,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
    }


def evaluate_capture_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized = [dict(row) for row in rows]
    if len(normalized) < MIN_CONSECUTIVE_CAPTURES:
        return {
            "ready": False,
            "reason": "insufficient_consecutive_post_pit_captures",
            "capture_count": len(normalized),
            "required_capture_count": MIN_CONSECUTIVE_CAPTURES,
            "spec": spec(),
        }

    window = normalized[-MIN_CONSECUTIVE_CAPTURES:]
    failures: list[str] = []
    evidence: list[dict[str, Any]] = []
    for index, row in enumerate(window):
        try:
            candidate_count = int(row.get("candidate_count"))
            orderbook_count = int(row.get("orderbook_count"))
            orderbook_error_count = int(row.get("orderbook_error_count"))
        except (TypeError, ValueError):
            failures.append(f"capture_{index}:invalid_counts")
            continue
        provenance_version = str(row.get("provenance_version") or "")
        if provenance_version != PIT_VERSION:
            failures.append(f"capture_{index}:provenance_version_mismatch")
        if REQUIRE_POSITIVE_CANDIDATE_COUNT and candidate_count <= 0:
            failures.append(f"capture_{index}:candidate_count_not_positive")
        if orderbook_error_count > MAX_ORDERBOOK_ERRORS_PER_CAPTURE:
            failures.append(f"capture_{index}:orderbook_errors_present")
        if REQUIRE_FULL_ORDERBOOK_COVERAGE and orderbook_count != candidate_count:
            failures.append(f"capture_{index}:orderbook_coverage_incomplete")
        if not row.get("feature_available_at"):
            failures.append(f"capture_{index}:feature_available_at_missing")
        evidence.append(
            {
                "captured_at": str(row.get("captured_at")),
                "inserted_at": str(row.get("inserted_at")),
                "candidate_count": candidate_count,
                "orderbook_count": orderbook_count,
                "orderbook_error_count": orderbook_error_count,
                "feature_available_at": str(row.get("feature_available_at")),
                "provenance_version": provenance_version,
                "source_commit_sha": row.get("source_commit_sha"),
            }
        )

    if failures:
        return {
            "ready": False,
            "reason": "data_quality_gate_not_satisfied",
            "capture_count": len(window),
            "required_capture_count": MIN_CONSECUTIVE_CAPTURES,
            "failures": failures,
            "evidence": evidence,
            "spec": spec(),
        }

    evidence_fingerprints = [canonical_fingerprint(item) for item in evidence]
    summary = {
        "ready": True,
        "reason": "data_quality_gate_satisfied",
        "capture_count": len(window),
        "required_capture_count": MIN_CONSECUTIVE_CAPTURES,
        "evidence": evidence,
        "evidence_fingerprints": evidence_fingerprints,
        "spec": spec(),
    }
    summary["evidence_window_fingerprint"] = canonical_fingerprint(
        {
            "spec_version": DATA_QUALITY_SPEC_VERSION,
            "evidence_fingerprints": evidence_fingerprints,
        }
    )
    return summary
