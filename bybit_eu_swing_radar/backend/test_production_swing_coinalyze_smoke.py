from __future__ import annotations

from scripts.production_swing_coinalyze_smoke import (
    evaluate,
    market_support_partition,
    run_smoke,
)


def top_payload():
    candidate = {
        "symbol": "AAAUSDC",
        "derivatives_status": "PARTIAL",
        "derivatives_status_reason": "Coinalyze payload is missing: liquidations.",
        "derivatives_context_only": True,
    }
    return {
        "strict_longs": [candidate],
        "strict_shorts": [],
        "watch_only_longs": [],
        "watch_only_shorts": [],
    }


def status_payload(sha="abc"):
    return {
        "worker": {
            "source_commit_sha": sha,
            "coinalyze_priority_symbols": ["AAAUSDC"],
            "coinalyze_priority_targeted_symbols": ["AAAUSDC"],
            "coinalyze_priority_enriched_symbols": ["AAAUSDC"],
            "coinalyze_priority_complete_symbols": [],
            "coinalyze_priority_partial_symbols": ["AAAUSDC"],
            "coinalyze_priority_missing_symbols": [],
            "coinalyze_priority_full_target_coverage": True,
        },
        "sources": [{"source": "Coinalyze", "status": "partial"}],
    }


def test_evaluate_requires_exact_priority_target_coverage_and_status_reason():
    assert evaluate(top_payload(), status_payload(), "abc") == []

    bad = status_payload()
    bad["worker"]["coinalyze_priority_targeted_symbols"] = []
    assert any("not all compact priority candidates targeted" in value for value in evaluate(top_payload(), bad, "abc"))

    top = top_payload()
    top["strict_longs"][0]["derivatives_status_reason"] = ""
    assert any("missing derivatives_status_reason" in value for value in evaluate(top, status_payload(), "abc"))


def test_market_support_partition_distinguishes_no_market_from_endpoint_degradation():
    top = top_payload()
    top["watch_only_longs"] = [
        {
            "symbol": "BBBUSBCC",
            "derivatives_status": "UNAVAILABLE",
            "derivatives_status_reason": "No matching Coinalyze future market.",
            "derivatives_context_only": True,
        },
        {
            "symbol": "CCCUSDC",
            "derivatives_status": "UNAVAILABLE",
            "derivatives_status_reason": "Coinalyze endpoint data unavailable after targeting.",
            "derivatives_context_only": True,
        },
    ]

    supported, unsupported = market_support_partition(top)

    assert supported == ["AAAUSDC", "CCCUSDC"]
    assert unsupported == ["BBBUSBCC"]


def test_evaluate_requires_all_supported_priority_candidates_targeted():
    top = top_payload()
    top["watch_only_longs"] = [
        {
            "symbol": "BBBUSBCC",
            "derivatives_status": "UNAVAILABLE",
            "derivatives_status_reason": "No matching Coinalyze future market.",
            "derivatives_context_only": True,
        }
    ]
    status = status_payload()
    worker = status["worker"]
    worker["coinalyze_priority_symbols"] = ["AAAUSDC", "BBBUSBCC"]
    worker["coinalyze_priority_targeted_symbols"] = ["BBBUSBCC"]
    worker["coinalyze_priority_enriched_symbols"] = ["AAAUSDC"]
    worker["coinalyze_priority_partial_symbols"] = ["AAAUSDC"]
    worker["coinalyze_priority_missing_symbols"] = ["BBBUSBCC"]
    worker["coinalyze_priority_full_target_coverage"] = False

    failures = evaluate(top, status, "abc")

    assert any("Coinalyze-supported priority candidate was not targeted" in value for value in failures)


def test_run_smoke_polls_until_exact_worker_executes():
    responses = iter([
        {"commit_sha": "abc"},
        status_payload("old"),
        top_payload(),
        status_payload("abc"),
        top_payload(),
    ])

    def fetch(url, api_key, timeout):
        return next(responses)

    sleeps = []
    assert run_smoke("https://example.test", "secret", "abc", fetch=fetch, sleep=sleeps.append) == 0
    assert sleeps == [15]


def test_evaluate_rejects_ok_source_health_for_partial_candidate():
    status = status_payload()
    status["sources"][0]["status"] = "ok"
    failures = evaluate(top_payload(), status, "abc")
    assert any("source health is ok" in value for value in failures)
