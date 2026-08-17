from __future__ import annotations

from typing import Any

from app.models import ScanResponse, TopCandidatesResponse


CANDIDATE_SECTIONS = (
    "strict_longs",
    "strict_shorts",
    "watch_only_longs",
    "watch_only_shorts",
)
DERIVATIVE_VALUE_FIELDS = (
    "open_interest_usd",
    "oi_change_1h_pct",
    "oi_change_4h_pct",
    "oi_change_24h_pct",
    "funding_rate",
    "long_liquidations_24h_usd",
    "short_liquidations_24h_usd",
)


def _derivatives_status(payload: dict[str, Any]) -> tuple[str, str]:
    if not payload:
        return "UNAVAILABLE", "No Coinalyze derivatives payload is cached for this candidate."

    reasons: list[str] = []
    endpoint_errors = payload.get("endpoint_errors")
    if endpoint_errors:
        labels = sorted({str(value).split(":", 1)[0] for value in endpoint_errors})
        reasons.append("Coinalyze endpoint failures: " + ", ".join(labels) + ".")

    availability = payload.get("availability")
    if isinstance(availability, dict) and availability:
        missing_endpoints = sorted(
            str(key) for key, value in availability.items() if not bool(value)
        )
        if missing_endpoints:
            reasons.append(
                "Unavailable Coinalyze coverage: " + ", ".join(missing_endpoints) + "."
            )

    missing_fields = [
        field for field in DERIVATIVE_VALUE_FIELDS
        if payload.get(field) is None
    ]
    if missing_fields:
        reasons.append("Missing derivatives fields: " + ", ".join(missing_fields) + ".")

    if reasons:
        return "PARTIAL", " ".join(reasons)
    return "GOOD", "All requested Coinalyze derivatives endpoints and fields are available for this candidate."


def attach_swing_candidate_derivatives(
    compact: TopCandidatesResponse,
    scan_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach context-only Coinalyze data to compact swing candidates.

    The compact ranking itself is produced first by the existing repository logic.
    This helper only joins already-cached ``Setup.metrics.derivatives`` by
    (symbol, side). It never recomputes or mutates scores, category, eligibility,
    tradeability, shortability, decision, trigger, stop, target, or RR.
    """
    result = compact.model_dump(mode="json")
    if not scan_payload:
        return result

    scan = ScanResponse.model_validate(scan_payload)
    setups = [
        *scan.longs,
        *scan.shorts,
        *scan.extended_watchlist,
        *scan.liquidity_blocked,
    ]

    by_key: dict[tuple[str, str], Any] = {}
    for setup in setups:
        key = (setup.symbol, setup.side)
        existing = by_key.get(key)
        current_derivatives = (setup.metrics or {}).get("derivatives") or {}
        if existing is None:
            by_key[key] = setup
            continue
        existing_derivatives = (existing.metrics or {}).get("derivatives") or {}
        if not existing_derivatives and current_derivatives:
            by_key[key] = setup

    for section in CANDIDATE_SECTIONS:
        for item in result.get(section, []):
            setup = by_key.get((item.get("symbol"), item.get("side")))
            derivatives = (
                ((setup.metrics or {}).get("derivatives") or {})
                if setup is not None
                else {}
            )
            status, reason = _derivatives_status(derivatives)
            if not derivatives and setup is not None:
                relevant = [
                    str(value) for value in (setup.missing_data or [])
                    if "Coinalyze" in str(value) or "derivative" in str(value).lower()
                ]
                if relevant:
                    reason = "; ".join(relevant)
            item["derivatives"] = derivatives
            item["derivatives_status"] = status
            item["derivatives_status_reason"] = reason
            item["derivatives_data_as_of"] = (
                setup.data_as_of.isoformat() if setup is not None and derivatives else None
            )
            item["derivatives_context_only"] = True

    notes = result.setdefault("notes", [])
    note = (
        "Candidate derivatives are context-only Coinalyze data; they do not modify "
        "swing scores, eligibility, tradeability, or execution proof."
    )
    if note not in notes:
        notes.append(note)
    return result
