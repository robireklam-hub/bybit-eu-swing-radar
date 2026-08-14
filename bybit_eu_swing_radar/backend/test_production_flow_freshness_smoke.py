from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

import scripts.production_flow_freshness_smoke as smoke


SHA = "a" * 40
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def status(batch="batch-a", sha=SHA, **overrides):
    value = {"data_as_of": NOW.isoformat(), "flow_batch_id": batch,
             "source_commit_sha": sha, "processed": 3, "good": 1,
             "partial": 1, "no_derivative_match": 1}
    value.update(overrides)
    return value


def context(batch="batch-b", sha=SHA, age=1, **overrides):
    value = {"data_as_of": (NOW - timedelta(seconds=age)).isoformat(),
             "flow_batch_id": batch, "source_commit_sha": sha,
             "data_quality": "GOOD", "coverage_status": "GOOD"}
    value.update(overrides)
    return value


def final_payloads(batch="batch-b"):
    return [status(batch), context(batch), context(batch), context(batch)]


def execute(candidates, baseline=None, final=None, error=None):
    responses = [{"commit_sha": SHA}, baseline or status()] + candidates
    responses += final if final is not None else final_payloads()
    calls, sleeps = [], []

    def fetch(url, api_key, timeout):
        calls.append(url)
        if error and len(calls) == error[0]:
            raise HTTPError(url, error[1], "error", {}, None)
        return responses[len(calls) - 1]

    result = smoke.run_smoke("https://production.example", "secret", SHA,
                             fetch=fetch, sleep=sleeps.append)
    return result, calls, sleeps


def test_same_batch_with_later_timestamp_does_not_pass():
    candidates = [status("batch-a", data_as_of=(NOW + timedelta(days=1)).isoformat())] * 20
    result, _, sleeps = execute(candidates)
    assert result == 1
    assert sleeps == [30] * 19


def test_new_batch_and_matching_commit_passes_and_final_smoke_is_exactly_four_gets(capsys):
    result, calls, sleeps = execute([status("batch-b")])
    assert result == 0 and sleeps == []
    assert calls[-4:] == [f"https://production.example{path}" for _, path in smoke.PATHS]
    assert len(calls) == 7
    assert capsys.readouterr().out.endswith(
        "DEPLOYMENT VERIFIED, WORKER EXECUTION VERIFIED.\n")


def test_new_batch_with_wrong_commit_does_not_pass():
    result, _, _ = execute([status("batch-b", "wrong")] * 20)
    assert result == 1


@pytest.mark.parametrize("batch", [None, "", 123])
def test_missing_or_invalid_baseline_batch_fails_closed(batch):
    result, calls, _ = execute([], baseline=status(batch))
    assert result == 1 and len(calls) == 2


def test_missing_candidate_batch_does_not_pass():
    result, _, _ = execute([status(None)] * 20)
    assert result == 1


def test_runner_clock_cannot_affect_batch_decision(monkeypatch):
    class ExplodingDateTime(datetime):
        @classmethod
        def now(cls, *args, **kwargs):
            raise AssertionError("runner clock must not be read")
    monkeypatch.setattr(smoke, "datetime", ExplodingDateTime)
    assert execute([status("batch-b", data_as_of="2000-01-01T00:00:00+00:00")])[0] == 0


def test_polling_timeout_remains_bounded():
    result, calls, sleeps = execute([status()] * 20)
    assert result == 1 and len(calls) == 22
    assert len(sleeps) == 19


@pytest.mark.parametrize("code", [401, 403, 429])
def test_auth_and_rate_limit_errors_stop_immediately(code):
    result, calls, sleeps = execute([status("batch-b")], error=(3, code))
    assert result == 1 and len(calls) == 3 and sleeps == []


def test_version_must_match_expected_deployment_sha():
    calls = []
    def fetch(url, api_key, timeout):
        calls.append(url)
        return {"commit_sha": "wrong"}
    assert smoke.run_smoke("https://production.example", "secret", SHA, fetch=fetch) == 1
    assert len(calls) == 1


def test_main_uses_expected_sha_not_github_sha(monkeypatch):
    captured = {}

    def run_smoke(base_url, api_key, expected_sha):
        captured.update(base_url=base_url, api_key=api_key, expected_sha=expected_sha)
        return 0

    monkeypatch.setenv("PRODUCTION_RADAR_API_BASE_URL", "https://production.example")
    monkeypatch.setenv("PRODUCTION_RADAR_API_KEY", "secret")
    monkeypatch.setenv("EXPECTED_SHA", SHA)
    monkeypatch.setenv("GITHUB_SHA", "wrong")
    monkeypatch.setattr(smoke, "run_smoke", run_smoke)
    assert smoke.main() == 0
    assert captured["expected_sha"] == SHA


@pytest.mark.parametrize("expected_sha", [None, "", "   "])
def test_main_fails_closed_without_expected_sha(monkeypatch, expected_sha):
    monkeypatch.setenv("PRODUCTION_RADAR_API_BASE_URL", "https://production.example")
    monkeypatch.setenv("PRODUCTION_RADAR_API_KEY", "secret")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    if expected_sha is None:
        monkeypatch.delenv("EXPECTED_SHA", raising=False)
    else:
        monkeypatch.setenv("EXPECTED_SHA", expected_sha)
    monkeypatch.setattr(smoke, "run_smoke", lambda *args: pytest.fail("must fail closed"))
    assert smoke.main() == 1


def test_final_payloads_require_same_commit_batch_and_invariants():
    bad = final_payloads()
    bad[1] = context("other")
    bad[2] = context(sha="wrong")
    bad[0] = status("batch-b", processed=4)
    assert execute([status("batch-b")], final=bad)[0] == 1
