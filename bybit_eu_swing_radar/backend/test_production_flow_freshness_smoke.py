from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

import scripts.production_flow_freshness_smoke as smoke

SHA = "a" * 40
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def status(batch="batch-a", sha="old", **overrides):
    value = {"data_as_of": NOW.isoformat(), "flow_batch_id": batch, "source_commit_sha": sha,
             "processed": 3, "good": 1, "partial": 1, "no_derivative_match": 1}
    value.update(overrides)
    return value


def context(batch="batch-b", sha=SHA, age=1, **overrides):
    value = {"data_as_of": (NOW - timedelta(seconds=age)).isoformat(), "flow_batch_id": batch,
             "source_commit_sha": sha, "data_quality": "GOOD", "coverage_status": "GOOD"}
    value.update(overrides)
    return value


def final_payloads(batch="batch-b"):
    return [status(batch, SHA), context(batch), context(batch), context(batch)]


def scripted_fetch(responses):
    calls = []
    def fetch(url, api_key, timeout):
        calls.append(url)
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response
    return fetch, calls


def test_waits_for_version_404_before_reading_baseline():
    responses = [HTTPError("u", 404, "not found", {}, None), {"commit_sha": SHA},
                 status("batch-b", SHA), *final_payloads()]
    fetch, calls = scripted_fetch(responses)
    sleeps = []
    result = smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=sleeps.append)
    assert result == 0
    assert sleeps == [smoke.POLL_INTERVAL_SECONDS]
    assert calls[0].endswith("/version")
    assert calls[1].endswith("/version")
    assert calls[2].endswith(smoke.STATUS_PATH)


def test_waits_for_transient_version_timeout_before_reading_baseline():
    responses = [TimeoutError(), {"commit_sha": SHA}, status("batch-b", SHA), *final_payloads()]
    fetch, calls = scripted_fetch(responses)
    sleeps = []
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=sleeps.append) == 0
    assert sleeps == [smoke.POLL_INTERVAL_SECONDS]
    assert calls[:3] == ["https://prod/version", "https://prod/version", f"https://prod{smoke.STATUS_PATH}"]


def test_baseline_from_expected_commit_avoids_waiting_for_another_worker_batch():
    responses = [{"commit_sha": SHA}, status("batch-b", SHA), *final_payloads()]
    fetch, calls = scripted_fetch(responses)
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=lambda _: None) == 0
    assert len(calls) == 6


def test_old_baseline_requires_distinct_expected_worker_batch():
    responses = [{"commit_sha": SHA}, status(), status(), status("batch-b", SHA), *final_payloads()]
    fetch, _ = scripted_fetch(responses)
    sleeps = []
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=sleeps.append) == 0
    assert sleeps == [smoke.POLL_INTERVAL_SECONDS]


@pytest.mark.parametrize("code", [401, 403, 429])
def test_version_auth_and_rate_limit_fail_immediately(code):
    responses = [HTTPError("u", code, "error", {}, None)]
    fetch, calls = scripted_fetch(responses)
    sleeps = []
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=sleeps.append) == 1
    assert len(calls) == 1
    assert sleeps == []


def test_unexpected_version_http_error_fails_immediately():
    fetch, calls = scripted_fetch([HTTPError("u", 500, "error", {}, None)])
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=lambda _: None) == 1
    assert len(calls) == 1


def test_version_polling_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(smoke, "MAX_POLLS", 3)
    fetch, calls = scripted_fetch([{"commit_sha": "old"}] * 3)
    sleeps = []
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=sleeps.append) == 1
    assert len(calls) == 3
    assert sleeps == [smoke.POLL_INTERVAL_SECONDS] * 2


def test_worker_polling_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(smoke, "MAX_POLLS", 3)
    fetch, calls = scripted_fetch([{"commit_sha": SHA}, status()] + [status()] * 3)
    sleeps = []
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=sleeps.append) == 1
    assert len(calls) == 5
    assert sleeps == [smoke.POLL_INTERVAL_SECONDS] * 2


def test_missing_baseline_batch_fails_closed_after_deployment_verification():
    fetch, calls = scripted_fetch([{"commit_sha": SHA}, status(batch=None)])
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch) == 1
    assert len(calls) == 2


def test_final_payloads_require_same_commit_batch_and_invariants():
    bad = final_payloads()
    bad[0] = status("batch-b", SHA, processed=4)
    bad[1] = context("other")
    bad[2] = context(sha="wrong")
    fetch, _ = scripted_fetch([{"commit_sha": SHA}, status("batch-b", SHA), *bad])
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=lambda _: None) == 1


def test_stale_good_context_fails():
    bad = final_payloads()
    bad[1] = context("batch-b", age=301)
    fetch, _ = scripted_fetch([{"commit_sha": SHA}, status("batch-b", SHA), *bad])
    assert smoke.run_smoke("https://prod", "secret", SHA, fetch=fetch, sleep=lambda _: None) == 1


def test_main_requires_all_configuration(monkeypatch):
    monkeypatch.delenv("PRODUCTION_RADAR_API_BASE_URL", raising=False)
    monkeypatch.delenv("PRODUCTION_RADAR_API_KEY", raising=False)
    monkeypatch.delenv("EXPECTED_SHA", raising=False)
    assert smoke.main() == 1
