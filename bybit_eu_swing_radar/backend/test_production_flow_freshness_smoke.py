from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from scripts.production_flow_freshness_smoke import (
    FINAL_PATHS, ImmediateHttpFailure, run_final_smoke, wait_for_commit_gate,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def status(at=None, **updates):
    value = {"data_as_of": (at or NOW).isoformat(), "flow_batch_id": "batch-new",
             "source_commit_sha": SHA, "processed": 3, "good": 1,
             "partial": 1, "no_derivative_match": 1}
    value.update(updates)
    return value


def context(age=1, **updates):
    value = {"data_as_of": (NOW - timedelta(seconds=age)).isoformat(),
             "flow_batch_id": "batch-new", "source_commit_sha": SHA,
             "data_quality": "GOOD", "coverage_status": "GOOD"}
    value.update(updates)
    return value


def test_commit_gate_requires_api_then_batch_transition():
    calls = []
    responses = [
        {"commit_sha": SHA},
        status(NOW, flow_batch_id="batch-old"),
        status(NOW - timedelta(days=1), flow_batch_id="batch-new"),
    ]
    def fetch(url, key, timeout):
        calls.append(url)
        return responses.pop(0)
    result = wait_for_commit_gate("https://api", "key", SHA, fetch=fetch,
                                  sleep=lambda _: None, max_rounds=2)
    assert result == "batch-new"
    assert calls == ["https://api/version", "https://api/v1/day-trade/flow/status",
                     "https://api/v1/day-trade/flow/status"]


def test_gate_missing_baseline_or_candidate_metadata_fails_closed():
    cases = [
        [{"commit_sha": "wrong"}],
        [{"commit_sha": SHA}, status(flow_batch_id="")],
        [{"commit_sha": SHA}, status(flow_batch_id="batch-old"),
         status(flow_batch_id="", source_commit_sha=SHA)],
        [{"commit_sha": SHA}, status(flow_batch_id="batch-old"),
         status(flow_batch_id="batch-new", source_commit_sha="wrong")],
    ]
    for responses in cases:
        def fetch(url, key, timeout, rows=list(responses)):
            return rows.pop(0) if rows else {"commit_sha": "wrong"}
        assert wait_for_commit_gate("https://api", "key", SHA, fetch=fetch,
                                    sleep=lambda _: None, max_rounds=len(responses)) is None


def test_same_batch_never_passes_even_with_later_timestamp():
    rows = [
        {"commit_sha": SHA},
        status(NOW, flow_batch_id="same"),
        status(NOW + timedelta(days=1), flow_batch_id="same"),
    ]
    assert wait_for_commit_gate(
        "https://api", "key", SHA,
        fetch=lambda *args: rows.pop(0), sleep=lambda _: None, max_rounds=2,
    ) is None


def test_401_403_429_fail_immediately_without_retry():
    for code in (401, 403, 429):
        calls = []
        def fetch(url, key, timeout, code=code):
            calls.append(url)
            raise ImmediateHttpFailure(f"HTTP {code}")
        try:
            wait_for_commit_gate("https://api", "secret", SHA, fetch=fetch,
                                 sleep=lambda _: None, max_rounds=20)
            assert False
        except ImmediateHttpFailure:
            pass
        assert len(calls) == 1


def test_gate_is_bounded_to_20_rounds_and_30_second_sleeps():
    calls, sleeps = [], []
    result = wait_for_commit_gate("https://api", "key", SHA,
        fetch=lambda *args: calls.append(args[0]) or {"commit_sha": "wrong"},
        sleep=sleeps.append, max_rounds=20)
    assert result is None and len(calls) == 20 and sleeps == [30] * 20


def test_final_smoke_exactly_four_gets_no_retry_and_consistent_metadata(capsys):
    rows = [status(), context(), context(), context()]
    calls = []
    def fetch(url, key, timeout):
        calls.append(url)
        return rows.pop(0)
    assert run_final_smoke("https://api", "secret", SHA, "batch-new", fetch=fetch) == 0
    assert calls == [f"https://api{path}" for _, path in FINAL_PATHS]
    assert "DEPLOYMENT VERIFIED, WORKER EXECUTION VERIFIED." in capsys.readouterr().out


def test_final_batch_commit_mismatch_and_request_error_fail_without_retry():
    for bad in (context(flow_batch_id="other"), context(source_commit_sha="other")):
        rows = [status(), bad, context(), context()]
        calls = []
        assert run_final_smoke("https://api", "key", SHA, "batch-new",
            fetch=lambda *args: calls.append(args[0]) or rows.pop(0)) == 1
        assert len(calls) == 4
    calls = []
    def failing(url, key, timeout):
        calls.append(url)
        if len(calls) == 2:
            raise URLError("secret")
        return status() if len(calls) == 1 else context()
    assert run_final_smoke("https://api", "secret", SHA, "batch-new", fetch=failing) == 1
    assert len(calls) == 4
