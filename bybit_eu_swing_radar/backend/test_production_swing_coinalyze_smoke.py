from __future__ import annotations

from scripts.production_swing_coinalyze_smoke import evaluate, run_smoke


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
            "coinalyze_priority_missing_symbols": [],
            "coinalyze_priority_full_target_coverage": True,
        }
    }


def test_evaluate_requires_exact_priority_target_coverage_and_status_reason():
    assert evaluate(top_payload(), status_payload(), "abc") == []

    bad = status_payload()
    bad["worker"]["coinalyze_priority_targeted_symbols"] = []
    assert any("not all compact priority candidates targeted" in value for value in evaluate(top_payload(), bad, "abc"))

    top = top_payload()
    top["strict_longs"][0]["derivatives_status_reason"] = ""
    assert any("missing derivatives_status_reason" in value for value in evaluate(top, status_payload(), "abc"))


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
